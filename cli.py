"""
fablegear / cli.py

Single entry point for all toolkit operations.
Run with: python3 cli.py <command> [options]

Commands:
    audit       Read-only library health check
    import      Import audio files into the database
    link        Link imported tracks to existing playlists
    relocate    Batch-update paths for moved/renamed files
    duplicates  Find acoustically identical files via Chromaprint
    process     Detect BPM/key and normalise loudness on audio files
    organize    Consolidate files into a choosable hierarchy (--by label, artist, …)
    rename      Rename files to clean titles based on metadata
    convert     Convert audio files to a target format

All write commands enforce:
  - Rekordbox not running (via write_db())
  - Timestamped backup created before any write (via write_db())
  - sys.exit(1) on unrecoverable error

All module imports are deferred inside command handlers. This means
`python3 cli.py --help` runs instantly without loading pyrekordbox,
mutagen, librosa, etc.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure the repo root and the chop_shop/ tool package are importable whether
# cli.py runs as a subprocess (python cli.py …) or is dispatched from a frozen
# bundle. The command handlers below import duplicate_detector, renamer,
# library_organizer, pruner, novelty_scanner, relocator, db_migrator, and
# renamer_learned by bare name; those modules live in chop_shop/.
_CLI_ROOT = Path(
    os.environ.get("FABLEGEAR_ROOT")
    or getattr(sys, "_MEIPASS", None)
    or Path(__file__).parent.resolve()
)
for _p in (str(_CLI_ROOT), str(_CLI_ROOT / "chop_shop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from FableGear.config import LOCAL_DB, MUSIC_ROOT   # when run as a package
except ImportError:
    try:
        from config import LOCAL_DB, MUSIC_ROOT         # when run as a script
    except RuntimeError:
        LOCAL_DB = None    # type: ignore[assignment]
        MUSIC_ROOT = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

_ARCHIVE = None
_ARCHIVE_ERROR: str | None = None


def _archive():
    """Lazily open the FableGear database so every tool can log to it.

    Failure is LOUD: without the archive, tool runs leave no record in
    fg_processing_log and every cross-tool optimization is lost, so the
    operator must be able to see the disconnect the moment it happens.
    """
    global _ARCHIVE, _ARCHIVE_ERROR
    if _ARCHIVE is None and _ARCHIVE_ERROR is None:
        try:
            from fablegear_database.database import FableGearDatabase  # noqa: PLC0415
            _ARCHIVE = FableGearDatabase()
        except Exception as exc:
            _ARCHIVE_ERROR = f"{type(exc).__name__}: {exc}"
            log.warning("FableGear archive unavailable — tool runs will NOT be recorded: %s", _ARCHIVE_ERROR)
            # Also emit on stdout so SSE-streamed UI logs surface it.
            print(f"[WARN] FableGear archive unavailable — this run will not be recorded ({_ARCHIVE_ERROR})", flush=True)
    return _ARCHIVE


def _require_archive(command_name: str):
    """Return the archive handle or exit — write commands must not run un-journaled.

    ``_archive()`` already warns loudly on failure, but a warning is easy to
    miss in a long SSE-streamed run. A destructive command with no archive
    means no journal row, no undo record, and no cross-tool report — exactly
    the silent-failure class the archive contract exists to prevent, so this
    is a hard stop (exit 2), not a soft warning. Callers exempt dry runs by
    calling ``_archive()`` directly instead when ``dry_run`` is set.
    """
    archive = _archive()
    if archive is None:
        log.error(
            "Archive unavailable for '%s' — this command writes and requires archive logging. "
            "See the warning above for the underlying error.",
            command_name,
        )
        sys.exit(2)
    return archive


def _rekordbox_running() -> bool:
    """True if a Rekordbox process is running — writing to master.db while it is
    open risks corruption, so callers must refuse."""
    import subprocess  # noqa: PLC0415
    try:
        return subprocess.run(["pgrep", "-x", "rekordbox"],
                              capture_output=True).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _guard_or_exit(paths, tool: str) -> None:
    """Refuse system/home/app-data scan roots — same rails as the organizer."""
    from path_guard import guard_sources  # noqa: PLC0415
    try:
        guard_sources(paths, tool)
    except ValueError as exc:
        log.error("%s", exc)
        print(f"[ERROR] {exc}", flush=True)
        sys.exit(2)


# ─── Report helpers ───────────────────────────────────────────────────────────

def _emit_report(text: str, subdir: str, filename: str) -> None:
    """
    Print a report so the UI can capture it, then save it to disk.

    Protocol:
      FABLEGEAR_REPORT_BEGIN — UI starts capturing
      <plain text lines>   — shown in terminal AND in the inline report card
      FABLEGEAR_REPORT_END  — UI stops capturing
      FABLEGEAR_REPORT_PATH: /path — UI stores the saved file path
    """
    print("FABLEGEAR_REPORT_BEGIN", flush=True)
    print(text, flush=True)
    print("FABLEGEAR_REPORT_END", flush=True)
    report_path = _write_report(subdir, filename, text)
    if report_path:
        print(f"FABLEGEAR_REPORT_PATH: {report_path}", flush=True)


def _write_report(subdir: str, filename: str, text: str) -> str | None:
    """
    Write a report text file to REPORTS_DIR/subdir/filename.
    Returns the written path as a string, or None if REPORTS_DIR is unavailable
    (drive not mounted, archive disabled, etc.).
    Failures are logged as warnings — they never abort the command.
    """
    try:
        try:
            from FableGear.config import REPORTS_DIR  # noqa: PLC0415
        except ImportError:
            from config import REPORTS_DIR           # noqa: PLC0415

        out_dir = REPORTS_DIR / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        return str(out_path)
    except Exception as exc:
        log.warning("Could not write report to %s/%s: %s", subdir, filename, exc)
        return None


def _log_root_step(action: str, root: Path, index: int, total: int) -> None:
    """Log a clear boundary before processing a specific source root."""
    if total > 1:
        log.info("══ %s %d/%d — %s", action, index, total, root)


def _append_root_breakdown(summary_text: str, root_sections: list[tuple[Path, str]]) -> str:
    """Append per-root summaries below an aggregate summary when multiple roots are used."""
    if len(root_sections) <= 1:
        return summary_text

    lines = [summary_text, "", "Per-source breakdown:"]
    total = len(root_sections)
    for index, (root, section_text) in enumerate(root_sections, start=1):
        lines.extend(["", f"[{index}/{total}] {root}"])
        for line in section_text.splitlines():
            lines.append(f"  {line}" if line else "")
    return "\n".join(lines)


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # pyrekordbox has its own internal handler that also prints warnings —
    # suppress it to ERROR so playlist-not-found noise doesn't appear twice.
    logging.getLogger("pyrekordbox").setLevel(logging.ERROR)


# ─── Command handlers ─────────────────────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> None:
    """Run the interactive first-run (or re-run) setup wizard."""
    from user_config import interactive_setup
    interactive_setup(update=getattr(args, "update", False))


def cmd_audit(args: argparse.Namespace) -> None:
    """Run a full read-only audit and print the summary."""
    from audit import full_audit
    from db_connection import read_db

    root = Path(args.root) if args.root else MUSIC_ROOT
    extra_roots = [Path(r) for r in (args.also_scan or [])]

    log.info("Opening database (read-only): %s", LOCAL_DB)
    try:
        with read_db(LOCAL_DB) as db:
            report = full_audit(db, root=root, extra_roots=extra_roots)
        summary_text = report.summary()
        print(summary_text)
        # Write report to REPORTS_DIR/Audit/
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = _write_report("Audit", f"audit_{timestamp}.txt", summary_text)
        if report_path:
            print(f"FABLEGEAR_REPORT_PATH: {report_path}", flush=True)
    except Exception:
        log.exception("Audit failed")
        sys.exit(1)


def cmd_usb_inspect(args: argparse.Namespace) -> None:
    """Read-only inspection of a Pioneer export drive (dual-format check)."""
    from usb_inspector import inspect_usb, NotAMountError

    try:
        report = inspect_usb(args.mount)
    except NotAMountError as exc:
        print(f"\u2717 {exc}", flush=True)
        sys.exit(1)

    def mark(ok):
        return "\u2713" if ok else ("\u26a0" if ok is None else "\u2717")

    print(f"USB inspection: {report.mount}", flush=True)
    print(f"  PIONEER/ directory:  {mark(report.has_pioneer_dir)}", flush=True)
    print(f"  DeviceSQL (CDJ-3000): {mark(report.devicesql.valid if report.devicesql.present else False)}  {report.devicesql.detail}", flush=True)
    print(f"  OneLibrary (OMNIS):   {mark(report.onelibrary.valid if report.onelibrary.present else False)}  {report.onelibrary.detail}", flush=True)
    print(f"  ANLZ analysis files:  {report.anlz_track_count:,} track(s)", flush=True)
    if report.settings_files:
        print(f"  Settings files:       {', '.join(Path(p).name for p in report.settings_files)}", flush=True)
    for note in report.notes:
        print(f"  note: {note}", flush=True)
    verdict = ("DUAL-FORMAT \u2014 boots on both fleets" if report.dual_format
               else "CDJ-3000 only" if report.cdj3000_ready
               else "OneLibrary only" if report.onelibrary_ready
               else "no readable device database")
    print(f"  Verdict: {verdict}", flush=True)


def cmd_anlz_read(args: argparse.Namespace) -> None:
    """Read-only deep parse of a track's ANLZ set (.DAT/.EXT/.2EX)."""
    from anlz_reader import read_anlz_set

    set_report = read_anlz_set(args.path)

    print(f"ANLZ set: {set_report.anlz_dir}", flush=True)
    for note in set_report.notes:
        print(f"  note: {note}", flush=True)
    for label, file_report in (("DAT", set_report.dat), ("EXT", set_report.ext), ("2EX", set_report.two_ex)):
        if file_report is None:
            continue
        tags = ", ".join(file_report.tags_present) or "(none)"
        print(f"  .{label}: {len(file_report.tags_present)} tags — {tags}", flush=True)
        if file_report.ppth_path:
            print(f"    path: {file_report.ppth_path}", flush=True)
        if file_report.beat_grid:
            first = file_report.beat_grid[0]
            print(
                f"    beat grid: {len(file_report.beat_grid)} beats, "
                f"first {first.tempo_bpm:.2f} BPM @ {first.time_ms}ms",
                flush=True,
            )
        for name, wf in file_report.waveform_tags.items():
            print(f"    {name}: len_entry_bytes={wf.len_entry_bytes} len_entries={wf.len_entries} bytes={wf.entry_bytes_total}", flush=True)
        for note in file_report.notes:
            print(f"    note: {note}", flush=True)
    if set_report.track_path:
        print(f"  Embedded track path: {set_report.track_path}", flush=True)
    print(f"  Beat count: {set_report.beat_count}", flush=True)


def cmd_pioneer_settings(args: argparse.Namespace) -> None:
    """Read-only parse of Pioneer/rekordbox player & mixer settings files."""
    from pioneer_settings import read_settings_file, read_settings_tree

    path = Path(args.path)
    reports = [read_settings_file(path)] if path.is_file() else read_settings_tree(path)

    if not reports:
        print(f"No known settings files found under {path}", flush=True)
        return

    for r in reports:
        mark = "✓" if r.valid else ("⚠" if r.valid is None else "✗")
        print(f"{mark} {r.filename}: {r.detail}", flush=True)
        if r.settings:
            print(f"    {len(r.settings)} settings parsed via {r.parsed_via}", flush=True)
        for note in r.notes:
            print(f"    note: {note}", flush=True)


def cmd_pdb_read(args: argparse.Namespace) -> None:
    """Read-only header validation of a DeviceSQL export.pdb / exportExt.pdb."""
    from devicesql_reader import read_pdb

    report = read_pdb(args.path)
    mark = "✓" if report.valid_header else "✗"
    print(f"{mark} {report.path}: {report.detail}", flush=True)
    for note in report.notes:
        print(f"  note: {note}", flush=True)


def cmd_export_audit(args: argparse.Namespace) -> None:
    """Read-only Phase B deep audit of a mounted Pioneer export tree.

    Calls anlz_reader / pioneer_settings / devicesql_reader / usb_inspector
    internally (real function calls, not shelling out) and persists the
    consolidated findings through the FableGear archive.
    """
    from export_auditor import audit_export
    from usb_inspector import NotAMountError

    archive = _require_archive("export-audit")
    try:
        report = audit_export(args.mount, archive=archive)
    except NotAMountError as exc:
        print(f"✗ {exc}", flush=True)
        sys.exit(1)

    print(f"Export audit: {report.mount}", flush=True)
    if report.usb_inspection:
        insp = report.usb_inspection
        verdict = ("DUAL-FORMAT — boots on both fleets" if insp.dual_format
                   else "CDJ-3000 only" if insp.cdj3000_ready
                   else "OneLibrary only" if insp.onelibrary_ready
                   else "no readable device database")
        print(f"  Verdict: {verdict}", flush=True)

    a = report.anlz_summary
    print(
        f"  ANLZ: {a.tracks_scanned} tracks scanned, {a.with_beat_grid} with beat grid "
        f"({a.total_beats} beats total), {a.with_waveform} with waveform, "
        f"{a.dat_missing} missing .DAT",
        flush=True,
    )

    if report.settings_files:
        print(f"  Settings: {len(report.settings_files)} file(s) found", flush=True)
        for sf in report.settings_files:
            mark = "✓" if sf.valid else "✗"
            print(f"    {mark} {sf.filename}", flush=True)

    if report.pdb_report:
        mark = "✓" if report.pdb_report.valid_header else "✗"
        print(f"  PDB: {mark} {report.pdb_report.detail}", flush=True)
        if report.pdb_report.partial:
            print("    note: track row extraction failed for this file (see devicesql_reader.py)", flush=True)
        elif report.pdb_report.valid_header:
            print(
                f"    tracks recovered: {len(report.pdb_report.tracks)} "
                "(format-spec-verified, not hardware-verified — see devicesql_reader.py HONESTY LIMIT)",
                flush=True,
            )

    cm = report.library_cross_match
    if cm.anlz_tracks_with_path:
        print(f"  Library cross-match: {cm.matched_in_archive}/{cm.anlz_tracks_with_path} ANLZ tracks matched in archive", flush=True)

    for finding in report.encryption_findings:
        if finding.present:
            print(f"  ⚠ {finding.name}: present ({finding.size} bytes) — {finding.note}", flush=True)

    for note in report.notes:
        print(f"  note: {note}", flush=True)

    print(f"  Archive logged: {report.archive_logged}", flush=True)


