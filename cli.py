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
    organize    Consolidate files into Artist / Album / Track hierarchy
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


def cmd_dead_files(args: argparse.Namespace) -> None:
    """Find audio files on disk not referenced in any Rekordbox database."""
    from dead_file_scanner import scan_dead_files

    roots = [Path(args.path)] + [Path(p) for p in (args.also_scan or [])]

    for root in roots:
        if not root.is_dir():
            log.error("PATH is not a directory: %s", root)
            sys.exit(1)

    db_paths = None  # defaults to LOCAL_DB + DJMT_DB

    total_found = [0]
    total_files = [0]

    def _progress(scanned: int, total: int) -> None:
        total_files[0] = total
        total_found[0] = scanned
        print(f"FABLEGEAR_SCAN_TICK: {scanned}", flush=True)

    log.info("Dead-file scan: roots=%s", [str(r) for r in roots])
    try:
        result = scan_dead_files(roots, db_paths=db_paths, progress_cb=_progress, archive=_archive())

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
    from importer import import_directory
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

    aggregate = None
    root_sections: list[tuple[Path, str]] = []

    def _merge(report):
        nonlocal aggregate
        if aggregate is None:
            aggregate = report
            return
        aggregate.imported += report.imported
        aggregate.skipped += report.skipped
        aggregate.resumed += report.resumed
        aggregate.failed += report.failed
        aggregate.results.extend(report.results)

    if args.dry_run:
        log.info("DRY RUN — no writes will occur")
        try:
            with read_db(LOCAL_DB) as db:
                for index, root in enumerate(roots, start=1):
                    _log_root_step("Import preview", root, index, len(roots))
                    report = import_directory(root, db, dry_run=True)
                    _merge(report)
                    root_sections.append((root, report.summary()))
            summary_text = aggregate.summary() if aggregate else "No import sources were processed."
            summary_text = _append_root_breakdown(summary_text, root_sections)
            print(summary_text)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = _write_report("Import", f"preview_import_{timestamp}.txt", summary_text)
            if report_path:
                print(f"FABLEGEAR_REPORT_PATH: {report_path}", flush=True)
        except Exception:
            log.exception("Dry-run import failed")
            sys.exit(1)
    else:
        log.info("Importing from %d source folder(s)", len(roots))
        try:
            with write_db(LOCAL_DB) as db:
                for index, root in enumerate(roots, start=1):
                    _log_root_step("Import", root, index, len(roots))
                    report = import_directory(root, db, dry_run=False, resume=args.resume)
                    _merge(report)
                    root_sections.append((root, report.summary()))
            summary_text = aggregate.summary() if aggregate else "No import sources were processed."
            summary_text = _append_root_breakdown(summary_text, root_sections)
            print(summary_text)
            if aggregate and aggregate.failed > 0:
                log.warning("%d tracks failed to import — see log above", aggregate.failed)
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
    log.info("Relocating: %s → %s", old_root, new_root)
    try:
        with write_db(LOCAL_DB) as db:
            results = relocate_directory(old_root, new_root, db, archive=_archive())
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
    """Scan one or more PATHs for acoustically identical files and write a CSV report."""
    from duplicate_detector import scan_duplicates, write_csv_report, write_trash_rescue_report

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
    root_label = ", ".join(str(r) for r in roots)
    log.info("Scanning for duplicates under: %s (workers=%d, match=%s)", root_label, workers, args.match_mode)
    log.info("This may take a while for large libraries — progress logged every %d files", 100)

    # ── Checkpoint: resume an interrupted scan, or start over with --checkpoint-action reset
    ckpt = _duplicates_checkpoint(roots, args)
    if len(roots) > 1:
        log.info(
            "Selected folders are scanned together as one comparison set so duplicates across different source folders are not missed."
        )

    try:
        result = scan_duplicates(
            root,
            max_workers=workers,
            match_mode=args.match_mode,
            fuzzy_threshold=args.fuzzy_threshold,
            checkpoint=ckpt,
            archive=_archive(),
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
        from FableGear.config import DJMT_DB as _DJMT_DB  # noqa: PLC0415
    except ImportError:
        try:
            from config import DJMT_DB as _DJMT_DB        # noqa: PLC0415
        except Exception:
            _DJMT_DB = None
    db_path = _DJMT_DB if (_DJMT_DB and _DJMT_DB.exists()) else LOCAL_DB

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
    try:
        with write_db(db_path) as db:
            summary = prune_files(
                remove_paths,
                db,
                log=lambda m: print(m, flush=True),
                permanent=args.permanent,
                keeper_map=keeper_map,
                archive=_archive(),
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
    """Return the DB path to operate on (explicit override > DJMT > LOCAL)."""
    if cli_db_path:
        candidate = Path(cli_db_path).expanduser()
        if not candidate.exists():
            log.error("DB path does not exist: %s", candidate)
            sys.exit(1)
        return candidate

    try:
        from FableGear.config import DJMT_DB as _DJMT_DB  # noqa: PLC0415
    except ImportError:
        try:
            from config import DJMT_DB as _DJMT_DB  # noqa: PLC0415
        except Exception:
            _DJMT_DB = None

    if _DJMT_DB and _DJMT_DB.exists():
        return _DJMT_DB
    if LOCAL_DB is None:
        log.error("No Rekordbox database path available")
        sys.exit(1)
    return LOCAL_DB


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
    try:
        result = scan_duplicates(
            root=scan_files[0].parent,
            files_override=scan_files,
            max_workers=workers,
            match_mode=match_mode,
            fuzzy_threshold=fuzzy_threshold,
            archive=_archive(),
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
                archive=_archive(),
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

        log.info(
            "Retry mode: processing %d specific file(s) — BPM:%s KEY:%s NORMALIZE:%s FORCE:%s",
            len(specific_paths), detect_bpm, detect_key, normalise, args.force,
        )

        all_results = []
        total = len(specific_paths)
        done = clean = errors = edited = tags_written = bpm_key_written = quarantined = enriched = 0

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

    all_results = []
    root_sections: list[tuple[Path, str]] = []
    for index, root in enumerate(roots, start=1):
        _log_root_step("Process", root, index, len(roots))
        try:
            results = process_directory(
                root,
                detect_bpm=detect_bpm,
                detect_key=detect_key,
                normalise=normalise,
                force=args.force,
                max_workers=max(1, args.workers),
                quarantine_dir=_quarantine_dir,
                enrich_tags=args.enrich_tags,
            )
            all_results.extend(results)
            # Persist this root's analysis immediately — a multi-drive run
            # interrupted on drive 3 must keep drives 1-2 in the archive.
            _persist_process_results(results, _archive())
            root_total = len(results)
            root_bpm_written = sum(1 for r in results if r.bpm_written)
            root_key_written = sum(1 for r in results if r.key_written)
            root_normalised = sum(1 for r in results if r.normalised)
            root_errored = sum(1 for r in results if not r.ok)
            root_quarantined = sum(1 for r in results if r.quarantined)
            root_skipped_bpm = sum(1 for r in results if r.skipped_bpm)
            root_skipped_key = sum(1 for r in results if r.skipped_key)
            root_lines = [f"{root_total} files were analyzed."]
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

    done = 0
    success_count = 0
    error_count = 0
    root_sections: list[tuple[Path, str]] = []

    def _emit_progress() -> None:
        print(
            "FABLEGEAR_PROGRESS: " + json.dumps({
                "done":      done,
                "total":     total,
                "remaining": total - done,
                "converted": success_count,
                "errors":    error_count,
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
                _fg_archive.relink_content(rec.id, str(dest))
            _fg_archive.log_operation(
                "convert", str(dest), status="ok",
                metadata={"from": str(src_path), "format": target_format},
            )
        except Exception as exc:
            log.warning("Archive update failed for convert %s: %s", src_path, exc)

    _emit_progress()

    for root_index, (root, tracks) in enumerate(tracks_by_root, start=1):
        _log_root_step("Convert", root, root_index, len(tracks_by_root))
        root_success = 0
        root_errors = 0
        root_total = len(tracks)

        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_convert_one, track): track for track in tracks}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        ok, msg, name = future.result()
                    except Exception as exc:
                        ok, msg, name = False, str(exc), futures[future].path.name
                    done += 1
                    if ok:
                        success_count += 1
                        root_success += 1
                        log.info("✓ %s: %s", name, msg)
                    else:
                        error_count += 1
                        root_errors += 1
                        log.error("✗ %s: %s", name, msg)
                    _emit_progress()
        else:
            for track_index, track in enumerate(tracks, start=1):
                log.info("[%d/%d] Converting %s", track_index, root_total, track.path.name)
                ok, msg = _convert_file(track.path, target_format)
                _journal_convert(track.path, ok, msg)
                done += 1
                if ok:
                    success_count += 1
                    root_success += 1
                    log.info("✓ %s: %s", track.path.name, msg)
                else:
                    error_count += 1
                    root_errors += 1
                    log.error("✗ %s: %s", track.path.name, msg)
                _emit_progress()

        root_lines = [f"{root_success} of {root_total} files were converted to {target_format.upper()}."]
        if root_errors:
            root_lines.append(f"{root_errors} files had errors — check the log above.")
        else:
            root_lines.append("No errors.")
        root_sections.append((root, "\n".join(root_lines)))

    if _fg_archive is not None and success_count:
        try:
            _fg_archive.log_operation(
                "convert_batch",
                metadata={
                    "roots": [str(r) for r in roots],
                    "format": target_format,
                    "converted": success_count,
                    "errors": error_count,
                },
            )
        except Exception as exc:
            log.warning("Archive batch log failed for convert: %s", exc)

    fmt_upper = target_format.upper()
    lines = ["Done converting.", "", f"{success_count} of {total} files were converted to {fmt_upper}."]
    if error_count:
        lines.append(f"{error_count} files had errors — check the log above.")
    else:
        lines.append("No errors.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _emit_report(_append_root_breakdown("\n".join(lines), root_sections), "Convert", f"convert_{timestamp}.txt")

    if error_count > 0:
        log.warning("%d files had errors — check log above", error_count)


def cmd_organize(args: argparse.Namespace) -> None:
    """Consolidate audio files into Artist / Album / Track hierarchy."""
    from pathlib import Path
    from library_organizer import organize_library

    primary = Path(args.source)
    extra   = [Path(p) for p in (getattr(args, "also_scan", None) or [])]
    sources = [primary] + extra
    target  = Path(args.target)
    mode    = getattr(args, "mode", "assimilate")

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

    # Past-participle forms used in both dry-run plans and live reports.
    # Using full past-tense words avoids "{verb}ed" suffixing producing "copyed".
    action_past = "copied" if mode == "integrate" else "moved"
    action_verb = action_past
    log.info(
        "Organizing  sources=%s  target=%s  mode=%s  dry_run=%s  workers=%d  mix_threshold=%.0f min",
        [str(s) for s in sources], target, mode, dry_run, max_workers, threshold / 60,
    )

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
                archive=_archive(),
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
                f"{root_moved} files would be {action_past} into Artist / Album / Track folders."
                if dry_run else
                f"{root_moved} files were {action_past} into Artist / Album / Track folders."
            )
        if root_skipped:
            root_lines.append(f"{root_skipped} were already at the destination — left alone.")
        if root_conflicts:
            root_lines.append(f"{root_conflicts} name clashes were handled by renaming.")
        if root_errors:
            root_lines.append(f"{root_errors} files had errors — check the log above.")
        root_sections.append((source, "\n".join(root_lines)))

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
            lines.append(f"  {moved} would be {action_past} into Artist / Album / Track folders.")
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
            lines.append(f"{moved} files were {action_verb} into Artist / Album / Track folders.")
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

    max_workers = max(1, getattr(args, "workers", 1))

    if dry_run:
        log.info("DRY RUN — no files will be copied. Pass --no-dry-run to execute.")

    log.info(
        "Novel scan  sources=%s  dest=%s  dry_run=%s  workers=%d  match_mode=%s",
        [str(s) for s in sources], dest, dry_run, max_workers, match_mode,
    )

    total_src = 0
    dest_index_size = 0
    aggregate_novel = []
    aggregate_present = []
    aggregate_errors = []
    root_sections: list[tuple[Path, str]] = []
    verb = "would be copied" if dry_run else "copied"

    for index, source in enumerate(sources, start=1):
        _log_root_step("Novelty", source, index, len(sources))
        root_result = scan_novel(
            [source], dest,
            dry_run=dry_run,
            max_workers=max_workers,
            match_mode=match_mode,
            archive=_archive(),
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
            f"Destination index: {root_result.dest_index_size} tracks.",
        ]
        if root_novel:
            root_lines.append(f"{root_novel} novel tracks {verb} to destination.")
        if root_present:
            root_lines.append(f"{root_present} tracks confirmed already present — skipped.")
        if root_errors:
            root_lines.append(f"{root_errors} errors — check log above.")
        root_sections.append((source, "\n".join(root_lines)))

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
        f"Destination index: {result.dest_index_size} tracks.",
        f"Comparison mode: {match_mode}.",
        "",
    ]
    if novel:
        lines.append(f"  {novel} novel tracks {verb} to destination.")
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

    results = []
    root_sections: list[tuple[Path, str]] = []

    try:
        if dry_run:
            for index, root in enumerate(roots, start=1):
                _log_root_step("Rename", root, index, len(roots))
                root_results = rename_directory(root, db=None, dry_run=True, max_workers=max_workers, archive=_archive())
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
            with write_db(LOCAL_DB) as db:
                for index, root in enumerate(roots, start=1):
                    _log_root_step("Rename", root, index, len(roots))
                    root_results = rename_directory(root, db=db, dry_run=False, max_workers=max_workers, archive=_archive())
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
    p_usb.add_argument("mount", help="Mount point of the drive, e.g. /Volumes/GIGSTICK")
    p_usb.set_defaults(func=cmd_usb_inspect)

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
    p_dupes.set_defaults(func=cmd_duplicates)

    # ── rekordbox-dedupe ──
    p_rb_dupes = sub.add_parser(
        "rekordbox-dedupe",
        help="Scan Rekordbox DB tracks for duplicates and optionally prune with playlist rethreading",
    )
    p_rb_dupes.add_argument(
        "--db-path",
        metavar="PATH",
        help="Explicit Rekordbox DB path (default: DJMT_DB when mounted, else LOCAL_DB)",
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
    p_convert.set_defaults(func=cmd_convert)

    # ── organize ──
    p_organize = sub.add_parser(
        "organize",
        help="Consolidate files into Artist / Album / Track hierarchy",
    )
    p_organize.add_argument(
        "source",
        metavar="SOURCE",
        help="Directory to scan for audio files",
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
        help="Home library root to copy novel tracks into",
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


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)

    log.debug("Command: %s", args.command)
    log.debug("Args: %s", vars(args))

    args.func(args)


if __name__ == "__main__":
    main()