def cmd_dead_files(args: argparse.Namespace) -> None:
    """Find audio files on disk not referenced in any Rekordbox database."""
    from dead_file_scanner import scan_dead_files

    roots = [Path(args.path)] + [Path(p) for p in (args.also_scan or [])]

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    db_paths = None  # defaults to LOCAL_DB + DEVICE_DB

    total_found = [0]
    total_files = [0]

    def _progress(scanned: int, total: int) -> None:
        total_files[0] = total
        total_found[0] = scanned
        print(f"FABLEGEAR_SCAN_TICK: {scanned}", flush=True)

    log.info("Dead-file scan: roots=%s", [str(r) for r in roots])
    archive = _require_archive("dead-files")
    try:
        result = scan_dead_files(roots, db_paths=db_paths, progress_cb=_progress, archive=archive)

        print(f"Scanned {result.total_scanned:,} audio files across {len(roots)} root(s).", flush=True)
        if result.db_paths_used:
            for db in result.db_paths_used:
                print(f"  DB: {db}", flush=True)
        else:
            print("  ⚠ No databases found — all files appear untracked.", flush=True)

        if result.dead_count == 0:
            print(f"✓ All {result.total_scanned:,} files are referenced in a database.", flush=True)
        else:
            print(f"⚠ {result.dead_count:,} untracked file(s):", flush=True)
            for f in result.dead_files:
                print(f"  DEAD: {f}", flush=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _emit_report(result.summary(), "DeadFiles", f"dead_files_{timestamp}.txt")
    except Exception:
        log.exception("Dead-file scan failed")
        sys.exit(1)


def cmd_import(args: argparse.Namespace) -> None:
    """Import audio files under one or more source paths into the database."""
    from importer_database import import_multi_drive_database_first
    from db_connection import read_db, write_db

    roots: list[Path] = [Path(args.path)]
    for extra in (getattr(args, "also_scan", None) or []):
        p = Path(extra)
        if p not in roots:
            roots.append(p)

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    if args.dry_run:
        log.info("DRY RUN — no writes will occur (delegating to database-first preview)")
        # We can run it with export_to_rekordbox=False to preview
        try:
            report = import_multi_drive_database_first(
                roots,
                export_to_rekordbox=False,
                force_refresh=False,
            )
            summary_text = (
                f"Database-First Import Preview (Dry Run):\n"
                f"  Total files scanned: {report.total_files}\n"
                f"  New files:           {report.new_files}\n"
                f"  Updated files:       {report.updated_files}\n"
                f"  Skipped files:       {report.skipped_files}\n"
                f"  Error files:         {report.error_files}\n"
            )
            print(summary_text)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = _write_report("Import", f"preview_import_{timestamp}.txt", summary_text)
            if report_path:
                print(f"FABLEGEAR_REPORT_PATH: {report_path}", flush=True)
        except Exception:
            log.exception("Dry-run import failed")
            sys.exit(1)
    else:
        log.info("Importing from %d source folder(s) (database-first)", len(roots))
        try:
            # Progress callback that prints progress to match SSE generator expects
            def progress_callback(current, total, drive_idx=0, drive_total=1):
                # Emit progress ticks to keep scan bar moving
                print(f"FABLEGEAR_PROGRESS: {json.dumps({'done': current, 'total': total})}", flush=True)

            report = import_multi_drive_database_first(
                roots,
                export_to_rekordbox=True,
                progress_callback=progress_callback,
                force_refresh=False,
            )
            summary_text = (
                f"Database-First Import Report:\n"
                f"  Total files scanned: {report.total_files}\n"
                f"  New files:           {report.new_files}\n"
                f"  Updated files:       {report.updated_files}\n"
                f"  Skipped files:       {report.skipped_files}\n"
                f"  Error files:         {report.error_files}\n"
                f"  Synced to Rekordbox: {report.rekordbox_exported}\n"
            )
            if report.errors:
                summary_text += "\nErrors:\n" + "\n".join(f"  {err}" for err in report.errors[:50])
            print(summary_text)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = _write_report("Import", f"import_{timestamp}.txt", summary_text)
            if report_path:
                print(f"FABLEGEAR_REPORT_PATH: {report_path}", flush=True)
        except Exception:
            log.exception("Import failed")
            sys.exit(1)


def cmd_link(args: argparse.Namespace) -> None:
    """Link imported tracks under one or more source paths to existing playlists."""
    from playlist_linker import link_directory
    from db_connection import read_db, write_db

    roots: list[Path] = [Path(args.path)]
    for extra in (getattr(args, "also_scan", None) or []):
        p = Path(extra)
        if p not in roots:
            roots.append(p)

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    db_ctx = read_db if args.dry_run else write_db
    aggregate = None
    root_sections: list[tuple[Path, str]] = []

    def _merge(report):
        nonlocal aggregate
        if aggregate is None:
            aggregate = report
            return
        aggregate.linked += report.linked
        aggregate.unmatched += report.unmatched
        aggregate.total_links += report.total_links
        aggregate.failed += report.failed
        aggregate.results.extend(report.results)

    log.info("Linking tracks under %d source folder(s)", len(roots))
    try:
        with db_ctx(LOCAL_DB) as db:
            for index, root in enumerate(roots, start=1):
                _log_root_step("Link", root, index, len(roots))
                report = link_directory(root, db, dry_run=args.dry_run)
                _merge(report)
                root_sections.append((root, report.summary()))
        summary_text = aggregate.summary() if aggregate else "No link sources were processed."
        summary_text = _append_root_breakdown(summary_text, root_sections)
        print(summary_text)
    except Exception:
        log.exception("Playlist linking failed")
        sys.exit(1)


def cmd_relocate(args: argparse.Namespace) -> None:
    """Batch-update FolderPath for files moved from OLD_ROOT to NEW_ROOT."""
    from relocator import relocate_directory
    from db_connection import write_db

    old_root = Path(args.old_root)
    new_root = Path(args.new_root)

    if not new_root.is_dir():
        log.error("NEW_ROOT is not a directory: %s", new_root)
        sys.exit(1)

    # old_root doesn't need to exist on disk — it's a string prefix matched
    # against FolderPath values in the DB. If it's a typo, relocate_directory
    # will match zero rows and log a warning.
    only_missing = not getattr(args, "include_existing", False)
    log.info(
        "Relocating: %s → %s (%s)",
        old_root, new_root,
        "broken paths only" if only_missing else "ALL rows under old_root",
    )
    archive = _require_archive("relocate")
    try:
        with write_db(LOCAL_DB) as db:
            results = relocate_directory(
                old_root, new_root, db, archive=archive, only_missing=only_missing,
            )
    except Exception:
        log.exception("Relocation failed")
        sys.exit(1)

    total = len(results)
    by_strategy: dict[str, int] = {}
    failed = 0
    for r in results:
        by_strategy[r.strategy] = by_strategy.get(r.strategy, 0) + 1
        if not r.success:
            failed += 1

    not_found = by_strategy.get("not_found", 0)
    updated   = total - not_found - failed

    lines = ["Done updating RekordBox paths.", "", f"{updated} of {total} tracks were updated."]
    if by_strategy.get("exact", 0):
        lines.append(f"  {by_strategy['exact']} matched by exact path.")
    if by_strategy.get("hash", 0):
        lines.append(f"  {by_strategy['hash']} matched by file content.")
    if by_strategy.get("fuzzy", 0):
        lines.append(f"  {by_strategy['fuzzy']} matched by filename.")
    if not_found:
        lines += ["", f"{not_found} tracks couldn't be found at the new location.",
                  "  Run Audit to see which ones and decide what to do."]
    if failed:
        lines += ["", f"{failed} tracks had write errors — check the log above."]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _emit_report("\n".join(lines), "Relocate", f"relocate_{timestamp}.txt")


def _get_checkpoint(tool: str, roots, args, config: dict | None = None):
    """Build a Checkpoint for tool+roots+config, honoring --checkpoint-action.

    Shared by every long-running per-file command (process, convert, organize,
    rename, novelty — duplicates uses its own richer variant below since it
    also restores in-memory fingerprint maps, not just a completed-count).

    reset clears any prior state and starts over; the default (resume) lets
    the caller pick up exactly where an interrupted run stopped by slicing
    its file list at saved["completed"]. Returns None if the checkpoint
    module or file is unavailable — callers must proceed unconditionally
    (no resume support) rather than fail when that happens.
    """
    try:
        from checkpoint import Checkpoint  # noqa: PLC0415
    except ImportError:
        return None
    try:
        ckpt = Checkpoint(tool, [str(r) for r in roots], config or {})
        action = getattr(args, "checkpoint_action", None) or "resume"
        if action == "reset":
            ckpt.reset()
            log.info("Checkpoint reset — starting %s from the beginning.", tool)
        elif ckpt.exists():
            info = ckpt.info()
            log.info(
                "Found checkpoint from %s (%s/%s done) — resuming. "
                "Pass --checkpoint-action reset to start over.",
                info.get("saved_at", "?"), info.get("completed", "?"), info.get("total", "?"),
            )
        return ckpt
    except Exception as exc:
        log.warning("Checkpoint unavailable (%s) — running without resume support.", exc)
        return None


def _duplicates_checkpoint(roots, args):
    """Build the duplicate-scan Checkpoint for resume/reset support.

    Returns a Checkpoint object (or None if the module is unavailable).
    --checkpoint-action reset clears any prior state; the default (resume)
    lets scan_duplicates pick up exactly where an interrupted run stopped.
    """
    try:
        from checkpoint import Checkpoint  # noqa: PLC0415
    except ImportError:
        return None
    try:
        config = {
            "match_mode": getattr(args, "match_mode", "exact"),
            "fuzzy_threshold": f"{getattr(args, 'fuzzy_threshold', 0.85):.2f}",
        }
        ckpt = Checkpoint("duplicates", [str(r) for r in roots], config)
        action = getattr(args, "checkpoint_action", None) or "resume"
        if action == "reset":
            ckpt.reset()
            log.info("Checkpoint reset — starting the scan from the beginning.")
        elif ckpt.exists():
            info = ckpt.info()
            log.info(
                "Found checkpoint from %s (%s files done) — resuming. "
                "Pass --checkpoint-action reset to start over.",
                info.get("saved_at", "?"), info.get("completed", "?"),
            )
        return ckpt
    except Exception as exc:
        log.warning("Checkpoint unavailable (%s) — running without resume support.", exc)
        return None


def cmd_duplicates(args: argparse.Namespace) -> None:
    """Scan one or more PATHs for duplicate files and write a CSV report.

    Two tiers: scan_mode='quick' does instant byte-identical matching from the
    cached DB hashes; scan_mode='deep' (default) runs acoustic fingerprinting.
    Both return a ScanResult, so the report / prune / resolve pipeline is shared.
    """
    from duplicate_detector import (
        scan_duplicates, scan_duplicates_hash, write_csv_report, write_trash_rescue_report,
    )

    paths = args.path if isinstance(args.path, list) else [args.path]
    roots = []
    for p in paths:
        r = Path(p)
        if not r.is_dir():
            log.error("PATH is not a directory: %s", r)
            sys.exit(1)
        roots.append(r)
    root = roots[0] if len(roots) == 1 else roots

    if args.output:
        output = Path(args.output)
    else:
        # Default: write into REPORTS_DIR/Duplicates/ (local user path on macOS),
        # otherwise fall back to ~/.fablegear/Reports/Duplicates.
        try:
            try:
                from FableGear.config import REPORTS_DIR  # noqa: PLC0415
            except ImportError:
                from config import REPORTS_DIR           # noqa: PLC0415
            out_dir = REPORTS_DIR / "Duplicates"
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = out_dir / f"duplicate_report_{timestamp}.csv"
        except Exception:
            output = Path.home() / ".fablegear" / "Reports" / "Duplicates" / "duplicate_report.csv"

    rescue_output = output.with_name(
        output.stem.replace("duplicate_report", "trash_rescue_report")
        if "duplicate_report" in output.stem
        else f"trash_rescue_{output.stem}"
    ).with_suffix(".txt")

    workers = max(1, args.workers)
    scan_mode = getattr(args, "scan_mode", "deep")
    root_label = ", ".join(str(r) for r in roots)
    if scan_mode == "quick":
        log.info("Quick scan under: %s — byte-identical match from cached hashes (no fpcalc).", root_label)
    else:
        log.info("Scanning for duplicates under: %s (workers=%d, match=%s)", root_label, workers, args.match_mode)
        log.info("This may take a while for large libraries — progress logged every %d files", 100)

    # ── Checkpoint: only the deep (fpcalc) scan can be interrupted/resumed;
    # the quick hash scan is instant, so it never touches the checkpoint.
    ckpt = None if scan_mode == "quick" else _duplicates_checkpoint(roots, args)
    if scan_mode != "quick" and len(roots) > 1:
        log.info(
            "Selected folders are scanned together as one comparison set so duplicates across different source folders are not missed."
        )

    archive = _require_archive("duplicates")
    try:
        if scan_mode == "quick":
            result = scan_duplicates_hash(root, archive=archive)
        else:
            result = scan_duplicates(
                root,
                max_workers=workers,
                match_mode=args.match_mode,
                fuzzy_threshold=args.fuzzy_threshold,
                checkpoint=ckpt,
                archive=archive,
            )
    except Exception:
        log.exception("Duplicate scan failed")
        sys.exit(1)

    groups   = result.groups
    removable = sum(len(g.recommended_remove) for g in groups)
    trapped_keeps = sum(1 for g in groups if g.keep_in_trash)

    # ── Trash rescue warning ──────────────────────────────────────────────────
    if result.unique_in_trash or trapped_keeps:
        print()
        print("  ╔══════════════════════════════════════════════════════════════╗")
        print("  ║  !!! RESCUE REQUIRED — DO NOT CLEAR TRASH YET !!!           ║")
        print("  ╠══════════════════════════════════════════════════════════════╣")
        if result.unique_in_trash:
            print(f"  ║  {len(result.unique_in_trash):>5} tracks exist ONLY in a trash folder            ║")
            print("  ║        → NOT included in the pruning CSV                    ║")
            print("  ║        → FableGear does not offer an automated rescue step   ║")
            print("  ║        → move these files manually before clearing trash    ║")
        if trapped_keeps:
            print(f"  ║  {trapped_keeps:>5} duplicate groups have their best copy in trash   ║")
            print("  ║        → marked keep_in_trash=YES in the CSV                ║")
            print("  ║        → pruner will NOT delete them, but manual trash      ║")
            print("  ║          cleanup would — move them first                    ║")
        print("  ╚══════════════════════════════════════════════════════════════╝")

    if not groups and not result.unique_in_trash:
        _emit_report(
            "No duplicates found. Every file in this folder appears to be unique.",
            "Duplicates", f"duplicates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
    else:
        lines = [
            f"Found {len(groups)} groups of identical tracks — {removable} files could be removed.",
            "Each group contains the same recording in different files.",
            "A report has been saved so you can review each group before deleting anything.",
        ]
        try:
            if groups:
                write_csv_report(result, output)
                lines.append(f"\nReport saved to: {output}")
            write_trash_rescue_report(result, rescue_output)
            lines.append(f"Rescue report:   {rescue_output}")
        except Exception:
            log.exception("Failed to write CSV report")
            sys.exit(1)
        _emit_report("\n".join(lines), "Duplicates",
                     f"duplicates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        if groups:
            print(f"FABLEGEAR_REPORT_PATH: {output}", flush=True)


def _persist_process_results(all_results, archive) -> int:
    """
    Persist tagger analysis (BPM / key) into fg_content and append a
    tag_tracks row to fg_processing_log.

    This is the producer half of the tagger → deduper Archive edge: what the
    tagger learns is durable, so downstream tools read it instead of
    recomputing. Returns the number of fg_content rows written.
    """
    if archive is None:
        return 0
    entries = []
    for r in all_results:
        if r.bpm_detected is None and r.key_detected is None:
            continue
        try:
            size = r.path.stat().st_size
        except OSError:
            size = 0
        entries.append((str(r.path), r.bpm_detected, r.key_detected, size))
    try:
        written = archive.bulk_set_analysis(entries)
        archive.log_operation(
            "tag_tracks",
            status="ok",
            metadata={
                "files_processed": len(all_results),
                "analysis_persisted": written,
                "bpm_written": sum(1 for r in all_results if r.bpm_written),
                "key_written": sum(1 for r in all_results if r.key_written),
                "normalized": sum(1 for r in all_results if r.normalised),
                "enrich_written": sum(1 for r in all_results if r.enrich_written),
                "errors": sum(1 for r in all_results if not r.ok),
            },
        )
        if written:
            log.info("Archive updated: BPM/key for %d file(s) persisted to fg_content", written)
        return written
    except Exception as exc:
        log.warning("Failed to persist tagger analysis to archive: %s", exc)
        return 0


def _run_shared_report(args, all_results, root_sections, _quarantine_dir) -> None:
    """
    Build and emit the Tag Tracks / Normalize completion report.
    Called from both the directory-scan and --paths-file retry branches of cmd_process.
    """

    _persist_process_results(all_results, _archive())

    detect_bpm = not args.no_bpm
    detect_key = not args.no_key
    normalise = not args.no_normalize and not getattr(args, "dry_run", False)

    total = len(all_results)
    bpm_written = sum(1 for r in all_results if r.bpm_written)
    key_written = sum(1 for r in all_results if r.key_written)
    normalised = sum(1 for r in all_results if r.normalised)
    errored = sum(1 for r in all_results if not r.ok)
    quarantined_results = [r for r in all_results if r.quarantined]
    quarantined = len(quarantined_results)
    skipped_bpm = sum(1 for r in all_results if r.skipped_bpm)
    skipped_key = sum(1 for r in all_results if r.skipped_key)

    # Summarise which roots were scanned so multi-drive runs are clear
    source_names = [str(r) for r, _ in root_sections] if root_sections else []
    if len(source_names) > 1:
        sources_line = "Sources scanned: " + ", ".join(source_names)
    elif len(source_names) == 1:
        sources_line = "Source: " + source_names[0]
    else:
        sources_line = ""

    if normalise and not detect_bpm and not detect_key:
        report_lines = [
            "\nDone.\n",
            f"{normalised} tracks were re-encoded to match the loudness target.",
            f"{total - normalised - errored} were already at the right level and skipped.",
        ]
    elif normalise:
        report_lines = [
            "\nDone.\n",
            f"{total} files were analyzed.",
            f"  BPM written: {bpm_written} files.{f'  {skipped_bpm} already had one.' if skipped_bpm else ''}",
            f"  Key written: {key_written} files.{f'  {skipped_key} already had one.' if skipped_key else ''}",
            f"  Loudness adjusted: {normalised} files.",
        ]
    else:
        report_lines = [
            "\nDone tagging.\n",
            f"{total} files were analyzed.",
            f"  BPM written: {bpm_written} files.{f'  {skipped_bpm} already had one.' if skipped_bpm else ''}",
            f"  Key written: {key_written} files.{f'  {skipped_key} already had one.' if skipped_key else ''}",
        ]

    if sources_line:
        report_lines.insert(1, sources_line)
        enrich_written = sum(1 for r in all_results if getattr(r, "enrich_written", False))
        if getattr(args, "enrich_tags", False) and enrich_written:
            report_lines.append(f"  MusicBrainz enriched: {enrich_written} files.")

    # ── Error breakdown ──────────────────────────────────────────────────────
    if errored:
        corrupt_results  = [r for r in all_results if r.quarantined]
        decode_results   = [r for r in all_results
                            if not r.quarantined and not r.ok
                            and any("audio decode failed" in e for e in r.errors)]
        tag_fail_results = [r for r in all_results
                            if not r.quarantined and not r.ok
                            and not any("audio decode failed" in e for e in r.errors)
                            and any("tag write failed" in e or "normalisation failed" in e
                                    for e in r.errors)]
        other_results    = [r for r in all_results
                            if not r.quarantined and not r.ok
                            and not any("audio decode failed" in e for e in r.errors)
                            and not any("tag write failed" in e or "normalisation failed" in e
                                        for e in r.errors)]

        report_lines.append(f"\n{'─' * 60}")
        report_lines.append(f"ERRORS  —  {errored} file(s) could not be fully processed\n")

        if decode_results:
            report_lines.append(f"  ⚠  Audio Decode Failures ({len(decode_results)})")
            report_lines.append("     File opened, but audio couldn't be decoded — BPM/key were skipped.")
            report_lines.append("     → Convert these files to MP3 or AIFF first, then re-run Tag Tracks.")
            for r in decode_results[:12]:
                report_lines.append(f"       • {r.path.name}")
            if len(decode_results) > 12:
                report_lines.append(f"       … and {len(decode_results) - 12} more — see log for full list")
            report_lines.append("")

        if tag_fail_results:
            report_lines.append(f"  ⚠  Tag Write Failures ({len(tag_fail_results)})")
            report_lines.append("     BPM/key detection succeeded, but writing the tag to the file failed.")
            report_lines.append("     → Check file is not read-only, then re-run with Force tag-overwrite on.")
            for r in tag_fail_results[:12]:
                err_short = next(
                    (e for e in r.errors if "tag write failed" in e or "normalisation failed" in e),
                    r.errors[0] if r.errors else "unknown",
                )
                report_lines.append(f"       • {r.path.name}  [{err_short}]")
            if len(tag_fail_results) > 12:
                report_lines.append(f"       … and {len(tag_fail_results) - 12} more")
            report_lines.append("")

        if corrupt_results:
            report_lines.append(f"  ✗  Corrupt / Unreadable — moved to Quarantine ({len(corrupt_results)})")
            report_lines.append("     These files could not be opened at the audio-library level.")
            report_lines.append(f"     Location: {_quarantine_dir}")
            report_lines.append("     → Inspect in the Quarantine folder. Delete or restore manually.")
            for r in corrupt_results[:12]:
                report_lines.append(f"       • {r.path.name}")
            if len(corrupt_results) > 12:
                report_lines.append(f"       … and {len(corrupt_results) - 12} more")
            report_lines.append("")

        if other_results:
            report_lines.append(f"  ⚠  Other Errors ({len(other_results)})")
            for r in other_results[:12]:
                err_short = r.errors[0] if r.errors else "unknown error"
                report_lines.append(f"       • {r.path.name}  [{err_short}]")
            if len(other_results) > 12:
                report_lines.append(f"       … and {len(other_results) - 12} more")
            report_lines.append("")

    if quarantined and not errored:
        report_lines.append(
            f"\n{'─' * 60}\n"
            f"QUARANTINED: {quarantined} corrupt file(s) moved to:\n"
            f"  {_quarantine_dir}\n"
        )
        for r in quarantined_results:
            report_lines.append(f"  {r.path.name}")
        report_lines.append("")

    report_text = _append_root_breakdown("\n".join(report_lines), root_sections)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if normalise:
        _emit_report(report_text, "Normalize", f"normalize_{timestamp}.txt")
    else:
        _emit_report(report_text, "Tag Tracks", f"tag_tracks_{timestamp}.txt")

    if errored > 0:
        log.warning("%d files had errors — check log above", errored)


def cmd_prune(args: argparse.Namespace) -> None:
    """Auto-prune duplicates listed in a duplicate_report.csv.

    Reads the quality-ranked report, keeps the best copy in each group, and
    moves the rest to a recoverable Trash folder (removing their DB rows and
    re-threading playlists to the keeper). Dry-run by default — pass
    --no-dry-run to actually prune. Mirrors the interactive Chop Shop prune
    and is the executor for the pipeline's "prune" step.
    """
    from pruner import load_report, prune_files
    from db_connection import read_db, write_db

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        log.error("Duplicate report not found: %s", csv_path)
        sys.exit(1)

    # Operate on the device DB when the Pioneer drive is mounted, else local —
    # same selection the interactive prune endpoint uses.
    try:
        from FableGear.config import DEVICE_DB as _DEVICE_DB  # noqa: PLC0415
    except ImportError:
        try:
            from config import DEVICE_DB as _DEVICE_DB        # noqa: PLC0415
        except Exception:
            _DEVICE_DB = None
    db_path = _DEVICE_DB if (_DEVICE_DB and _DEVICE_DB.exists()) else LOCAL_DB

    # Load + rank the report (read-only connection flags DB-referenced files).
    try:
        with read_db(db_path) as _rdb:
            groups = load_report(csv_path, _rdb)
    except Exception:
        log.exception("Failed to load duplicate report")
        sys.exit(1)

    remove_paths: list[str] = []
    keeper_map: dict[str, str] = {}
    locked_groups = 0
    for g in groups:
        cands = g.remove_candidates          # built-in trash-safety lock
        if not cands:
            if g.keep_in_trash:
                locked_groups += 1
            continue
        keep = g.keep
        for e in cands:
            remove_paths.append(e.file_path)
            if keep:
                keeper_map[e.file_path] = keep.file_path

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not remove_paths:
        _emit_report(
            "No prunable duplicates found.\n"
            f"  Groups in report                 : {len(groups)}\n"
            f"  Locked (keeper in trash, skipped): {locked_groups}",
            "Prune", f"prune_{timestamp}.txt")
        return

    if args.dry_run:
        lines = [
            "DRY RUN — no files were removed.",
            "",
            f"Would remove {len(remove_paths)} duplicate file(s), keeping the best "
            f"copy in each of {len(groups)} group(s).",
        ]
        if locked_groups:
            lines.append(f"  {locked_groups} group(s) skipped (best copy is in a trash folder).")
        lines.append("")
        lines += [f"  REMOVE: {p}" for p in remove_paths[:200]]
        if len(remove_paths) > 200:
            lines.append(f"  … and {len(remove_paths) - 200} more.")
        lines += ["", "Re-run with --no-dry-run to move these to the recovery folder."]
        _emit_report("\n".join(lines), "Prune", f"prune_{timestamp}.txt")
        return

    log.info("Pruning %d duplicate file(s) from %s", len(remove_paths), db_path)
    archive = _require_archive("prune")
    try:
        with write_db(db_path) as db:
            summary = prune_files(
                remove_paths,
                db,
                log=lambda m: print(m, flush=True),
                permanent=args.permanent,
                keeper_map=keeper_map,
                archive=archive,
            )
    except Exception:
        log.exception("Prune failed")
        sys.exit(1)

    lines = [
        "Prune complete.",
        "",
        f"  DB entries removed   : {summary.get('db_removed', 0)}",
        f"  Files moved to trash : {summary.get('files_moved', 0)}",
        f"  Playlists re-threaded: {summary.get('playlists_rethreaded', 0)}",
        f"  Skipped              : {summary.get('skipped', 0)}",
    ]
    if summary.get("trash_dir"):
        lines.append(f"  Recovery folder      : {summary['trash_dir']}")
    errs = summary.get("errors") or []
    if errs:
        lines += ["", f"{len(errs)} error(s):"] + [f"  {e}" for e in errs[:50]]
    _emit_report("\n".join(lines), "Prune", f"prune_{timestamp}.txt")


def _resolve_active_db_path(cli_db_path: str | None = None) -> Path:
    """Return the DB path to operate on (explicit override > DEVICE > LOCAL)."""
    if cli_db_path:
        candidate = Path(cli_db_path).expanduser()
        if not candidate.exists():
            log.error("DB path does not exist: %s", candidate)
            sys.exit(1)
        return candidate

    try:
        from FableGear.config import DEVICE_DB as _DEVICE_DB  # noqa: PLC0415
    except ImportError:
        try:
            from config import DEVICE_DB as _DEVICE_DB  # noqa: PLC0415
        except Exception:
            _DEVICE_DB = None

    if _DEVICE_DB and _DEVICE_DB.exists():
        return _DEVICE_DB
    if LOCAL_DB is None:
        log.error("No Rekordbox database path available")
        sys.exit(1)
    return LOCAL_DB


def cmd_rekordbox_sync(args: argparse.Namespace) -> None:
    """Run bidirectional synchronization between FableGear and Rekordbox databases."""
    from config import LOCAL_DB
    from fablegear_database import FableGearDatabase, RekordboxSyncAdapter
    
    db_path = _resolve_active_db_path(getattr(args, "db_path", None))
    dry_run = getattr(args, "dry_run", True)
    
    log.info("Starting bidirectional Rekordbox synchronization using DB: %s", db_path)
    if dry_run:
        log.info("Dry-run mode active. No database writes will be executed.")
        
    try:
        fg_db = FableGearDatabase()
        adapter = RekordboxSyncAdapter(fg_db)
        stats = adapter.sync_bidirectional(db_path, dry_run=dry_run)
        
        log.info("Synchronization complete. Results:")
        log.info("  Tracks imported to Rekordbox: %d", stats["tracks_imported_to_rekordbox"])
        log.info("  Tracks imported to FableGear:  %d", stats["tracks_imported_to_fablegear"])
        log.info("  Tracks updated in Rekordbox:  %d", stats["tracks_updated_in_rekordbox"])
        log.info("  Tracks updated in FableGear:   %d", stats["tracks_updated_in_fablegear"])
        log.info("  Cues/loops synchronized:       %d", stats["cues_synchronized"])
        log.info("  Cues/loops deleted:            %d", stats["cues_deleted"])
        
        if stats["errors"]:
            log.error("Sync encountered errors:")
            for err in stats["errors"]:
                log.error("  %s", err)
            sys.exit(1)
            
    except Exception as e:
        log.exception("Fatal error during database synchronization: %s", e)
        sys.exit(1)


def cmd_import_missing_rekordbox(args: argparse.Namespace) -> None:
    """Phase 2: add the audio a recovery references but the Rekordbox collection
    lacks (located via FableGear's archive) so a later push can link them.
    Dry-run unless --write; --write requires Rekordbox closed, backs up first,
    records added track ids for --undo, and logs to the audit trail."""
    import json
    import shutil
    from datetime import datetime
    from pathlib import Path
    import playlist_recovery as R
    from fablegear_database import FableGearDatabase

    MANIFESTS = Path.home() / ".fablegear" / "rekordbox_import_manifests"
    LIVE_DB = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    target = getattr(args, "target", None)

    if getattr(args, "undo", False):
        if _rekordbox_running():
            log.error("Close Rekordbox first, then re-run --undo.")
            sys.exit(1)
        mans = sorted(MANIFESTS.glob("*.json")) if MANIFESTS.is_dir() else []
        if not mans:
            log.error("No import manifest found — nothing to undo.")
            sys.exit(1)
        man = json.loads(mans[-1].read_text())
        from pyrekordbox import Rekordbox6Database
        db = Rekordbox6Database(path=man.get("target") or None)
        n = 0
        for cid in man.get("added_content_ids", []):
            try:
                db.delete_content(cid); n += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("  could not remove track %s: %s", cid, exc)
        db.commit(); db.close()
        mans[-1].rename(mans[-1].with_suffix(".json.undone"))
        log.info("Undid import — removed %d added track(s).", n)
        return

    sources = list(getattr(args, "source", None) or [])
    if getattr(args, "source_list", None):
        for ln in Path(args.source_list).read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                sources.append(ln)
    if not sources:
        log.error("Pass --source / --source-list (e.g. the recovered master.db).")
        sys.exit(1)

    fg = FableGearDatabase()
    log.info("Reading recovered crates from %d source(s)…", len(sources))
    rec = R.recover(sources, database=None, strategy="richest",
                    merge_numbered=getattr(args, "merge_duplicates", False))

    if not getattr(args, "write", False):
        rep = R.import_missing_to_rekordbox(rec.crates, fg, target_db_path=target, dry_run=True)
        log.info("Target: %s", rep.target)
        log.info("Files referenced by recovered crates: %d", rep.wanted_files)
        log.info("  already in collection : %d", rep.already_present)
        log.info("  missing               : %d", rep.missing)
        log.info("  -> LOCATABLE (add now): %d", rep.locatable)
        log.info("  -> not locatable      : %d (drive unmounted or gone)", rep.not_locatable)
        for fn in rep.sample:
            log.info("      + %s", fn)
        log.info("DRY RUN — nothing added. Re-run with --write (Rekordbox closed).")
        return

    if _rekordbox_running():
        log.error("Rekordbox is running — close it before --write.")
        sys.exit(1)
    tgt = Path(target) if target else LIVE_DB
    if not tgt.is_file():
        log.error("Target master.db not found: %s", tgt)
        sys.exit(1)
    bdir = Path.home() / ".fablegear" / "rekordbox_master_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_import")
    bdir.mkdir(parents=True, exist_ok=True)
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tgt) + suf)
        if p.is_file():
            shutil.copy2(p, bdir / p.name)
    log.info("Backed up %s → %s", tgt.name, bdir)

    rep = R.import_missing_to_rekordbox(rec.crates, fg, target_db_path=target, dry_run=False)
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    man = {"target": target, "backup": str(bdir), "added": rep.added,
           "added_content_ids": rep.added_content_ids, "timestamp": datetime.now().isoformat()}
    (MANIFESTS / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").write_text(json.dumps(man, indent=2))
    try:
        fg.log_operation("import_to_rekordbox", file_path=str(tgt), status="ok",
                         metadata={"added": rep.added, "backup": str(bdir)})
    except Exception:  # noqa: BLE001
        pass
    log.info("%s", rep.detail)
    log.info("These new tracks are in the collection but UNANALYZED — analyze them in "
             "Rekordbox (select all → Analyze) for waveforms/grids.")
    log.info("Undo (Rekordbox closed): python3 cli.py import-missing-to-rekordbox --undo")
    log.info("Then run push-recovery-to-rekordbox --write to link the crates.")


def cmd_push_rekordbox(args: argparse.Namespace) -> None:
    """Push recovered crates into the live Rekordbox master.db (dry-run unless
    --write). Non-destructive: creates new playlists under one folder, links
    only tracks already in the collection (deduped), and skips any playlist name
    that already exists. --write requires Rekordbox closed and takes its own
    backup first; every created playlist id is recorded for one-command --undo."""
    import json
    import shutil
    from datetime import datetime
    from pathlib import Path
    import playlist_recovery as R
    from fablegear_database import FableGearDatabase

    MANIFESTS = Path.home() / ".fablegear" / "rekordbox_push_manifests"
    LIVE_DB = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    target = getattr(args, "target", None)

    # ── Undo ──
    if getattr(args, "undo", False):
        if _rekordbox_running():
            log.error("Close Rekordbox first, then re-run --undo.")
            sys.exit(1)
        manifests = sorted(MANIFESTS.glob("*.json")) if MANIFESTS.is_dir() else []
        if not manifests:
            log.error("No push manifest found — nothing to undo.")
            sys.exit(1)
        man = json.loads(manifests[-1].read_text())
        from pyrekordbox import Rekordbox6Database
        db = Rekordbox6Database(path=man.get("target") or None)
        n = 0
        for pid in reversed(man.get("created_playlist_ids", [])):
            try:
                db.delete_playlist(pid); n += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("  could not delete playlist %s: %s", pid, exc)
        db.commit(); db.close()
        manifests[-1].rename(manifests[-1].with_suffix(".json.undone"))
        log.info("Undid push %s — removed %d playlist(s).", man.get("folder_name"), n)
        return

    # ── Gather + resolve (read-only) ──
    sources = list(getattr(args, "source", None) or [])
    if getattr(args, "source_list", None):
        for ln in Path(args.source_list).read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                sources.append(ln)
    if not sources:
        log.error("Pass --source / --source-list (e.g. the recovered master.db).")
        sys.exit(1)

    log.info("Reading recovered crates from %d source(s)…", len(sources))
    rec = R.recover(sources, database=None, strategy="richest",
                    merge_numbered=getattr(args, "merge_duplicates", False))
    crates = rec.crates
    skip_existing = not getattr(args, "no_skip_existing", False)

    min_tracks = int(getattr(args, "min_tracks", 1))
    if not getattr(args, "write", False):
        rep = R.push_to_rekordbox(crates, target_db_path=target, dry_run=True,
                                  skip_existing=skip_existing, min_tracks=min_tracks)
        log.info("Target: %s", rep.target)
        log.info("Recovered crates: %d", rep.total_crates)
        log.info("  would CREATE : %d crate(s), %d track link(s)", rep.crates_planned, rep.links_planned)
        log.info("  would SKIP   : %d (name already in your library)", rep.skipped_existing)
        log.info("  no match     : %d crate(s) with no track in the current collection", rep.crates_no_match)
        log.info("  need import  : %d placement(s) whose files aren't in the collection yet", rep.unresolved_placements)
        for name, n in rep.sample:
            log.info("      [%5d]  %s", n, name)
        log.info("DRY RUN — nothing written. Re-run with --write to push (Rekordbox must be closed).")
        return

    # ── WRITE (guarded) ──
    if _rekordbox_running():
        log.error("Rekordbox is running — close it before --write.")
        sys.exit(1)
    tgt = Path(target) if target else LIVE_DB
    if not tgt.is_file():
        log.error("Target master.db not found: %s", tgt)
        sys.exit(1)
    # own fresh backup
    bdir = Path.home() / ".fablegear" / "rekordbox_master_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_push")
    bdir.mkdir(parents=True, exist_ok=True)
    for suf in ("", "-wal", "-shm"):
        p = Path(str(tgt) + suf)
        if p.is_file():
            shutil.copy2(p, bdir / p.name)
    log.info("Backed up %s → %s", tgt.name, bdir)

    folder_name = f"Recovered {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    rep = R.push_to_rekordbox(crates, target_db_path=target, dry_run=False,
                              folder_name=folder_name, skip_existing=skip_existing,
                              min_tracks=min_tracks)

    MANIFESTS.mkdir(parents=True, exist_ok=True)
    man = {"folder_name": folder_name, "target": target, "backup": str(bdir),
           "created_folder_id": rep.created_folder_id,
           "created_playlist_ids": rep.created_playlist_ids,
           "crates": rep.crates_planned, "links": rep.links_planned,
           "timestamp": datetime.now().isoformat()}
    (MANIFESTS / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").write_text(json.dumps(man, indent=2))
    try:
        FableGearDatabase().log_operation("push_to_rekordbox", file_path=str(tgt), status="ok",
                                          metadata={k: man[k] for k in ("folder_name", "crates", "links", "backup")})
    except Exception:  # noqa: BLE001
        pass
    log.info("%s", rep.detail)
    log.info("Undo anytime (Rekordbox closed): python3 cli.py push-recovery-to-rekordbox --undo")
    log.info("Backup kept at %s", bdir)


def cmd_recover_playlists(args: argparse.Namespace) -> None:
    """Recover playlists ("crates") from exported media and rebuild them in the
    FableGear archive. Dry-run by default (report only). With --write, rebuilds
    every crate that has enough resolved tracks under one timestamped
    "Recovered <ts>" folder — non-destructive (only creates new playlists),
    checkpointed, audit-logged, and removable in one action (delete the folder).
    """
    import playlist_recovery as R
    from fablegear_database import FableGearDatabase
    from datetime import datetime

    sources = list(getattr(args, "source", None) or [])
    if getattr(args, "source_list", None):
        for line in Path(args.source_list).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                sources.append(line)
    if not sources:
        log.error("Pass at least one --source / --source-list (a drive/folder to scan, "
                  "or a direct exportLibrary.db / export.pdb path).")
        sys.exit(1)

    db = FableGearDatabase()
    log.info("Scanning %d source location(s) for exported crates…", len(sources))
    report = R.recover(sources, database=db, strategy=getattr(args, "strategy", "richest"),
                       merge_numbered=getattr(args, "merge_duplicates", False))

    min_resolved = int(getattr(args, "min_resolved", 1))
    keep = [c for c in report.crates
            if sum(1 for t in c.tracks if t.content_id) >= min_resolved]
    log.info("Found %d export source(s).", len(report.sources))
    log.info("Recovered %d unique crate(s), %d track placement(s); %d/%d resolved (%d%%).",
             len(report.crates), report.resolution.total_tracks,
             report.resolution.resolved, report.resolution.total_tracks,
             100 * report.resolution.resolved // max(1, report.resolution.total_tracks))
    log.info("%d crate(s) meet --min-resolved=%d and will be rebuilt.", len(keep), min_resolved)

    # Report body
    lines = [f"# FableGear playlist recovery — {'WRITE' if getattr(args,'write',False) else 'DRY RUN'}",
             f"sources: {len(report.sources)} | crates: {len(report.crates)} | "
             f"resolved: {report.resolution.resolved}/{report.resolution.total_tracks}", ""]
    for c in keep:
        res = sum(1 for t in c.tracks if t.content_id)
        lines.append(f"[{res:>4}/{len(c.tracks):>4} resolved]  {c.name}")
    if getattr(args, "report", None):
        Path(args.report).write_text("\n".join(lines))
        log.info("Wrote report to %s", args.report)
    else:
        for ln in lines[3:23]:
            log.info("  %s", ln)
        if len(keep) > 20:
            log.info("  … and %d more (use --report FILE for the full list)", len(keep) - 20)

    if not getattr(args, "write", False):
        log.info("Dry run — nothing written. Re-run with --write to rebuild these into the archive.")
        return

    # ── Guarded write: everything under one removable folder ──
    # --replace: remove any prior "Recovered …" folders first (their crates are
    # a subset of this run). Delete children then the folder itself.
    if getattr(args, "replace", False):
        for pl in db.list_playlists():
            if pl.get("type") == "folder" and str(pl.get("name", "")).startswith("Recovered"):
                for child in db.get_playlist(pl["id"]).get("children", []) if db.get_playlist(pl["id"]) else []:
                    db.delete_playlist(child["id"])
                db.delete_playlist(pl["id"])
                log.info("Removed prior recovery folder %r (id=%s)", pl.get("name"), pl.get("id"))

    folder_name = f"Recovered {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    folder_id = db.create_playlist(folder_name, playlist_type="folder")
    created = [folder_id]
    crates_written = links_written = 0
    for c in keep:
        try:
            # dedupe resolved tracks within the crate, preserve order
            ids, seen = [], set()
            for t in c.tracks:
                if t.content_id is not None and t.content_id not in seen:
                    seen.add(t.content_id)
                    ids.append(t.content_id)
            if not ids:
                continue
            pid = db.create_playlist(c.name, parent_id=folder_id)
            created.append(pid)
            crates_written += 1
            # bulk insert — one executemany per crate instead of 5 queries/track
            with db.transaction() as conn:
                cur = conn.cursor()
                cur.executemany(
                    "INSERT INTO fg_playlist_song (playlist_id, content_id, track_number) VALUES (?,?,?)",
                    [(pid, cid, i) for i, cid in enumerate(ids, 1)],
                )
                cur.execute("UPDATE fg_playlist SET track_count = ? WHERE id = ?", (len(ids), pid))
            links_written += len(ids)
        except Exception as exc:  # noqa: BLE001 — one bad crate must not abort
            log.warning("  crate %r failed: %s", c.name, exc)
    try:
        db.log_operation("recover_playlists", file_path=folder_name, status="ok",
                         metadata={"folder_id": folder_id, "crates": crates_written,
                                   "links": links_written, "sources": len(report.sources),
                                   "created_playlist_ids": created})
    except Exception:  # noqa: BLE001
        pass
    log.info("Rebuilt %d crate(s) / %d track link(s) under folder %r (id=%s).",
             crates_written, links_written, folder_name, folder_id)
    log.info("To undo: delete the %r folder (or its playlist ids %s…).",
             folder_name, created[:3])


def cmd_parse(args: argparse.Namespace) -> None:
    """Parse — the Track Parsing tool.

    Prepares tracks so DJ gear (CDJ/XDJ/OPUS) can display and play them with
    full attributes: it identifies the BPM and musical key, builds the beat grid
    (tempo + downbeat phase), and generates the waveform analysis a player shows
    — the monochrome, colour, and 3-band scrolling waveforms + overviews. Results
    are stored in the library (beat grid in the DB, waveforms in a per-track
    analysis cache), so `export-onelibrary --with-anlz` is pure assembly and
    never re-analyzes.

    Uses BPM/key already in the library; only detects them when missing (or
    --force), applying octave correction. Detection writes file tags, so close
    Rekordbox first if you expect any track to need detection.
    """
    from fablegear_database import FableGearDatabase
    from fablegear_database.database import BeatGridRecord
    from pathlib import Path as _P
    import sys as _sys

    cs = _P(__file__).resolve().parent / "chop_shop"
    if str(cs) not in _sys.path:
        _sys.path.insert(0, str(cs))
    import waveform_generator as wg

    db = FableGearDatabase()
    all_tracks = db.get_content_with_relations(None)
    if getattr(args, "path", None) and not getattr(args, "all", False):
        src = str(_P(args.path).resolve())
        tracks = [t for t in all_tracks
                  if t.file_path and str(_P(t.file_path).resolve()).startswith(src)]
    else:
        tracks = all_tracks
    tracks = [t for t in tracks if t.file_path and _P(t.file_path).is_file()]
    tracks.sort(key=lambda t: t.id or 0)
    if not tracks:
        log.error("No tracks to parse (import them first).")
        sys.exit(1)

    do_wave = not getattr(args, "no_waveforms", False)
    force = getattr(args, "force", False)

    # Resume checkpoint (order-independent, by file path) — matches convert/
    # process, so an interrupted --all parse of a big library picks up where it
    # stopped instead of re-analyzing everything.
    ckpt_roots = [_P(args.path)] if getattr(args, "path", None) else [_P.home() / ".fablegear"]
    ckpt = _get_checkpoint("parse", ckpt_roots, args, {"waveforms": do_wave})
    done_paths: set = set()
    if ckpt is not None:
        saved = ckpt.load()
        if saved:
            done_paths = set(saved.get("done_paths", []))
            if done_paths and not force:
                log.info("Resuming: %d track(s) already parsed, skipping.", len(done_paths))

    def _save_ckpt() -> None:
        if ckpt is not None:
            ckpt.save({"done_paths": sorted(done_paths), "completed": len(done_paths),
                       "total": len(tracks)})

    log.info("Parsing %d track(s) for DJ-gear playback%s…",
             len(tracks), "" if do_wave else " (grids only)")

    ok = 0
    for i, t in enumerate(tracks, 1):
        name = t.file_name or str(t.id)
        if not force and t.file_path in done_paths:
            continue
        try:
            bpm, key = t.bpm, t.key
            if force or bpm is None or key is None:
                from audio_processor import process_file
                res = process_file(_P(t.file_path), detect_bpm=True, detect_key=True,
                                   normalise=False, force=force, fix_octaves=True)
                bpm = res.bpm_detected if res.bpm_detected is not None else bpm
                key = res.key_detected if res.key_detected is not None else key
                upd = {}
                if bpm is not None:
                    upd["bpm"] = bpm
                if key is not None:
                    upd["key"] = key
                if upd:
                    db.update_content(t.id, upd)

            # Beat grid: constant tempo from BPM, phase from downbeat estimate.
            if bpm and t.duration:
                offset = wg.estimate_first_beat_ms(t.file_path, bpm)
                beat_ms = 60000.0 / float(bpm)
                total = int((float(t.duration) * 1000.0 - offset) / beat_ms)
                grid = [BeatGridRecord(content_id=t.id, beat_number=(j % 4) + 1,
                                       time_msec=int(round(offset + j * beat_ms)),
                                       bpm=float(bpm))
                        for j in range(max(0, total))]
                db.bulk_upsert_beatgrids(t.id, grid)

            # Waveforms -> per-track analysis cache.
            n_cols = 0
            if do_wave:
                wf = wg.analyze_audio(t.file_path)
                wg.save_waveform_cache(t.id, wg.all_waveform_tags(wf))
                n_cols = wf.n_cols

            # Document the parse in the archive's audit trail.
            try:
                db.log_operation("parse", file_path=t.file_path, status="ok",
                                 metadata={"content_id": t.id, "bpm": bpm, "key": key,
                                           "grid_beats": len(db.get_beatgrid_for_content(t.id)),
                                           "waveform_cols": n_cols, "waveforms": do_wave})
            except Exception:  # noqa: BLE001 — logging must never fail the parse
                pass

            done_paths.add(t.file_path)
            if i % 10 == 0:
                _save_ckpt()
            ok += 1
            log.info("  [%d/%d] parsed %s (bpm=%s key=%s)", i, len(tracks), name, bpm, key)
        except Exception as exc:  # noqa: BLE001 — one bad track must not abort the run
            try:
                db.log_operation("parse", file_path=t.file_path, status="error",
                                 error_message=str(exc), metadata={"content_id": t.id})
            except Exception:  # noqa: BLE001
                pass
            log.warning("  [%d/%d] parse failed for %s: %s", i, len(tracks), name, exc)

    # Clean completion — clear the checkpoint so the next run starts fresh
    # (matches convert/process). An interrupted run leaves it saved for resume.
    if ckpt is not None:
        ckpt.reset()
    log.info("Parse complete: %d/%d track(s) ready for DJ gear "
             "(export with `export-onelibrary --with-anlz`).", ok, len(tracks))


def cmd_playlist(args: argparse.Namespace) -> None:
    """Create/list FableGear playlists. `create NAME --from-folder PATH` makes a
    playlist and adds every imported track whose file lives under PATH."""
    from fablegear_database import FableGearDatabase
    from pathlib import Path as _P

    db = FableGearDatabase()
    action = args.playlist_action

    if action == "list":
        for pl in db.list_playlists():
            kind = "folder" if pl.get("type") == "folder" else "playlist"
            log.info("  [%s] id=%s %r", kind, pl.get("id"), pl.get("name"))
        return

    if action == "create":
        name = args.name
        existing = {p["name"]: p for p in db.list_playlists() if p.get("type") != "folder"}
        if name in existing:
            pid = existing[name]["id"]
            log.info("Playlist %r already exists (id=%s) — reusing", name, pid)
        else:
            pid = db.create_playlist(name)
            log.info("Created playlist %r (id=%s)", name, pid)

        if getattr(args, "from_folder", None):
            src = str(_P(args.from_folder).resolve())
            tracks = [t for t in db.get_content_with_relations(None)
                      if t.file_path and str(_P(t.file_path).resolve()).startswith(src)]
            tracks.sort(key=lambda t: (t.file_name or "").lower())
            if not tracks:
                log.warning("No imported tracks found under %s — import first?", src)
            added = 0
            for t in tracks:
                try:
                    if db.add_song(pid, t.id):
                        added += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("  add %s failed: %s", t.id, exc)
            log.info("Added %d track(s) to %r (id=%s)", added, name, pid)
        return


def cmd_export_onelibrary(args: argparse.Namespace) -> None:
    """Write a Pioneer OneLibrary exportLibrary.db from FableGear's database,
    plus the device-identity companion files (RBFLTR.DAT, djprofile.nxs)
    that sit alongside it on a real device tree.

    Read-only against FableGear's own database; never touches Rekordbox or
    any existing Pioneer file. Refuses to overwrite an existing target.
    """
    from fablegear_database import FableGearDatabase
    from fablegear_database.onelibrary_writer import OneLibraryWriter
    from fablegear_database.device_identity import write_rbfltr, write_dj_profile

    target = Path(args.target)
    log.info("Writing OneLibrary export to: %s", target)

    device_name = getattr(args, "device_name", "") or "FableGear"
    dj_name = getattr(args, "dj_name", "") or "FableGear"

    fg_db = FableGearDatabase()

    # Resolve which tracks + playlists to export.
    playlist_ids = None
    content_ids = None
    if getattr(args, "content_ids", None):
        content_ids = [int(x) for x in str(args.content_ids).split(",") if x.strip()]
    if getattr(args, "playlist", None) or getattr(args, "playlist_id", None):
        want_id = getattr(args, "playlist_id", None)
        want_name = getattr(args, "playlist", None)
        match = None
        for pl in fg_db.list_playlists():
            if pl.get("type") == "folder":
                continue
            if (want_id is not None and pl.get("id") == int(want_id)) or \
               (want_name is not None and pl.get("name") == want_name):
                match = pl
                break
        if match is None:
            log.error("Playlist not found: %r", want_id if want_id is not None else want_name)
            sys.exit(1)
        playlist_ids = [match["id"]]
        if content_ids is None:
            content_ids = [s.id for s in fg_db.get_playlist_songs(match["id"])]
        log.info("Exporting playlist %r (%d tracks)", match["name"], len(content_ids))

    # Drive root for audio staging / ANLZ = the folder that holds PIONEER/.
    stage_root = None
    if getattr(args, "stage_audio", False):
        pr = target.parent.parent
        if pr.name == "PIONEER":
            stage_root = pr.parent
            log.info("Staging audio into %s/Contents/", stage_root)
        else:
            log.warning("--stage-audio ignored: target is not under PIONEER/rekordbox/")

    # --force: replace an existing export in place (removes the DB + its WAL/SHM
    # sidecars) so re-exporting to the same stick doesn't require a manual rm.
    if getattr(args, "force", False) and target.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(target) + suffix)
            try:
                if p.exists():
                    p.unlink()
            except OSError as exc:
                log.error("Could not remove existing %s: %s", p, exc)
                sys.exit(1)
        log.info("Replaced existing export at %s (--force)", target)

    try:
        result = OneLibraryWriter(fg_db).write(
            target,
            content_ids=content_ids,
            include_playlists=not getattr(args, "no_playlists", False),
            device_name=device_name,
            stage_audio_to=stage_root,
            playlist_ids=playlist_ids,
        )
    except FileExistsError as exc:
        log.error(str(exc))
        sys.exit(1)
    except Exception:
        log.exception("OneLibrary export failed")
        sys.exit(1)

    log.info("Export complete:")
    log.info("  Tracks written  : %d", result.tracks_written)
    log.info("  Tracks skipped  : %d", result.tracks_skipped)
    log.info("  Cues written    : %d", result.cues_written)
    log.info("  Playlists       : %d", result.playlists_written)
    log.info("  Playlist entries: %d", result.playlist_entries_written)
    if result.errors:
        log.warning("  Errors (%d):", len(result.errors))
        for err in result.errors[:20]:
            log.warning("    %s", err)
        if len(result.errors) > 20:
            log.warning("    ... and %d more", len(result.errors) - 20)

    # Document the export in the archive's audit trail — a gig stick should
    # leave a record of exactly what was written, when, and to where.
    try:
        fg_db.log_operation(
            "export_onelibrary", file_path=str(target), status="ok",
            metadata={
                "tracks_written": result.tracks_written,
                "tracks_skipped": result.tracks_skipped,
                "playlist": getattr(args, "playlist", None),
                "audio_staged": result.audio_files_copied,
                "audio_missing": result.audio_files_missing,
                "with_anlz": bool(getattr(args, "with_anlz", False)),
                "device_name": device_name,
                "errors": len(result.errors),
            },
        )
    except Exception:  # noqa: BLE001 — logging must never fail the export
        pass

    if not getattr(args, "no_identity_files", False):
        # target is expected to be .../PIONEER/rekordbox/exportLibrary.db —
        # derive the PIONEER/ root two levels up to place the companions
        # correctly. If the caller pointed target somewhere unconventional,
        # skip rather than guess at a wrong location.
        pioneer_root = target.parent.parent
        if pioneer_root.name == "PIONEER":
            write_rbfltr(pioneer_root)
            write_dj_profile(pioneer_root, display_name=dj_name)
            log.info("  Device identity : RBFLTR.DAT + djprofile.nxs (%s) written", dj_name)
        else:
            log.warning(
                "  Skipped device-identity files — target's grandparent "
                "directory is %r, not 'PIONEER'. Pass a target like "
                ".../PIONEER/rekordbox/exportLibrary.db for these to be "
                "placed automatically, or use --no-identity-files to silence "
                "this note.", pioneer_root.name,
            )

    log.warning(
        "NOTE: none of this has been validated on physical Pioneer hardware. "
        "Test on a sacrificial USB stick — never a gig stick — and keep a "
        "Rekordbox-made control stick until trust is earned."
    )
    if getattr(args, "with_anlz", False):
        _generate_export_anlz(fg_db, target, result)
    elif not getattr(args, "no_anlz_note", False):
        log.info(
            "No ANLZ files written (pass --with-anlz to generate beat grids + "
            "waveforms). Without them the CDJ re-analyzes on load."
        )


def _generate_export_anlz(fg_db, target: Path, result) -> None:
    """Generate ANLZ (.DAT/.EXT/.2EX with grids + waveforms) for every track in
    a just-written OneLibrary export, keyed to the same content_id its
    analysisDataFilePath points at. Best-effort per track."""
    from fablegear_database.exporter import PioneerExporter

    pr = target.parent.parent
    if pr.name != "PIONEER":
        log.warning("Skipping ANLZ: target not under PIONEER/rekordbox/ (%s)", target)
        return
    drive_root = pr.parent

    # content.path per content_id, read back from the encrypted DB we just wrote.
    path_by_cid = {}
    try:
        import sqlcipher3
        from fablegear_database.onelibrary_writer import _ONELIBRARY_KEY, _CIPHER_COMPATIBILITY
        conn = sqlcipher3.connect(str(target))
        cur = conn.cursor()
        cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
        cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
        path_by_cid = {cid: p for cid, p in cur.execute("SELECT content_id, path FROM content")}
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read back content paths for ANLZ (%s); PPTH may be blank", exc)

    exporter = PioneerExporter(fg_db)
    ok = 0
    total = len(result.content_id_map)
    log.info("Generating ANLZ (grids + waveforms) for %d track(s)…", total)
    for fg_id, content_id in result.content_id_map.items():
        rel = path_by_cid.get(content_id, "")
        try:
            if exporter.export_track_anlz(content_id=fg_id, target_root=drive_root,
                                          relative_audio_path=rel, device_content_id=content_id):
                ok += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("  ANLZ for content %s failed: %s", content_id, exc)
    log.info("  ANLZ written for %d/%d track(s)", ok, total)


def cmd_rekordbox_dedupe(args: argparse.Namespace) -> None:
    """Scan only Rekordbox library tracks for duplicates, then optionally prune + rethread playlists."""
    from config import AUDIO_EXTENSIONS
    from db_connection import read_db, write_db
    from duplicate_detector import scan_duplicates, write_csv_report, write_trash_rescue_report
    from pruner import load_report, prune_files

    db_path = _resolve_active_db_path(getattr(args, "db_path", None))
    workers = max(1, int(getattr(args, "workers", 1) or 1))
    match_mode = getattr(args, "match_mode", "exact")
    fuzzy_threshold = float(getattr(args, "fuzzy_threshold", 0.85))

    log.info("Rekordbox dedupe scan using DB: %s", db_path)
    try:
        with read_db(db_path) as db:
            rows = db.get_content().all()
            db_paths = [Path(str(row.FolderPath)) for row in rows if getattr(row, "FolderPath", None)]
    except Exception:
        log.exception("Failed to read Rekordbox content list")
        sys.exit(1)

    seen: set[Path] = set()
    scan_files: list[Path] = []
    missing_on_disk = 0
    non_audio_paths = 0
    for p in db_paths:
        rp = p.expanduser()
        if rp in seen:
            continue
        seen.add(rp)
        if rp.suffix.lower() not in AUDIO_EXTENSIONS:
            non_audio_paths += 1
            continue
        if not rp.exists():
            missing_on_disk += 1
            continue
        scan_files.append(rp)

    if not scan_files:
        _emit_report(
            "No on-disk audio files were found from the Rekordbox library paths.",
            "Rekordbox Dedupe",
            f"rekordbox_dedupe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        return

    output = Path(args.output).expanduser() if getattr(args, "output", None) else None
    if output is None:
        try:
            try:
                from FableGear.config import REPORTS_DIR  # noqa: PLC0415
            except ImportError:
                from config import REPORTS_DIR  # noqa: PLC0415
            out_dir = REPORTS_DIR / "Duplicates"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = out_dir / f"rekordbox_duplicate_report_{stamp}.csv"
        except Exception:
            output = Path.home() / ".fablegear" / "Reports" / "Duplicates" / "rekordbox_duplicate_report.csv"

    rescue_output = output.with_name(
        output.stem.replace("duplicate_report", "trash_rescue_report")
        if "duplicate_report" in output.stem
        else f"trash_rescue_{output.stem}"
    ).with_suffix(".txt")

    log.info(
        "Scanning %d Rekordbox-referenced files (missing=%d non-audio=%d, workers=%d, match=%s)",
        len(scan_files), missing_on_disk, non_audio_paths, workers, match_mode,
    )
    archive = _require_archive("rekordbox-dedupe")
    try:
        result = scan_duplicates(
            root=scan_files[0].parent,
            files_override=scan_files,
            max_workers=workers,
            match_mode=match_mode,
            fuzzy_threshold=fuzzy_threshold,
            archive=archive,
        )
    except Exception:
        log.exception("Rekordbox duplicate scan failed")
        sys.exit(1)

    if result.groups or result.unique_in_trash:
        try:
            if result.groups:
                write_csv_report(result, output)
            write_trash_rescue_report(result, rescue_output)
        except Exception:
            log.exception("Failed to write Rekordbox dedupe reports")
            sys.exit(1)

    if not result.groups:
        lines = [
            "No duplicate groups found among Rekordbox-referenced files.",
            f"Scanned files        : {len(scan_files)}",
            f"Missing on disk      : {missing_on_disk}",
            f"Non-audio DB entries : {non_audio_paths}",
        ]
        if result.unique_in_trash:
            lines.append(f"Trash rescue report  : {rescue_output}")
        _emit_report(
            "\n".join(lines),
            "Rekordbox Dedupe",
            f"rekordbox_dedupe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        return

    try:
        with read_db(db_path) as db:
            groups = load_report(output, db)
    except Exception:
        log.exception("Failed to load Rekordbox duplicate report for pruning")
        sys.exit(1)

    remove_paths: list[str] = []
    keeper_map: dict[str, str] = {}
    locked_groups = 0
    for g in groups:
        cands = g.remove_candidates
        if not cands:
            if g.keep_in_trash:
                locked_groups += 1
            continue
        keep = g.keep
        for e in cands:
            remove_paths.append(e.file_path)
            if keep:
                keeper_map[e.file_path] = keep.file_path

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not remove_paths:
        _emit_report(
            "No prunable duplicates found in Rekordbox library scan.\n"
            f"  Duplicate groups               : {len(groups)}\n"
            f"  Locked (keeper in trash)       : {locked_groups}\n"
            f"  Duplicate report               : {output}\n"
            f"  Trash rescue report            : {rescue_output}",
            "Rekordbox Dedupe",
            f"rekordbox_dedupe_{timestamp}.txt",
        )
        return

    if args.dry_run:
        lines = [
            "DRY RUN — no DB rows or files were modified.",
            "",
            f"DB path              : {db_path}",
            f"Scanned files        : {len(scan_files)}",
            f"Duplicate groups     : {len(groups)}",
            f"Would remove files   : {len(remove_paths)}",
            f"Playlist rewires     : up to {len(keeper_map)} duplicate references",
            f"Duplicate report     : {output}",
            f"Trash rescue report  : {rescue_output}",
        ]
        if locked_groups:
            lines.append(f"Locked groups        : {locked_groups} (keeper in trash)")
        _emit_report("\n".join(lines), "Rekordbox Dedupe", f"rekordbox_dedupe_{timestamp}.txt")
        return

    log.info("Pruning %d Rekordbox duplicate files from %s", len(remove_paths), db_path)
    try:
        with write_db(db_path) as db:
            summary = prune_files(
                remove_paths,
                db,
                log=lambda m: print(m, flush=True),
                permanent=args.permanent,
                keeper_map=keeper_map,
                archive=archive,
            )
    except Exception:
        log.exception("Rekordbox dedupe prune failed")
        sys.exit(1)

    lines = [
        "Rekordbox dedupe complete.",
        "",
        f"  DB path               : {db_path}",
        f"  DB entries removed    : {summary.get('db_removed', 0)}",
        f"  Files moved to trash  : {summary.get('files_moved', 0)}",
        f"  Playlists re-threaded : {summary.get('playlists_rethreaded', 0)}",
        f"  Skipped               : {summary.get('skipped', 0)}",
        f"  Duplicate report      : {output}",
        f"  Trash rescue report   : {rescue_output}",
    ]
    if summary.get("trash_dir"):
        lines.append(f"  Recovery folder       : {summary['trash_dir']}")
    errs = summary.get("errors") or []
    if errs:
        lines += ["", f"{len(errs)} error(s):"] + [f"  {e}" for e in errs[:50]]
    _emit_report("\n".join(lines), "Rekordbox Dedupe", f"rekordbox_dedupe_{timestamp}.txt")


def cmd_process(args: argparse.Namespace) -> None:
    """
    Detect BPM/key and normalise loudness for audio files under PATH.

    Dry-run behavior:
      --dry-run suppresses loudness normalisation (audio file modification).
      BPM and key detection still run and tag values are still written.
      To skip tag writes as well, combine: --no-bpm --no-key --no-normalize.

    --paths-file mode:
      When --paths-file is supplied, only the specific files listed in that
      file are processed — no directory scan occurs. PATH arg is still required
      by argparse but is not used as a scan root in this mode.
    """
    from audio_processor import process_directory, process_file, is_corrupt, quarantine_file
    import json as _json

    paths_file = getattr(args, "paths_file", None)

    # ── Specific-file retry mode ────────────────────────────────────────────
    if paths_file:
        pf = Path(paths_file)
        if not pf.exists():
            log.error("--paths-file not found: %s", pf)
            sys.exit(1)
        specific_paths = [Path(ln.strip()) for ln in pf.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not specific_paths:
            log.error("--paths-file is empty: %s", pf)
            sys.exit(1)

        detect_bpm = not args.no_bpm
        detect_key = not args.no_key
        normalise = not args.no_normalize and not args.dry_run

        try:
            from config import QUARANTINE_DIR as _cfg_quarantine
            _quarantine_dir = _cfg_quarantine
        except Exception:
            _quarantine_dir = specific_paths[0].parent / "QUARANTINE"

        # Checkpoint: only when this retry-mode entry is the Smart Skip
        # pre-filter's own recursion (args._smart_skip_roots is set by that
        # block just before it calls cmd_process() again) — a genuine
        # explicit "retry these specific files" call (process-retry route)
        # never sets this and must always reprocess exactly what it's given.
        _smart_skip_origin_roots = getattr(args, "_smart_skip_roots", None)
        ckpt = None
        ckpt_done_paths: set[str] = set()
        if _smart_skip_origin_roots:
            ckpt_roots = [r for r, _count in _smart_skip_origin_roots]
            ckpt = _get_checkpoint("process", ckpt_roots, args, {
                "bpm": detect_bpm, "key": detect_key, "normalize": normalise,
                "enrich": bool(getattr(args, "enrich_tags", False)),
                "force": bool(getattr(args, "force", False)),
            })
            if ckpt is not None:
                saved = ckpt.load()
                if saved:
                    ckpt_done_paths = set(saved.get("done_paths", []))
                    if ckpt_done_paths:
                        before = len(specific_paths)
                        specific_paths = [p for p in specific_paths if str(p) not in ckpt_done_paths]
                        log.info(
                            "Resuming: %d/%d files already done per checkpoint, skipping those.",
                            before - len(specific_paths), before,
                        )

        log.info(
            "Retry mode: processing %d specific file(s) — BPM:%s KEY:%s NORMALIZE:%s FORCE:%s",
            len(specific_paths), detect_bpm, detect_key, normalise, args.force,
        )

        all_results = []
        total = len(specific_paths)
        done = clean = errors = edited = tags_written = bpm_key_written = quarantined = enriched = 0
        _ckpt_save_counter = 0

        for i, path in enumerate(specific_paths, start=1):
            if not path.exists():
                log.warning("[%d/%d] Not found — skipping: %s", i, total, path)
                continue
            r = process_file(
                path,
                detect_bpm=detect_bpm,
                detect_key=detect_key,
                normalise=normalise,
                force=True,   # always force in retry mode
                enrich_tags=getattr(args, "enrich_tags", False),
            )
            if r.errors:
                errors += 1
                log.info("[%d/%d] %s  ✗ %s", i, total, path.name, "; ".join(r.errors))
            else:
                log.info("[%d/%d] %s", i, total, path.name)
                if ckpt is not None:
                    ckpt_done_paths.add(str(path))

            if is_corrupt(r):
                quarantine_file(r, _quarantine_dir)
                quarantined += 1

            any_edit = r.bpm_written or r.key_written or r.normalised
            if any_edit:
                edited += 1
                if r.bpm_written or r.key_written:
                    bpm_key_written += 1
                tags_written += 1
            elif r.ok:
                clean += 1
            done += 1

            if ckpt is not None:
                _ckpt_save_counter += 1
                if _ckpt_save_counter % 25 == 0:
                    ckpt.save({"done_paths": sorted(ckpt_done_paths), "completed": len(ckpt_done_paths)})

            print(
                "FABLEGEAR_PROGRESS: " + _json.dumps({
                    "done": done, "total": total, "remaining": total - done,
                    "clean": clean, "errors": errors, "edited": edited,
                    "tags_written": tags_written, "bpm_key_written": bpm_key_written,
                    "quarantined": quarantined, "enriched": enriched,
                }),
                flush=True,
            )
            all_results.append(r)

        # Clean completion of the Smart Skip retry pass — clear the checkpoint.
        if ckpt is not None:
            ckpt.reset()

        # Build root_sections from smart-skip metadata if available,
        # otherwise fall back to a single entry for retry mode.
        smart_roots = getattr(args, "_smart_skip_roots", None)
        if smart_roots and len(smart_roots) > 0:
            root_sections: list[tuple[Path, str]] = [
                (root, f"{count} file(s) needed tagging.")
                for root, count in smart_roots
            ]
        else:
            root_sections: list[tuple[Path, str]] = [
                (specific_paths[0].parent,
                 f"{total} file(s) retried.  {total - errors} OK, {errors} still errored.")
            ]
        # Patch args so the shared report block below works unchanged
        args._all_results_override = all_results
        args._root_sections_override = root_sections
        args._quarantine_dir_override = _quarantine_dir
        # Fall through to shared report section below
        _run_shared_report(args, all_results, root_sections, _quarantine_dir)
        return

    # ── Normal directory-scan mode ──────────────────────────────────────────
    # Build the list of roots: primary path + any --also-scan additions.
    roots: list[Path] = [Path(args.path)]
    for extra in (getattr(args, "also_scan", None) or []):
        p = Path(extra)
        if p not in roots:
            roots.append(p)

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    _guard_or_exit(roots, "the track tagger")

    detect_bpm = not args.no_bpm
    detect_key = not args.no_key

    # ── Smart-skip pre-filter ───────────────────────────────────────────────
    # When --smart-skip is set, scan each root and collect only files that
    # actually need work.  This runs inside the subprocess so it appears in
    # the log stream, is cancellable via the normal interrupt path, and
    # emits per-file scan progress to keep the UI alive.
    if getattr(args, "smart_skip", False) and (detect_bpm or detect_key):
        from scanner import scan_directory  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        pending: list[Path] = []
        pending_per_root: dict[Path, int] = {}
        total_scanned = 0
        skipped_complete = 0
        for root in roots:
            root_pending = 0
            for track in scan_directory(root):
                total_scanned += 1
                needs_bpm = detect_bpm and track.bpm is None
                needs_key = detect_key and track.key is None
                if needs_bpm or needs_key:
                    pending.append(track.path)
                    root_pending += 1
                else:
                    skipped_complete += 1
                print(
                    "FABLEGEAR_PROGRESS: " + _json.dumps({"scanned": total_scanned}),
                    flush=True,
                )
            pending_per_root[root] = root_pending
        log.info(
            "Smart Skip: %d/%d file(s) need work; %d already complete and skipped.",
            len(pending), total_scanned, skipped_complete,
        )
        if not pending:
            print("Smart Skip: all files already tagged — nothing to do.")
            return
        # Stash per-root counts so the report can show which sources were scanned
        args._smart_skip_roots = [
            (root, count) for root, count in pending_per_root.items() if count > 0
        ]
        args._smart_skip_skipped = skipped_complete
        # Reuse the paths-file branch: write pending list and re-enter
        import tempfile as _tf  # noqa: PLC0415
        with _tf.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="fablegear_smart_skip_",
            delete=False, encoding="utf-8",
        ) as f:
            f.write("\n".join(str(p) for p in pending))
            tmp_path = Path(f.name)
        args.paths_file = str(tmp_path)
        args.smart_skip = False  # prevent infinite recursion
        cmd_process(args)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return
    normalise = not args.no_normalize and not args.dry_run

    log.info(
        "Processing %d root(s) — BPM:%s KEY:%s NORMALIZE:%s FORCE:%s DRY_RUN:%s",
        len(roots), detect_bpm, detect_key, normalise, args.force, args.dry_run,
    )

    if args.dry_run:
        log.info(
            "DRY RUN — loudness normalisation suppressed. "
            "BPM/key tag writes will still occur unless --no-bpm / --no-key are set."
        )

    if normalise:
        log.warning(
            "Normalisation will modify audio files in-place. "
            "Originals are backed up as .bak during the operation only. "
            "Ensure your files are backed up independently before proceeding."
        )

    # Determine quarantine directory: one level above the first root, named QUARANTINE
    try:
        from config import QUARANTINE_DIR as _cfg_quarantine
        _quarantine_dir = _cfg_quarantine
    except Exception:
        _quarantine_dir = roots[0].parent / "QUARANTINE"

    # Checkpoint: remember which files were cleanly processed (no errors) so
    # an interrupted run can resume without redoing BPM/key detection or —
    # more importantly — re-normalising files that were already normalised.
    ckpt = _get_checkpoint("process", roots, args, {
        "bpm": detect_bpm, "key": detect_key, "normalize": normalise,
        "enrich": bool(args.enrich_tags), "force": bool(args.force),
    })
    ckpt_done_paths: set[str] = set()
    if ckpt is not None:
        saved = ckpt.load()
        if saved:
            ckpt_done_paths = set(saved.get("done_paths", []))

    _ckpt_save_counter = 0

    def _on_result(r) -> None:
        nonlocal _ckpt_save_counter
        if ckpt is None:
            return
        if r.ok and not r.errors:
            ckpt_done_paths.add(str(r.path))
        _ckpt_save_counter += 1
        if _ckpt_save_counter % 25 == 0:
            ckpt.save({"done_paths": sorted(ckpt_done_paths), "completed": len(ckpt_done_paths)})

    all_results = []
    root_sections: list[tuple[Path, str]] = []
    for index, root in enumerate(roots, start=1):
        _log_root_step("Process", root, index, len(roots))
        try:
            already_done = 0
            if ckpt_done_paths:
                from scanner import scan_directory  # noqa: PLC0415
                already_done = len([1 for t in scan_directory(root) if str(t.path) in ckpt_done_paths])
            results = process_directory(
                root,
                detect_bpm=detect_bpm,
                detect_key=detect_key,
                normalise=normalise,
                force=args.force,
                force_bpm=getattr(args, "force_bpm", False),
                force_key=getattr(args, "force_key", False),
                fix_octaves=getattr(args, "fix_octaves", False),
                max_workers=max(1, args.workers),
                quarantine_dir=_quarantine_dir,
                enrich_tags=args.enrich_tags,
                skip_paths=ckpt_done_paths or None,
                on_result=_on_result,
            )
            all_results.extend(results)
            # Persist this root's analysis immediately — a multi-drive run
            # interrupted on drive 3 must keep drives 1-2 in the archive.
            _persist_process_results(results, _archive())
            root_total = len(results) + already_done
            root_bpm_written = sum(1 for r in results if r.bpm_written)
            root_key_written = sum(1 for r in results if r.key_written)
            root_normalised = sum(1 for r in results if r.normalised)
            root_errored = sum(1 for r in results if not r.ok)
            root_quarantined = sum(1 for r in results if r.quarantined)
            root_skipped_bpm = sum(1 for r in results if r.skipped_bpm)
            root_skipped_key = sum(1 for r in results if r.skipped_key)
            root_lines = [f"{root_total} files were analyzed."]
            if already_done:
                root_lines.append(f"{already_done} already done per checkpoint — skipped.")
            if detect_bpm:
                root_lines.append(
                    f"BPM written: {root_bpm_written}.{f' {root_skipped_bpm} already had one.' if root_skipped_bpm else ''}"
                )
            if detect_key:
                root_lines.append(
                    f"Key written: {root_key_written}.{f' {root_skipped_key} already had one.' if root_skipped_key else ''}"
                )
            if normalise:
                root_lines.append(f"Loudness adjusted: {root_normalised} files.")
            if root_quarantined:
                root_lines.append(f"{root_quarantined} corrupt files moved to QUARANTINE — see report.")
            if root_errored:
                root_lines.append(f"{root_errored} files had errors — check the log above.")
            root_sections.append((root, "\n".join(root_lines)))
        except Exception:
            log.exception("Processing failed for %s", root)
            sys.exit(1)

    # Clean completion — clear the checkpoint so the next run starts fresh.
    if ckpt is not None:
        ckpt.reset()

    total = len(all_results)
    bpm_written = sum(1 for r in all_results if r.bpm_written)
    key_written = sum(1 for r in all_results if r.key_written)
    normalised = sum(1 for r in all_results if r.normalised)
    errored = sum(1 for r in all_results if not r.ok)
    quarantined_results = [r for r in all_results if r.quarantined]
    quarantined = len(quarantined_results)
    skipped_bpm = sum(1 for r in all_results if r.skipped_bpm)
    skipped_key = sum(1 for r in all_results if r.skipped_key)

    if normalise and not detect_bpm and not detect_key:
        # Normalize-only mode
        report_lines = [
            "\nDone.\n",
            f"{normalised} tracks were re-encoded to match the loudness target.",
            f"{total - normalised - errored} were already at the right level and skipped.",
        ]
    elif normalise:
        # Full process mode
        report_lines = [
            "\nDone.\n",
            f"{total} files were analyzed.",
            f"  BPM written: {bpm_written} files.{f'  {skipped_bpm} already had one.' if skipped_bpm else ''}",
            f"  Key written: {key_written} files.{f'  {skipped_key} already had one.' if skipped_key else ''}",
            f"  Loudness adjusted: {normalised} files.",
        ]
    else:
        # Tag-only mode
        report_lines = [
            "\nDone tagging.\n",
            f"{total} files were analyzed.",
            f"  BPM written: {bpm_written} files.{f'  {skipped_bpm} already had one.' if skipped_bpm else ''}",
            f"  Key written: {key_written} files.{f'  {skipped_key} already had one.' if skipped_key else ''}",
        ]
        enrich_written = sum(1 for r in all_results if getattr(r, 'enrich_written', False))
        if args.enrich_tags and enrich_written:
            report_lines.append(f"  MusicBrainz enriched: {enrich_written} files.")

    # ── Error breakdown — shared across all modes ────────────────────────────
    if errored:
        # Categorise failures
        corrupt_results  = [r for r in all_results if r.quarantined]
        decode_results   = [r for r in all_results
                            if not r.quarantined and not r.ok
                            and any("audio decode failed" in e for e in r.errors)]
        tag_fail_results = [r for r in all_results
                            if not r.quarantined and not r.ok
                            and not any("audio decode failed" in e for e in r.errors)
                            and any("tag write failed" in e or "normalisation failed" in e
                                    for e in r.errors)]
        other_results    = [r for r in all_results
                            if not r.quarantined and not r.ok
                            and not any("audio decode failed" in e for e in r.errors)
                            and not any("tag write failed" in e or "normalisation failed" in e
                                        for e in r.errors)]

        report_lines.append(f"\n{'─' * 60}")
        report_lines.append(f"ERRORS  —  {errored} file(s) could not be fully processed\n")

        if decode_results:
            report_lines.append(f"  ⚠  Audio Decode Failures ({len(decode_results)})")
            report_lines.append("     File opened, but audio couldn't be decoded — BPM/key were skipped.")
            report_lines.append("     → Convert these files to MP3 or AIFF first, then re-run Tag Tracks.")
            _MAX = 12
            for r in decode_results[:_MAX]:
                report_lines.append(f"       • {r.path.name}")
            if len(decode_results) > _MAX:
                report_lines.append(f"       … and {len(decode_results) - _MAX} more — see log for full list")
            report_lines.append("")

        if tag_fail_results:
            report_lines.append(f"  ⚠  Tag Write Failures ({len(tag_fail_results)})")
            report_lines.append("     BPM/key detection succeeded, but writing the tag to the file failed.")
            report_lines.append("     → Check file is not read-only, then re-run with Force tag-overwrite on.")
            for r in tag_fail_results[:12]:
                err_short = next(
                    (e for e in r.errors if "tag write failed" in e or "normalisation failed" in e),
                    r.errors[0] if r.errors else "unknown",
                )
                report_lines.append(f"       • {r.path.name}  [{err_short}]")
            if len(tag_fail_results) > 12:
                report_lines.append(f"       … and {len(tag_fail_results) - 12} more")
            report_lines.append("")

        if corrupt_results:
            report_lines.append(f"  ✗  Corrupt / Unreadable — moved to Quarantine ({len(corrupt_results)})")
            report_lines.append("     These files could not be opened at the audio-library level.")
            report_lines.append(f"     Location: {_quarantine_dir}")
            report_lines.append("     → Inspect in the Quarantine folder. Delete or restore manually.")
            for r in corrupt_results[:12]:
                report_lines.append(f"       • {r.path.name}")
            if len(corrupt_results) > 12:
                report_lines.append(f"       … and {len(corrupt_results) - 12} more")
            report_lines.append("")

        if other_results:
            report_lines.append(f"  ⚠  Other Errors ({len(other_results)})")
            for r in other_results[:12]:
                err_short = r.errors[0] if r.errors else "unknown error"
                report_lines.append(f"       • {r.path.name}  [{err_short}]")
            if len(other_results) > 12:
                report_lines.append(f"       … and {len(other_results) - 12} more")
            report_lines.append("")

    # Quarantine section removed — now folded into the error breakdown above.
    # Kept as a fallback for the case where quarantined files were already
    # counted but no error records exist (should not happen, but defensive).
    if quarantined and not errored:
        report_lines.append(
            f"\n{'─' * 60}\n"
            f"QUARANTINED: {quarantined} corrupt file(s) moved to:\n"
            f"  {_quarantine_dir}\n"
        )
        for r in quarantined_results:
            report_lines.append(f"  {r.path.name}")
        report_lines.append("")

    report_text = _append_root_breakdown("\n".join(report_lines), root_sections)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if normalise:
        _emit_report(report_text, "Normalize", f"normalize_{timestamp}.txt")
    else:
        _emit_report(report_text, "Tag Tracks", f"tag_tracks_{timestamp}.txt")

    if errored > 0:
        log.warning("%d files had errors — check log above", errored)


def cmd_convert(args: argparse.Namespace) -> None:
    """Convert audio files to target format (mp3, wav, aif, flac) across one or more source paths."""
    import concurrent.futures
    import json
    from pathlib import Path
    from audio_processor import _convert_file
    from scanner import scan_directory

    roots: list[Path] = [Path(args.path)]
    for extra in (getattr(args, "also_scan", None) or []):
        p = Path(extra)
        if p not in roots:
            roots.append(p)

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    _guard_or_exit(roots, "the converter")

    target_format = args.format.lower().lstrip(".")
    if target_format not in ("mp3", "wav", "aif", "aiff", "flac"):
        log.error("Unsupported format: %s", args.format)
        sys.exit(1)

    # Normalize aif → aiff
    if target_format == "aif":
        target_format = "aiff"

    max_workers = max(1, getattr(args, "workers", 1))
    log.info("Converting audio files to %s across %d source folder(s) (workers=%d)", target_format, len(roots), max_workers)

    tracks_by_root: list[tuple[Path, list]] = []
    total = 0
    for root in roots:
        root_tracks = list(scan_directory(root))
        tracks_by_root.append((root, root_tracks))
        total += len(root_tracks)

    log.info("Found %d audio files", total)

    if not total:
        log.warning("No audio files found")
        return

    # ── Checkpoint: resume by path (not index) — directory listing order
    # isn't guaranteed stable run to run, so a saved index-slice could skip
    # the wrong files. A completed-paths set is order-independent instead.
    ckpt = _get_checkpoint("convert", roots, args, {"format": target_format})
    ckpt_done_paths: set[str] = set()
    if ckpt is not None:
        saved = ckpt.load()
        if saved:
            ckpt_done_paths = set(saved.get("done_paths", []))
            if ckpt_done_paths:
                log.info(
                    "Resuming: %d/%d files already converted, skipping those.",
                    len(ckpt_done_paths), total,
                )

    def _save_ckpt_now() -> None:
        if ckpt is not None:
            ckpt.save({"done_paths": sorted(ckpt_done_paths), "completed": len(ckpt_done_paths), "total": total})

    done = 0
    converted_count = 0   # files actually converted   → footer "edited"
    skipped_count = 0     # nothing to do (already the target, or target exists) → footer "clean"
    error_count = 0       # genuine failures — corrupt or DRM-protected inputs
    root_sections: list[tuple[Path, str]] = []

    def _classify(ok: bool, msg: str) -> str:
        """Bucket a per-file result: 'converted' (file changed), 'skipped'
        (already the target format, or an MP3 already exists — nothing to do and
        nothing lost), or 'error' (a real decode/convert failure)."""
        if ok:
            return "skipped" if msg.startswith("Already") else "converted"
        return "skipped" if msg.lower().endswith("already exists") else "error"

    def _emit_progress() -> None:
        print(
            "FABLEGEAR_PROGRESS: " + json.dumps({
                "done":      done,
                "total":     total,
                "remaining": total - done,
                "edited":    converted_count,   # live-counts converted files in the footer
                "clean":     skipped_count,     # live-counts skipped files in the footer
                "errors":    error_count,
                "converted": converted_count,   # kept for the report / back-compat
                "skipped":   skipped_count,
            }),
            flush=True,
        )

    def _convert_one(track) -> tuple[bool, str, str]:
        ok, msg = _convert_file(track.path, target_format)
        _journal_convert(track.path, ok, msg)
        return ok, msg, track.path.name

    _fg_archive = _archive()
    _target_ext = ".aiff" if target_format == "aiff" else f".{target_format}"

    def _journal_convert(src_path: Path, ok: bool, msg: str) -> None:
        """Journal each conversion the moment it lands — the original file is
        replaced, so an interrupted run must still know what was converted."""
        if _fg_archive is None or not ok or msg.startswith("Already"):
            return
        dest = src_path.with_suffix(_target_ext)
        try:
            rec = _fg_archive.get_content_by_path(str(src_path))
            if rec and rec.id is not None:
                # Rekordbox-style relocate + refresh: keep the row (tags/cues/
                # playlists/id) but repoint it at the new file and refresh
                # file_size/file_hash + clear the now-stale acoustic fingerprint.
                _fg_archive.relink_converted(rec.id, str(dest))
            _fg_archive.log_operation(
                "convert", str(dest), status="ok",
                metadata={"from": str(src_path), "format": target_format},
            )
        except Exception as exc:
            log.warning("Archive update failed for convert %s: %s", src_path, exc)

    _emit_progress()

    for root_index, (root, tracks) in enumerate(tracks_by_root, start=1):
        _log_root_step("Convert", root, root_index, len(tracks_by_root))
        root_converted = 0
        root_skipped = 0
        root_errors = 0
        root_total = len(tracks)

        # Checkpoint resume: files already recorded as done in a previous run
        # are skipped outright — no restat, no ffmpeg invocation — rather than
        # re-scanned and re-classified as "already converted" every time.
        already_done = [t for t in tracks if str(t.path) in ckpt_done_paths]
        to_process = [t for t in tracks if str(t.path) not in ckpt_done_paths]
        if already_done:
            done += len(already_done)
            skipped_count += len(already_done)
            root_skipped += len(already_done)
            log.info("%d file(s) already converted per checkpoint — skipped.", len(already_done))
            _emit_progress()
        tracks = to_process

        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_convert_one, track): track for track in tracks}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        ok, msg, name = future.result()
                    except Exception as exc:
                        ok, msg, name = False, str(exc), futures[future].path.name
                    done += 1
                    kind = _classify(ok, msg)
                    if kind == "converted":
                        converted_count += 1
                        root_converted += 1
                        ckpt_done_paths.add(str(futures[future].path))
                        log.info("✓ %s: %s", name, msg)
                    elif kind == "skipped":
                        skipped_count += 1
                        root_skipped += 1
                        ckpt_done_paths.add(str(futures[future].path))
                        log.info("• %s: %s", name, msg)
                    else:
                        error_count += 1
                        root_errors += 1
                        log.error("✗ %s: %s", name, msg)
                    if done % 25 == 0:
                        _save_ckpt_now()
                    _emit_progress()
        else:
            for track_index, track in enumerate(tracks, start=1):
                log.info("[%d/%d] Converting %s", track_index, root_total, track.path.name)
                ok, msg = _convert_file(track.path, target_format)
                _journal_convert(track.path, ok, msg)
                done += 1
                kind = _classify(ok, msg)
                if kind == "converted":
                    converted_count += 1
                    root_converted += 1
                    ckpt_done_paths.add(str(track.path))
                    log.info("✓ %s: %s", track.path.name, msg)
                elif kind == "skipped":
                    skipped_count += 1
                    root_skipped += 1
                    ckpt_done_paths.add(str(track.path))
                    log.info("• %s: %s", track.path.name, msg)
                else:
                    error_count += 1
                    root_errors += 1
                    log.error("✗ %s: %s", track.path.name, msg)
                if done % 25 == 0:
                    _save_ckpt_now()
                _emit_progress()

        root_lines = [f"{root_converted} of {root_total} files converted to {target_format.upper()}."]
        if root_skipped:
            root_lines.append(f"{root_skipped} skipped (already {target_format.upper()} or already present).")
        if root_errors:
            root_lines.append(f"{root_errors} could not be converted (corrupt or DRM-protected input).")
        else:
            root_lines.append("No errors.")
        root_sections.append((root, "\n".join(root_lines)))

    # Clean completion (every root's file list fully processed) — clear the
    # checkpoint so the next run starts fresh rather than finding a stale
    # "resume" state for what is, from here on, a finished job.
    if ckpt is not None:
        ckpt.reset()

    if _fg_archive is not None and converted_count:
        try:
            _fg_archive.log_operation(
                "convert_batch",
                metadata={
                    "roots": [str(r) for r in roots],
                    "format": target_format,
                    "converted": converted_count,
                    "skipped": skipped_count,
                    "errors": error_count,
                },
            )
        except Exception as exc:
            log.warning("Archive batch log failed for convert: %s", exc)

    fmt_upper = target_format.upper()
    lines = ["Done converting.", "", f"{converted_count} of {total} files converted to {fmt_upper}."]
    if skipped_count:
        lines.append(f"{skipped_count} skipped — already {fmt_upper}, or an {fmt_upper} already exists (nothing lost).")
    if error_count:
        lines.append(
            f"{error_count} could not be converted — corrupt files or DRM-protected inputs "
            "(e.g. iTunes .m4p, protected .wma). These are bad inputs, not a FableGear failure."
        )
    else:
        lines.append("No errors.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _emit_report(_append_root_breakdown("\n".join(lines), root_sections), "Convert", f"convert_{timestamp}.txt")

    if error_count > 0:
        log.warning("%d files could not be converted (corrupt or DRM-protected)", error_count)


def cmd_organize(args: argparse.Namespace) -> None:
    """Consolidate audio files into a choosable folder hierarchy (default: Artist / Album)."""
    from pathlib import Path
    from library_organizer import organize_library, parse_scheme

    primary = Path(args.source)
    extra   = [Path(p) for p in (getattr(args, "also_scan", None) or [])]
    sources = [primary] + extra
    target  = Path(args.target)
    mode    = getattr(args, "mode", "assimilate")

    # Choosable grouping scheme (default: Artist / Album). A typo fails loudly.
    try:
        scheme_keys = parse_scheme(getattr(args, "by", None))
    except ValueError as exc:
        log.error("%s", exc)
        print(f"[ERROR] {exc}", flush=True)
        sys.exit(2)
    scheme_label = " / ".join(k.capitalize() for k in scheme_keys)

    for s in sources:
        if not s.is_dir():
            log.error("SOURCE is not a directory: %s", s)
            sys.exit(1)

    dry_run     = not args.no_dry_run
    max_workers = max(1, getattr(args, "workers", 1))
    threshold   = float(getattr(args, "mix_threshold", 15)) * 60.0

    if not target.is_dir():
        if dry_run:
            log.info("Target directory does not exist yet: %s (would be created on a live run)", target)
        else:
            try:
                target.mkdir(parents=True, exist_ok=True)
                log.info("Created target directory: %s", target)
            except OSError as e:
                log.error("Cannot create target directory %s: %s", target, e)
                sys.exit(1)

    if dry_run:
        log.info("DRY RUN — no files will be touched. Pass --no-dry-run to execute.")

    # Live runs must not proceed un-journaled — see _require_archive. Dry runs
    # touch nothing, so a missing archive is just a soft warning there.
    archive = _archive() if dry_run else _require_archive("organize")

    # Past-participle forms used in both dry-run plans and live reports.
    # Using full past-tense words avoids "{verb}ed" suffixing producing "copyed".
    action_past = "copied" if mode == "integrate" else "moved"
    action_verb = action_past
    log.info(
        "Organizing  sources=%s  target=%s  mode=%s  dry_run=%s  workers=%d  mix_threshold=%.0f min",
        [str(s) for s in sources], target, mode, dry_run, max_workers, threshold / 60,
    )

    # Checkpoint: only for live runs — a dry run touches nothing, so there's
    # nothing to resume and re-running it in full is cheap and expected.
    ckpt = None
    ckpt_done_paths: set[str] = set()
    if not dry_run:
        ckpt = _get_checkpoint("organize", sources, args, {
            "target": str(target), "mode": mode,
        })
        if ckpt is not None:
            saved = ckpt.load()
            if saved:
                ckpt_done_paths = set(saved.get("done_paths", []))

    _ckpt_save_counter = 0

    def _on_organize_result(r) -> None:
        nonlocal _ckpt_save_counter
        if ckpt is None:
            return
        if r.action in ("moved", "conflict_renamed", "skipped"):
            ckpt_done_paths.add(str(r.src))
        _ckpt_save_counter += 1
        if _ckpt_save_counter % 25 == 0:
            ckpt.save({"done_paths": sorted(ckpt_done_paths), "completed": len(ckpt_done_paths)})

    results = []
    root_sections: list[tuple[Path, str]] = []
    for index, source in enumerate(sources, start=1):
        _log_root_step("Organize", source, index, len(sources))
        try:
            root_results = organize_library(
                [source], target,
                mode=mode,
                dry_run=dry_run,
                max_workers=max_workers,
                mix_threshold_sec=threshold,
                archive=archive,
                skip_paths=ckpt_done_paths or None,
                on_result=_on_organize_result,
                scheme=scheme_keys,
            )
        except ValueError as exc:
            # Source guardrail tripped (system root / home folder / app data).
            log.error("%s", exc)
            print(f"[ERROR] {exc}", flush=True)
            sys.exit(2)
        results.extend(root_results)

        root_moved = sum(1 for r in root_results if r.action in ("moved", "dry_run", "conflict_renamed"))
        root_skipped = sum(1 for r in root_results if r.action == "skipped")
        root_conflicts = sum(1 for r in root_results if r.action == "conflict_renamed")
        root_errors = sum(1 for r in root_results if r.action == "error")
        root_lines = [f"{len(root_results)} files scanned."]
        if root_moved:
            root_lines.append(
                f"{root_moved} files would be {action_past} into {scheme_label} folders."
                if dry_run else
                f"{root_moved} files were {action_past} into {scheme_label} folders."
            )
        if root_skipped:
            root_lines.append(f"{root_skipped} were already at the destination — left alone.")
        if root_conflicts:
            root_lines.append(f"{root_conflicts} name clashes were handled by renaming.")
        if root_errors:
            root_lines.append(f"{root_errors} files had errors — check the log above.")
        root_sections.append((source, "\n".join(root_lines)))

    # Clean completion — clear the checkpoint so the next run starts fresh.
    if ckpt is not None:
        ckpt.reset()

    moved     = sum(1 for r in results if r.action in ("moved", "dry_run", "conflict_renamed"))
    skipped   = sum(1 for r in results if r.action == "skipped")
    conflicts = sum(1 for r in results if r.action == "conflict_renamed")
    errors    = sum(1 for r in results if r.action == "error")

    src_desc = str(sources[0]) if len(sources) == 1 else f"{len(sources)} source folders"

    if dry_run:
        mode_note = (
            "Integration mode — files will be copied to the target; the source drive stays untouched."
            if mode == "integrate" else
            "Assimilation mode — files will be moved and the source will be cleaned up."
        )
        lines = [
            "Here's what would change.",
            "",
            f"{len(results)} files scanned across {src_desc}.",
            f"Mode: {mode_note}",
        ]
        if moved:
            lines.append(f"  {moved} would be {action_past} into {scheme_label} folders.")
        if skipped:
            lines.append(f"  {skipped} are exact copies already at the destination — they'd be skipped.")
        if conflicts:
            lines.append(f"  {conflicts} have a name clash — they'd be renamed (e.g. track_1.mp3).")
        if errors:
            lines.append(f"  {errors} had errors — check the log above.")
        lines += ["", f"Nothing has been {action_past}. Uncheck \"Dry Run\" and run again to execute."]
    else:
        lines = ["Done organizing.", ""]
        if moved:
            lines.append(f"{moved} files were {action_verb} into {scheme_label} folders.")
        if skipped:
            lines.append(f"{skipped} were already at the destination — left alone.")
        if conflicts:
            lines.append(f"{conflicts} name clashes were handled by renaming (e.g. track_1.mp3).")
        if errors:
            lines.append(f"{errors} files had errors — check the log above.")
        else:
            lines.append("No errors.")
        if mode == "integrate":
            lines.append("Source folders were not modified.")
        else:
            lines.append("Empty source folders were cleaned up.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _emit_report(_append_root_breakdown("\n".join(lines), root_sections), "Organize", f"organize_{timestamp}.txt")

    if dry_run:
        # Emit planned moves so the UI/log shows what would happen
        for r in results:
            if r.action == "dry_run":
                log.info("PLAN  %s  →  %s", r.src.name, r.reason)

    if errors > 0:
        log.warning("%d files had errors — check log above", errors)


def cmd_novelty(args: argparse.Namespace) -> None:
    """Find tracks that only exist on the source and copy them to the destination."""
    from pathlib import Path
    from novelty_scanner import scan_novel

    primary = Path(args.source)
    extra   = [Path(p) for p in (getattr(args, "also_scan", None) or [])]
    sources = [primary] + extra
    dest    = Path(args.dest)
    copy_to_arg = getattr(args, "copy_to", None)
    copy_to = Path(copy_to_arg) if copy_to_arg else None
    dry_run = not args.no_dry_run
    match_mode = getattr(args, "match_mode", "fingerprint")

    for s in sources:
        if not s.is_dir():
            log.error("SOURCE is not a directory: %s", s)
            sys.exit(1)
    if not dest.is_dir():
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.error("Cannot create destination %s: %s", dest, e)
            sys.exit(1)
    if copy_to is not None and not copy_to.is_dir():
        try:
            copy_to.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.error("Cannot create copy-to folder %s: %s", copy_to, e)
            sys.exit(1)

    max_workers = max(1, getattr(args, "workers", 1))

    if dry_run:
        log.info("DRY RUN — no files will be copied. Pass --no-dry-run to execute.")

    # Live runs must not proceed un-journaled — see _require_archive. Dry runs
    # touch nothing, so a missing archive is just a soft warning there.
    archive = _archive() if dry_run else _require_archive("novelty")

    log.info(
        "Novel scan  sources=%s  compare_against=%s  copy_to=%s  dry_run=%s  "
        "workers=%d  match_mode=%s",
        [str(s) for s in sources], dest, copy_to or dest, dry_run, max_workers, match_mode,
    )

    total_src = 0
    dest_index_size = 0
    aggregate_novel = []
    aggregate_present = []
    aggregate_errors = []
    root_sections: list[tuple[Path, str]] = []
    verb = "would be copied" if dry_run else "copied"
    copy_target_label = str(copy_to) if copy_to is not None else str(dest)

    # Checkpoint: only for live runs — a dry run touches nothing, so there's
    # nothing to resume and re-running it in full is cheap and expected.
    ckpt = None
    ckpt_done_paths: set[str] = set()
    if not dry_run:
        ckpt = _get_checkpoint("novelty", sources, args, {
            "dest": str(dest), "copy_to": str(copy_to) if copy_to else "", "match_mode": match_mode,
        })
        if ckpt is not None:
            saved = ckpt.load()
            if saved:
                ckpt_done_paths = set(saved.get("done_paths", []))

    _ckpt_save_counter = 0

    def _on_novelty_result(r) -> None:
        nonlocal _ckpt_save_counter
        if ckpt is None:
            return
        if r.action in ("copied", "dry_run", "skipped"):
            ckpt_done_paths.add(str(r.path))
        _ckpt_save_counter += 1
        if _ckpt_save_counter % 25 == 0:
            ckpt.save({"done_paths": sorted(ckpt_done_paths), "completed": len(ckpt_done_paths)})

    for index, source in enumerate(sources, start=1):
        _log_root_step("Novelty", source, index, len(sources))
        root_result = scan_novel(
            [source], dest,
            copy_to=copy_to,
            dry_run=dry_run,
            max_workers=max_workers,
            match_mode=match_mode,
            archive=archive,
            skip_paths=ckpt_done_paths or None,
            on_result=_on_novelty_result,
        )
        total_src += root_result.total_src
        dest_index_size = max(dest_index_size, root_result.dest_index_size)
        aggregate_novel.extend(root_result.novel)
        aggregate_present.extend(root_result.present)
        aggregate_errors.extend(root_result.errors)

        root_novel = len(root_result.novel)
        root_present = len(root_result.present)
        root_errors = len(root_result.errors)
        root_lines = [
            f"{root_result.total_src} tracks scanned on source.",
            f"Compared against: {root_result.dest_index_size} tracks in {dest}.",
        ]
        if root_novel:
            root_lines.append(f"{root_novel} novel tracks {verb} to {copy_target_label}.")
        if root_present:
            root_lines.append(f"{root_present} tracks confirmed already present — skipped.")
        if root_errors:
            root_lines.append(f"{root_errors} errors — check log above.")
        root_sections.append((source, "\n".join(root_lines)))

    # Clean completion — clear the checkpoint so the next run starts fresh.
    if ckpt is not None:
        ckpt.reset()

    class _AggregateNoveltyResult:
        pass

    result = _AggregateNoveltyResult()
    result.novel = aggregate_novel
    result.present = aggregate_present
    result.errors = aggregate_errors
    result.total_src = total_src
    result.dest_index_size = dest_index_size

    novel   = len(result.novel)
    present = len(result.present)
    errors  = len(result.errors)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines = [
        "Novel Track Scan complete.",
        "",
        f"{result.total_src} tracks scanned on source.",
        f"Compared against: {result.dest_index_size} tracks in {dest}.",
        f"Comparison mode: {match_mode}.",
        "",
    ]
    if novel:
        lines.append(f"  {novel} novel tracks {verb} to {copy_target_label}.")
    if present:
        lines.append(f"  {present} tracks confirmed already present — skipped.")
    if errors:
        lines.append(f"  {errors} errors — check log above.")
    if dry_run:
        lines += ["", "Nothing has been copied. Uncheck \"Dry Run\" and run again to execute."]

    _emit_report(_append_root_breakdown("\n".join(lines), root_sections), "Novelty Scan", f"novelty_{timestamp}.txt")

    if errors > 0:
        log.warning("%d files had errors — check log above", errors)


def cmd_rename(args: argparse.Namespace) -> None:
    """Rename audio files based on their ID3/tag metadata to clean filenames."""
    from db_connection import write_db
    from renamer import rename_directory

    roots = [Path(args.path)] + [Path(p) for p in (getattr(args, "also_scan", None) or [])]

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    dry_run = not args.no_dry_run
    requested_workers = max(1, getattr(args, "workers", 1))
    max_workers = 1

    if requested_workers != 1:
        log.info("Rename runs sequentially; ignoring workers=%d", requested_workers)

    if dry_run:
        log.info("DRY RUN — no files will be renamed. Pass --no-dry-run to execute.")

    log.info(
        "Renaming audio files under %s  dry_run=%s  workers=%d",
        [str(r) for r in roots], dry_run, max_workers,
    )

    # Checkpoint: only for live runs — a dry run touches nothing, so there's
    # nothing to resume and re-running it in full is cheap and expected.
    ckpt = None
    ckpt_done_paths: set[str] = set()
    if not dry_run:
        ckpt = _get_checkpoint("rename", roots, args, {})
        if ckpt is not None:
            saved = ckpt.load()
            if saved:
                ckpt_done_paths = set(saved.get("done_paths", []))

    _ckpt_save_counter = 0

    def _on_rename_result(r) -> None:
        nonlocal _ckpt_save_counter
        if ckpt is None:
            return
        if r.action in ("renamed", "no_change", "collision_numbered", "quarantined"):
            ckpt_done_paths.add(str(r.original_path))
        _ckpt_save_counter += 1
        if _ckpt_save_counter % 25 == 0:
            ckpt.save({"done_paths": sorted(ckpt_done_paths), "completed": len(ckpt_done_paths)})

    results = []
    root_sections: list[tuple[Path, str]] = []

    try:
        if dry_run:
            for index, root in enumerate(roots, start=1):
                _log_root_step("Rename", root, index, len(roots))
                root_results = rename_directory(
                    root, db=None, dry_run=True, max_workers=max_workers, archive=_archive(),
                    skip_paths=ckpt_done_paths or None, on_result=_on_rename_result,
                )
                results.extend(root_results)
                root_renamed = sum(1 for r in root_results if r.action == "renamed")
                root_skipped = sum(1 for r in root_results if r.action == "no_change")
                root_collisions = sum(1 for r in root_results if r.action == "collision_numbered")
                root_quarantined = sum(1 for r in root_results if r.action == "quarantined")
                root_errors = sum(1 for r in root_results if r.action == "error")
                root_lines = [f"{len(root_results)} audio files scanned."]
                if root_renamed:
                    root_lines.append(f"{root_renamed} files would be renamed.")
                if root_skipped:
                    root_lines.append(f"{root_skipped} already have clean names — would be left alone.")
                if root_collisions:
                    root_lines.append(f"{root_collisions} would get numbered suffixes to avoid clashes.")
                if root_quarantined:
                    root_lines.append(f"{root_quarantined} unresolved files would be moved to No-Name tracks for Tagging.")
                if root_errors:
                    root_lines.append(f"{root_errors} had errors — check the log above.")
                root_sections.append((root, "\n".join(root_lines)))
        else:
            archive = _require_archive("rename")
            with write_db(LOCAL_DB) as db:
                for index, root in enumerate(roots, start=1):
                    _log_root_step("Rename", root, index, len(roots))
                    root_results = rename_directory(
                        root, db=db, dry_run=False, max_workers=max_workers, archive=archive,
                        skip_paths=ckpt_done_paths or None, on_result=_on_rename_result,
                    )
                    results.extend(root_results)
                    root_renamed = sum(1 for r in root_results if r.action == "renamed")
                    root_skipped = sum(1 for r in root_results if r.action == "no_change")
                    root_collisions = sum(1 for r in root_results if r.action == "collision_numbered")
                    root_quarantined = sum(1 for r in root_results if r.action == "quarantined")
                    root_errors = sum(1 for r in root_results if r.action == "error")
                    root_lines = [f"{len(root_results)} audio files scanned."]
                    if root_renamed:
                        root_lines.append(f"{root_renamed} files were renamed to clean titles.")
                    if root_skipped:
                        root_lines.append(f"{root_skipped} already had clean names — left alone.")
                    if root_collisions:
                        root_lines.append(f"{root_collisions} name clashes were handled by numbering.")
                    if root_quarantined:
                        root_lines.append(f"{root_quarantined} unresolved files were moved to No-Name tracks for Tagging.")
                    if root_errors:
                        root_lines.append(f"{root_errors} files had errors — check the log above.")
                    root_sections.append((root, "\n".join(root_lines)))
    except Exception:
        log.exception("Rename failed")
        sys.exit(1)

    # Clean completion — clear the checkpoint so the next run starts fresh.
    if ckpt is not None:
        ckpt.reset()

    total = len(results)
    renamed = sum(1 for r in results if r.action == "renamed")
    skipped = sum(1 for r in results if r.action == "no_change")
    collisions = sum(1 for r in results if r.action == "collision_numbered")
    quarantined = sum(1 for r in results if r.action == "quarantined")
    errors = sum(1 for r in results if r.action == "error")

    if dry_run:
        lines = [
            "Here's what would change.",
            "",
            f"{total} audio files scanned.",
        ]
        if renamed:
            lines.append(f"  {renamed} files would be renamed to clean titles.")
        if skipped:
            lines.append(f"  {skipped} already have clean names — would be left alone.")
        if collisions:
            lines.append(f"  {collisions} would get numbered suffixes to avoid clashes (e.g. title_1.mp3).")
        if quarantined:
            lines.append(f"  {quarantined} unresolved files would be moved to No-Name tracks for Tagging.")
        if errors:
            lines.append(f"  {errors} had errors — check the log above.")
        lines += ["", "Nothing has been renamed. Uncheck \"Dry Run\" and run again to execute."]
    else:
        lines = ["Done renaming.", ""]
        if renamed:
            lines.append(f"{renamed} files were renamed to clean titles (artist kept in tags).")
        if skipped:
            lines.append(f"{skipped} already had clean names — left alone.")
        if collisions:
            lines.append(f"{collisions} name clashes were handled by numbering (e.g. title_1.mp3).")
        if quarantined:
            lines.append(f"{quarantined} unresolved files were moved to No-Name tracks for Tagging.")
        if errors:
            lines.append(f"{errors} files had errors — check the log above.")
        else:
            lines.append("No errors.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _emit_report(_append_root_breakdown("\n".join(lines), root_sections), "Rename", f"rename_{timestamp}.txt")

    if errors > 0:
        log.warning("%d files had errors — check log above", errors)


# ─── Argument parser ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rbtk",
        description="Rekordbox Toolkit — library management for serious DJ libraries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cli.py audit
    python3 cli.py import "/path/to/music" --dry-run
    python3 cli.py import "/path/to/music"
    python3 cli.py link "/path/to/music"
  python3 cli.py relocate /old/path /new/path
    python3 cli.py duplicates "/path/to/music" --output ~/Desktop/dupes.csv
        python3 cli.py rekordbox-dedupe --dry-run
        python3 cli.py rekordbox-dedupe --no-dry-run
    python3 cli.py process "/path/to/music" --no-normalize
    python3 cli.py process "/path/to/music" --dry-run --no-bpm --no-key
    python3 cli.py convert "/path/to/music" mp3
    python3 cli.py convert "/path/to/music" flac
    python3 cli.py rename "/path/to/music" --dry-run
    python3 cli.py rename "/path/to/music"
        """,
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # ── audit ──
    p_usb = sub.add_parser(
        "usb-inspect",
        help="Read-only check of a Pioneer export drive (DeviceSQL + OneLibrary)",
    )
    p_usb.add_argument("mount", help="Mount point of the drive, e.g. /Volumes/DJ_USB")
    p_usb.set_defaults(func=cmd_usb_inspect)

    p_anlz = sub.add_parser(
        "anlz-read",
        help="Read-only deep parse of one track's ANLZ set (.DAT/.EXT/.2EX)",
    )
    p_anlz.add_argument("path", help="Per-track ANLZ directory (contains ANLZ0000.DAT)")
    p_anlz.set_defaults(func=cmd_anlz_read)

    p_settings = sub.add_parser(
        "pioneer-settings",
        help="Read-only parse of Pioneer/rekordbox player & mixer settings files",
    )
    p_settings.add_argument("path", help="A settings file, or a PIONEER/ directory to scan")
    p_settings.set_defaults(func=cmd_pioneer_settings)

    p_pdb = sub.add_parser(
        "pdb-read",
        help="Read-only header validation of a DeviceSQL export.pdb / exportExt.pdb",
    )
    p_pdb.add_argument("path", help="Path to export.pdb or exportExt.pdb")
    p_pdb.set_defaults(func=cmd_pdb_read)

    p_export_audit = sub.add_parser(
        "export-audit",
        help="Read-only Phase B deep audit of a mounted Pioneer export tree (writes to the FableGear archive)",
    )
    p_export_audit.add_argument("mount", help="Mount point of the drive, e.g. /Volumes/DJ_USB")
    p_export_audit.set_defaults(func=cmd_export_audit)

    p_audit = sub.add_parser("audit", help="Read-only library health check")
    p_audit.add_argument(
        "--root",
        metavar="PATH",
        help=f"Primary music root for orphan scan (default: {MUSIC_ROOT})",
    )
    p_audit.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        dest="also_scan",
        help="Additional library root to include in the physical scan (repeatable)",
    )
    p_audit.set_defaults(func=cmd_audit)

    # ── import ──
    p_import = sub.add_parser("import", help="Import audio files into the database")
    p_import.add_argument("path", metavar="PATH", help="Directory to import")
    p_import.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        dest="also_scan",
        help="Additional directory to import (repeatable)",
    )
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report without writing to the database",
    )
    p_import.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted import using the saved progress state for each source root",
    )
    p_import.set_defaults(func=cmd_import)

    # ── link ──
    p_link = sub.add_parser("link", help="Link imported tracks to existing playlists")
    p_link.add_argument("path", metavar="PATH", help="Directory whose tracks to link")
    p_link.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        dest="also_scan",
        help="Additional directory whose tracks to link (repeatable)",
    )
    p_link.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview playlist matches without writing any playlist links",
    )
    p_link.set_defaults(func=cmd_link)

    # ── relocate ──
    p_relocate = sub.add_parser(
        "relocate",
        help="Batch-update paths for moved/renamed files",
    )
    p_relocate.add_argument(
        "old_root",
        metavar="OLD_ROOT",
        help="Previous path prefix stored in the DB (does not need to exist on disk)",
    )
    p_relocate.add_argument("new_root", metavar="NEW_ROOT", help="New path where files now live")
    p_relocate.add_argument(
        "--include-existing",
        action="store_true",
        help="Also repoint rows whose file still exists at the old path "
             "(mid-migration only; by default healthy tracks are never touched)",
    )
    p_relocate.set_defaults(func=cmd_relocate)

    # ── duplicates ──
    p_dupes = sub.add_parser(
        "duplicates",
        help="Find acoustically identical files via Chromaprint",
    )
    p_dupes.add_argument("path", metavar="PATH", nargs="+", help="Directory (or directories) to scan")
    p_dupes.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="CSV output path (default: ~/fablegear/duplicate_report.csv)",
    )
    p_dupes.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Number of parallel fpcalc workers (default: 1)",
    )
    p_dupes.add_argument(
        "--match-mode", "-m",
        metavar="MODE",
        choices=["exact", "fuzzy", "tags", "all"],
        default="exact",
        dest="match_mode",
        help=(
            "Matching strategy: "
            "exact=fingerprint string equality (default, fastest), "
            "fuzzy=Hamming-distance fingerprint comparison (catches different encodings), "
            "tags=title+artist+duration pre-matching, "
            "all=tags + fuzzy (most thorough, slowest)"
        ),
    )
    p_dupes.add_argument(
        "--fuzzy-threshold",
        metavar="F",
        type=float,
        default=0.85,
        dest="fuzzy_threshold",
        help="Similarity threshold for fuzzy fingerprint matching (0.0–1.0, default: 0.85)",
    )
    p_dupes.add_argument(
        "--checkpoint-action",
        choices=["resume", "reset"],
        default="resume",
        dest="checkpoint_action",
        help="resume (default): continue an interrupted scan from its checkpoint. "
             "reset: discard the checkpoint and fingerprint from the beginning.",
    )
    p_dupes.add_argument(
        "--scan-mode",
        choices=["quick", "deep"],
        default="deep",
        dest="scan_mode",
        help="quick = instant byte-identical match from cached DB hashes (no fpcalc); "
             "deep (default) = acoustic Chromaprint fingerprinting (catches re-encodes / different formats).",
    )
    p_dupes.set_defaults(func=cmd_duplicates)

    # ── rekordbox-dedupe ──
    p_rb_dupes = sub.add_parser(
        "rekordbox-dedupe",
        help="Scan Rekordbox DB tracks for duplicates and optionally prune with playlist rethreading",
    )
    p_rb_dupes.add_argument(
        "--db-path",
        metavar="PATH",
        help="Explicit Rekordbox DB path (default: DEVICE_DB when mounted, else LOCAL_DB)",
    )
    p_rb_dupes.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="CSV output path for duplicate report",
    )
    p_rb_dupes.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Number of parallel fpcalc workers (default: 1)",
    )
    p_rb_dupes.add_argument(
        "--match-mode", "-m",
        metavar="MODE",
        choices=["exact", "fuzzy", "tags", "all"],
        default="exact",
        dest="match_mode",
        help=(
            "Matching strategy: exact (default), fuzzy, tags, or all"
        ),
    )
    p_rb_dupes.add_argument(
        "--fuzzy-threshold",
        metavar="F",
        type=float,
        default=0.85,
        dest="fuzzy_threshold",
        help="Similarity threshold for fuzzy mode (0.0–1.0, default: 0.85)",
    )
    p_rb_dupes.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually prune duplicates and rewrite playlist references",
    )
    p_rb_dupes.add_argument(
        "--permanent",
        action="store_true",
        help="Permanently delete instead of moving to recoverable Trash",
    )
    p_rb_dupes.set_defaults(func=cmd_rekordbox_dedupe, dry_run=True, permanent=False)

    # ── rekordbox-sync ──
    p_rb_sync = sub.add_parser(
        "rekordbox-sync",
        help="Run bidirectional synchronization between FableGear and Rekordbox databases",
    )
    p_rb_sync.add_argument(
        "--db-path",
        metavar="PATH",
        help="Explicit Rekordbox DB path (default: LOCAL_DB)",
    )
    p_rb_sync.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually apply synchronization changes to the databases",
    )
    p_rb_sync.set_defaults(func=cmd_rekordbox_sync, dry_run=True)

    # ── export-onelibrary ──
    p_rec = sub.add_parser(
        "recover-playlists",
        help="Recover playlists/crates from exported media (exportLibrary.db / "
             "export.pdb) and rebuild them in the archive. Dry-run unless --write.",
    )
    p_rec.add_argument("--source", action="append", default=[],
                       help="Drive/folder to scan, or a direct export file (repeatable)")
    p_rec.add_argument("--source-list", default=None, dest="source_list",
                       help="File with one source path per line")
    p_rec.add_argument("--write", action="store_true",
                       help="Rebuild the recovered crates into the archive (default: dry-run)")
    p_rec.add_argument("--strategy", choices=["richest"], default="richest",
                       help="Union strategy when a crate appears on multiple sticks")
    p_rec.add_argument("--merge-duplicates", action="store_true", dest="merge_duplicates",
                       help="Collapse Rekordbox '(N)' duplicate-name crates into one each")
    p_rec.add_argument("--replace", action="store_true",
                       help="Delete any prior 'Recovered …' folders before writing this run")
    p_rec.add_argument("--min-resolved", type=int, default=1, dest="min_resolved",
                       help="Only rebuild crates with at least this many resolved tracks")
    p_rec.add_argument("--report", default=None, help="Write the full crate list to this file")
    p_rec.set_defaults(func=cmd_recover_playlists)

    p_imp = sub.add_parser(
        "import-missing-to-rekordbox",
        help="Phase 2: add the audio a recovery references but Rekordbox lacks "
             "(located via FableGear), so the push can then link it. Dry-run unless --write.",
    )
    p_imp.add_argument("--source", action="append", default=[],
                       help="Recovery source (e.g. the recovered master.db). Repeatable.")
    p_imp.add_argument("--source-list", default=None, dest="source_list",
                       help="File with one source path per line")
    p_imp.add_argument("--target", default=None,
                       help="master.db to write to (default: your live library)")
    p_imp.add_argument("--write", action="store_true",
                       help="Actually add tracks (default: dry-run). Requires Rekordbox closed.")
    p_imp.add_argument("--merge-duplicates", action="store_true", dest="merge_duplicates",
                       help="Collapse Rekordbox '(N)' duplicate-name crates into one each")
    p_imp.add_argument("--undo", action="store_true",
                       help="Remove the tracks added by the last import (Rekordbox closed)")
    p_imp.set_defaults(func=cmd_import_missing_rekordbox)

    p_push = sub.add_parser(
        "push-recovery-to-rekordbox",
        help="Push recovered crates into the live Rekordbox master.db (dry-run "
             "unless --write; non-destructive, backed up, undoable).",
    )
    p_push.add_argument("--source", action="append", default=[],
                        help="Recovery source (e.g. the recovered master.db). Repeatable.")
    p_push.add_argument("--source-list", default=None, dest="source_list",
                        help="File with one source path per line")
    p_push.add_argument("--target", default=None,
                        help="master.db to write to (default: your live library)")
    p_push.add_argument("--write", action="store_true",
                        help="Actually write (default: dry-run). Requires Rekordbox closed.")
    p_push.add_argument("--merge-duplicates", action="store_true", dest="merge_duplicates",
                        help="Collapse Rekordbox '(N)' duplicate-name crates into one each")
    p_push.add_argument("--min-tracks", type=int, default=1, dest="min_tracks",
                        help="Only push crates with at least this many resolvable tracks "
                             "(e.g. 7 to drop tiny package playlists)")
    p_push.add_argument("--no-skip-existing", action="store_true", dest="no_skip_existing",
                        help="Do not skip crates whose name already exists (default: skip them)")
    p_push.add_argument("--undo", action="store_true",
                        help="Remove the playlists created by the last push (Rekordbox closed)")
    p_push.set_defaults(func=cmd_push_rekordbox)

    p_pl = sub.add_parser("playlist", help="Create or list FableGear playlists")
    pl_sub = p_pl.add_subparsers(dest="playlist_action", required=True)
    pl_create = pl_sub.add_parser("create", help="Create a playlist (optionally from an imported folder)")
    pl_create.add_argument("name", help="Playlist name")
    pl_create.add_argument("--from-folder", default=None,
                           help="Add all imported tracks whose file is under this folder")
    pl_sub.add_parser("list", help="List playlists")
    p_pl.set_defaults(func=cmd_playlist)

    p_onelib = sub.add_parser(
        "export-onelibrary",
        help="Write a Pioneer OneLibrary exportLibrary.db from FableGear's database "
             "(CDJ-3000/OMNIS-DUO/XDJ-AZ/OPUS-QUAD format; hardware-unvalidated)",
    )
    p_onelib.add_argument(
        "target",
        metavar="TARGET",
        help="Destination path for exportLibrary.db (must not already exist)",
    )
    p_onelib.add_argument(
        "--no-playlists",
        action="store_true",
        help="Skip exporting playlists/folders",
    )
    p_onelib.add_argument(
        "--device-name",
        default="",
        help="Device name to record in the property table (default: FableGear)",
    )
    p_onelib.add_argument(
        "--dj-name",
        default="",
        help="DJ profile display name for djprofile.nxs (default: FableGear)",
    )
    p_onelib.add_argument(
        "--no-identity-files",
        action="store_true",
        help="Skip writing RBFLTR.DAT and djprofile.nxs alongside exportLibrary.db",
    )
    p_onelib.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing export at TARGET (removes the DB + WAL/SHM) "
             "instead of refusing — for re-exporting to the same stick",
    )
    p_onelib.add_argument(
        "--stage-audio",
        action="store_true",
        help="Copy each track's audio onto the drive (TARGET/../../Contents/...) "
             "and point the library at it — required for a playable CDJ stick",
    )
    p_onelib.add_argument(
        "--with-anlz",
        action="store_true",
        help="Generate ANLZ analysis files (beat grids + waveforms) for every "
             "exported track. Adds ~4-6s/track. Without it the CDJ re-analyzes.",
    )
    p_onelib.add_argument(
        "--playlist",
        default=None,
        help="Export only this playlist (by name) and its tracks, not the whole archive",
    )
    p_onelib.add_argument(
        "--playlist-id",
        type=int,
        default=None,
        help="Export only this playlist (by id) and its tracks",
    )
    p_onelib.add_argument(
        "--content-ids",
        default=None,
        help="Comma-separated FableGear content ids to export (overrides playlist track set)",
    )
    p_onelib.set_defaults(func=cmd_export_onelibrary)

    # ── prune ──
    p_prune = sub.add_parser(
        "prune",
        help="Remove duplicates listed in a duplicate_report.csv (keeps best copy)",
    )
    p_prune.add_argument(
        "csv_path",
        metavar="CSV",
        help="Path to a duplicate_report.csv produced by the duplicates command",
    )
    p_prune.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually prune. Without this flag prune only previews (dry-run by default).",
    )
    p_prune.add_argument(
        "--permanent",
        action="store_true",
        help="Permanently delete instead of moving to a recoverable Trash folder",
    )
    p_prune.set_defaults(func=cmd_prune, dry_run=True, permanent=False)

    # ── process ──
    p_parse = sub.add_parser(
        "parse",
        help="Track Parsing tool — prepare tracks for DJ gear: BPM, key, beat "
             "grid, and waveforms (mono/colour/3-band). Makes export pure assembly.",
    )
    p_parse.add_argument("path", metavar="PATH", nargs="?", default=None,
                         help="Folder of imported tracks to parse (omit with --all for the whole library)")
    p_parse.add_argument("--all", action="store_true", help="Parse every track in the library")
    p_parse.add_argument("--force", action="store_true",
                         help="Re-detect BPM/key and regenerate even if already present")
    p_parse.add_argument("--no-waveforms", action="store_true",
                         help="Build beat grids only, skip waveform generation (faster)")
    p_parse.set_defaults(func=cmd_parse)

    p_process = sub.add_parser(
        "process",
        help="Detect BPM/key and normalise loudness",
    )
    p_process.add_argument("path", metavar="PATH", help="Directory to process")
    p_process.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing BPM/key tags",
    )
    p_process.add_argument(
        "--force-bpm",
        action="store_true",
        dest="force_bpm",
        help="Re-detect and overwrite BPM even if a BPM tag already exists",
    )
    p_process.add_argument(
        "--force-key",
        action="store_true",
        dest="force_key",
        help="Re-detect and overwrite key even if a key tag already exists",
    )
    p_process.add_argument(
        "--fix-octaves",
        action="store_true",
        dest="fix_octaves",
        help="Octave-correct detected BPM into 76-152 (fixes librosa half/double-"
             "time errors, e.g. 60->120). Only 2x errors are fixable this way.",
    )
    p_process.add_argument(
        "--no-bpm",
        action="store_true",
        help="Skip BPM detection and tag writes",
    )
    p_process.add_argument(
        "--no-key",
        action="store_true",
        help="Skip key detection and tag writes",
    )
    p_process.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip loudness normalisation (default: normalise is ON)",
    )
    p_process.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Suppress loudness normalisation. "
            "BPM/key tag writes still occur unless --no-bpm/--no-key are also set."
        ),
    )
    p_process.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Parallel ffmpeg workers for loudness measurement/normalisation (default: 1)",
    )
    p_process.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        dest="also_scan",
        help="Additional directory to process (repeatable)",
    )
    p_process.add_argument(
        "--enrich-tags",
        action="store_true",
        dest="enrich_tags",
        help="Enrich metadata from AcoustID/MusicBrainz after BPM/key detection (requires ACOUSTID_API_KEY in config)",
    )
    p_process.add_argument(
        "--paths-file",
        metavar="FILE",
        dest="paths_file",
        default=None,
        help="Text file containing one absolute file path per line. When supplied, only those specific files are processed (PATH arg is still required but ignored as a scan root).",
    )
    p_process.add_argument(
        "--smart-skip",
        action="store_true",
        dest="smart_skip",
        help="Before processing, filter out files that already have all requested tags. Faster re-runs when most files are already complete.",
    )
    p_process.add_argument(
        "--checkpoint-action",
        choices=["resume", "reset"],
        default="resume",
        dest="checkpoint_action",
        help="resume (default): continue an interrupted run from its checkpoint. "
             "reset: discard the checkpoint and start from the beginning.",
    )
    p_process.set_defaults(func=cmd_process)

    # ── convert ──
    p_convert = sub.add_parser(
        "convert",
        help="Convert audio files to target format (mp3, wav, aif, flac)",
    )
    p_convert.add_argument("path", metavar="PATH", help="Directory to convert")
    p_convert.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        dest="also_scan",
        help="Additional directory to convert (repeatable)",
    )
    p_convert.add_argument(
        "format",
        metavar="FORMAT",
        help="Target format: mp3, wav, aif, or flac",
    )
    p_convert.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Parallel ffmpeg workers for conversion (default: 1)",
    )
    p_convert.add_argument(
        "--checkpoint-action",
        choices=["resume", "reset"],
        default="resume",
        dest="checkpoint_action",
        help="resume (default): continue an interrupted conversion from its checkpoint. "
             "reset: discard the checkpoint and start from the beginning.",
    )
    p_convert.set_defaults(func=cmd_convert)

    # ── organize ──
    p_organize = sub.add_parser(
        "organize",
        help="Consolidate files into a choosable folder hierarchy (default: Artist / Album)",
    )
    p_organize.add_argument(
        "source",
        metavar="SOURCE",
        help="Directory to scan for audio files",
    )
    p_organize.add_argument(
        "--by",
        metavar="KEYS",
        default=None,
        help="Grouping scheme as slash-nested keys (default: artist/album). "
             "Keys: label, artist, album, title, genre, year, filetype. "
             "Examples: --by label   --by label/artist   --by genre/artist   --by filetype. "
             "Tag values are cleaned (URLs, junk, Camelot-in-artist dropped); a track with no "
             "value for the first key goes to Orphaned Tracks.",
    )
    p_organize.add_argument(
        "target",
        metavar="TARGET",
        help="Root of the organised library (e.g. /path/to/music)",
    )
    p_organize.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Actually move files. Default behaviour is dry-run (preview only).",
    )
    p_organize.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Parallel I/O workers for the move phase (default: 1)",
    )
    p_organize.add_argument(
        "--mix-threshold",
        metavar="MINUTES",
        type=float,
        default=15.0,
        help="Tracks at or above this duration (minutes) go to Live Sets & Mixes (default: 15)",
    )
    p_organize.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        default=[],
        dest="also_scan",
        help="Additional source directory to scan (can be repeated for multiple sources)",
    )
    p_organize.add_argument(
        "--mode",
        choices=["assimilate", "integrate"],
        default="assimilate",
        help=(
            "assimilate: move files, remove source duplicates, prune empty dirs (default). "
            "integrate: copy files to target only — source drive is never modified."
        ),
    )
    p_organize.add_argument(
        "--checkpoint-action",
        choices=["resume", "reset"],
        default="resume",
        dest="checkpoint_action",
        help="resume (default): continue an interrupted organize run from its checkpoint. "
             "reset: discard the checkpoint and start from the beginning.",
    )
    p_organize.set_defaults(func=cmd_organize)

    # ── novelty ───────────────────────────────────────────────────────────────
    p_novelty = sub.add_parser(
        "novelty",
        help="Find and copy tracks that exist only on the source (not in destination)",
    )
    p_novelty.add_argument(
        "source",
        metavar="SOURCE",
        help="Drive or directory to scan for novel tracks",
    )
    p_novelty.add_argument(
        "dest",
        metavar="DEST",
        help="Home library root to compare source tracks against",
    )
    p_novelty.add_argument(
        "--copy-to",
        metavar="PATH",
        dest="copy_to",
        default=None,
        help="Where confirmed-novel tracks are copied. Defaults to DEST "
             "(the old single-folder behavior) when omitted — pass this to "
             "keep new finds segregated in their own folder while still "
             "comparing against the real home library.",
    )
    p_novelty.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Actually copy files. Default is dry-run (preview only).",
    )
    p_novelty.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Parallel workers (default: 1)",
    )
    p_novelty.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        default=[],
        dest="also_scan",
        help="Additional source directory (can be repeated)",
    )
    p_novelty.add_argument(
        "--match-mode",
        choices=["fingerprint", "filename"],
        default="fingerprint",
        help="fingerprint: metadata pre-filter + fingerprint confirmation (default). filename: match by normalized filename only (faster, less strict).",
    )
    p_novelty.add_argument(
        "--checkpoint-action",
        choices=["resume", "reset"],
        default="resume",
        dest="checkpoint_action",
        help="resume (default): continue an interrupted novelty scan from its checkpoint. "
             "reset: discard the checkpoint and start from the beginning.",
    )
    p_novelty.set_defaults(func=cmd_novelty)

    # ── rename ──
    p_rename = sub.add_parser(
        "rename",
        help="Rename audio files to clean titles based on ID3/tag metadata"
    )
    p_rename.add_argument(
        "path",
        metavar="PATH",
        help="Directory to scan and rename files"
    )
    p_rename.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        default=[],
        dest="also_scan",
        help="Additional directory to scan and rename (can be repeated)",
    )
    p_rename.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Actually rename files. Default is dry-run (preview only).",
    )
    p_rename.add_argument(
        "--workers", "-w",
        metavar="N",
        type=int,
        default=1,
        help="Parallel workers (default: 1)",
    )
    p_rename.add_argument(
        "--checkpoint-action",
        choices=["resume", "reset"],
        default="resume",
        dest="checkpoint_action",
        help="resume (default): continue an interrupted rename run from its checkpoint. "
             "reset: discard the checkpoint and start from the beginning.",
    )
    p_rename.set_defaults(func=cmd_rename)

    # ── dead-files ──
    p_dead = sub.add_parser(
        "dead-files",
        help="Find audio files on disk not referenced in any Rekordbox database",
    )
    p_dead.add_argument(
        "path",
        metavar="PATH",
        help="Root directory to scan (e.g. /path/to/music)",
    )
    p_dead.add_argument(
        "--also-scan",
        metavar="PATH",
        action="append",
        dest="also_scan",
        help="Additional root directory to include in the scan (repeatable)",
    )
    p_dead.set_defaults(func=cmd_dead_files)

    # ── setup ──
    p_setup = sub.add_parser("setup", help="Run the first-run configuration wizard")
    p_setup.add_argument(
        "--update",
        action="store_true",
        help="Re-run setup, pre-filling existing values",
    )
    p_setup.set_defaults(func=cmd_setup)

    return parser


def build_parser() -> argparse.ArgumentParser:
    """Public parser factory for testing and programmatic access."""
    return _build_parser()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)

    log.debug("Command: %s", args.command)
    log.debug("Args: %s", vars(args))

    args.func(args)


if __name__ == "__main__":
    main()
