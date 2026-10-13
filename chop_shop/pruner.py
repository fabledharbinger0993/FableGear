"""
FableGear / pruner.py -- the Remove step of De-Duplicate.

Loads a duplicate_report.csv, enriches entries with live file metadata,
and executes confirmed removals:

  1. Moves each duplicate file into the "FableGear Duplicates" folder that sits
     NEXT TO the music library (never inside it, so later scans skip it). The
     file keeps its path relative to the library, so it is easy to find and
     put back. Nothing is deleted.
  2. Removes the duplicate's row from the FableGear database (fg_content),
     after moving its playlist memberships, cues, beat grid and any metadata
     the kept track is missing onto the kept track.

Rekordbox's own database is never opened or written.

Called from routes_tools.py and cli.py. Never called directly by the user.

Trash rescue gate:
  Before any removal can execute, trash_rescue_preflight(csv_path) must be
  called and return an empty issues list. If the companion rescue report
  has unresolved items, or the CSV contains keep_in_trash=YES rows, the
  preflight raises TrashRescueRequired. The removal will not proceed until
  the user has reviewed and cleared those items. FableGear does not offer
  an automated rescue step -- the user must act manually.

Public interface:
  trash_rescue_preflight(csv_path) -> None   (raises TrashRescueRequired)
  load_report(csv_path, archive=None) -> list[DupeGroup]
  duplicates_folder(library_root, sample_path) -> Path
  prune_files(file_paths, archive, ...) -> dict
"""

import csv
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config import DUPLICATES_DIR_NAME

_log = logging.getLogger(__name__)

# ── Trash rescue gate ────────────────────────────────────────────────────────

class TrashRescueRequired(RuntimeError):
    """
    Raised by trash_rescue_preflight() when unresolved rescue items are found.
    The prune must not proceed until the user has acted on the rescue report.
    """
    def __init__(self, message: str, issues: list[str]):
        super().__init__(message)
        self.issues = issues


def trash_rescue_preflight(csv_path: Path) -> None:
    """
    Check whether it is safe to proceed with a prune run against csv_path.

    Raises TrashRescueRequired if either:
      1. A companion rescue report (.txt, same stem prefix) exists and
         contains file paths — meaning unique-in-trash tracks were found
         during the last scan and have not been cleared.
      2. The CSV itself contains rows with keep_in_trash=YES — meaning at
         least one duplicate group's best surviving copy is inside a trash
         folder. Pruning the REVIEW_REMOVE copies for that group while the
         KEEP is still in trash would leave no safe copy anywhere.

    FableGear does not offer an automated rescue step. The user must manually
    move the flagged files before the prune can run.

    Parameters
    ----------
    csv_path : Path
        Path to the duplicate_report CSV that will be fed to load_report().
    """
    issues: list[str] = []

    # ── Check 1: companion rescue report ─────────────────────────────────────
    # The rescue report is written alongside the CSV with a parallel name:
    #   duplicate_report_20260403_013019.csv
    #   trash_rescue_report_20260403_013019.txt
    stem = csv_path.stem
    rescue_stem = stem.replace("duplicate_report", "trash_rescue_report")
    if rescue_stem == stem:
        rescue_stem = f"trash_rescue_{stem}"
    rescue_path = csv_path.with_name(rescue_stem).with_suffix(".txt")

    if rescue_path.exists():
        # Scan the rescue report for actual file paths (lines starting with /)
        with open(rescue_path, encoding="utf-8", errors="replace") as f:
            rescue_paths = [
                ln.strip() for ln in f
                if ln.strip().startswith("/") or ln.strip().startswith("\\")
            ]
        if rescue_paths:
            issues.append(
                f"Rescue report lists {len(rescue_paths)} track(s) that need manual "
                f"attention before pruning: {rescue_path}"
            )

    # ── Check 2: keep_in_trash rows in the CSV ────────────────────────────────
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if "keep_in_trash" in (reader.fieldnames or []):
                trapped = [
                    row["file_path"]
                    for row in reader
                    if row.get("keep_in_trash", "").strip().upper() == "YES"
                    and row.get("action", "").strip() == "KEEP"
                ]
                if trapped:
                    issues.append(
                        f"{len(trapped)} group(s) have their best copy inside a trash folder "
                        f"(keep_in_trash=YES). Move those files to a safe location before pruning."
                    )
    except Exception:
        pass  # If the CSV can't be read here, load_report will surface the error

    if issues:
        lines = [
            "╔══════════════════════════════════════════════════════════════════╗",
            "║  !!! PRUNE BLOCKED — TRASH RESCUE REQUIRED !!!                  ║",
            "║                                                                  ║",
            "║  Unique or possibly-unique tracks were found inside trash or     ║",
            "║  trash-adjacent folders. FableGear does not offer an automated   ║",
            "║  rescue step. You must review and act on these manually before  ║",
            "║  any pruning can proceed.                                        ║",
            "╠══════════════════════════════════════════════════════════════════╣",
        ]
        for issue in issues:
            # Word-wrap each issue to ~66 chars
            words = issue.split()
            line = ""
            for word in words:
                if len(line) + len(word) + 1 > 64:
                    lines.append(f"║  {line:<66}║")
                    line = word
                else:
                    line = (line + " " + word).strip()
            if line:
                lines.append(f"║  {line:<66}║")
        lines.append("╚══════════════════════════════════════════════════════════════════╝")
        raise TrashRescueRequired("\n".join(lines), issues)


# ── Quality ranking ───────────────────────────────────────────────────────────
# Higher tier = higher quality. Used to re-rank within each duplicate group.

FORMAT_TIER: dict[str, int] = {
    ".aiff": 6, ".aif": 6, ".aifc": 6,
    ".wav":  5,
    ".flac": 4,
    ".m4a":  3, ".m4p": 3, ".mp4": 3, ".m4v": 3,
    ".mp3":  2,
    ".ogg":  1, ".opus": 1,
}

RARP_SCORE: dict[str, int] = {
    "PN":  3,   # Pioneer Numbered
    "MIK": 2,   # Mixed In Key tagged
    "RAW": 1,   # Neither
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DupeEntry:
    group_id:      str
    action:        str            # KEEP | REVIEW_REMOVE (re-assigned on load)
    rank:          str            # PN | MIK | RAW
    file_path:     str
    file_size_mb:  float
    bpm:           str | None  # CSV-sourced text like "127.00"; TrackInfo/FG-DB use float — do not compare directly
    key:           str | None
    filename:      str
    # enriched after load
    format_ext:    str  = ""
    format_tier:   int  = 0
    exists_on_disk:bool = True
    in_db:         bool = False
    tag_completeness: int = 0

    @property
    def quality_score(self) -> tuple:
        """Higher = better. Used to sort within a group.

        Delegates to dedupe_sort_key so the pruner ranks by the exact same
        rule the duplicate detector used to write the reviewed report — what a
        human approves as the keeper is what actually survives.
        """
        return dedupe_sort_key(Path(self.file_path), self.rank)


@dataclass
class DupeGroup:
    group_id:     str
    entries:      list[DupeEntry] = field(default_factory=list)
    keep_in_trash: bool = False  # True when the KEEP file lives in a trash folder

    @property
    def keep(self) -> DupeEntry | None:
        return next((e for e in self.entries if e.action == "KEEP"), None)

    @property
    def remove_candidates(self) -> list[DupeEntry]:
        # Safety lock: if the best surviving copy is in a trash folder, pruning
        # the REVIEW_REMOVE files would leave no safe copy once trash is cleared.
        # Return nothing so the pruner can never act on this group regardless of
        # how it was called or whether preflight was skipped.
        if self.keep_in_trash:
            return []
        return [e for e in self.entries if e.action == "REVIEW_REMOVE"]


# ── Tag completeness helper ───────────────────────────────────────────────────

def _count_tags(path: Path) -> int:
    """
    Count how many meaningful tags the file has.
    Used to prefer well-tagged copies during deduplication.
    Checks: title, artist, album, BPM, key, year, genre.
    Returns 0–7.
    """
    if not path.exists():
        return 0
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(str(path), easy=False)
        if audio is None or audio.tags is None:
            return 0
        tags = audio.tags
        tag_type = type(tags).__name__
        is_vorbis = "VCFLACDict" in tag_type or "VComment" in tag_type
        is_mp4    = "MP4Tags" in tag_type or "MP4" in tag_type
        score = 0
        def _has(id3_key, vorbis_key, mp4_key=None):
            nonlocal score
            try:
                if is_vorbis:
                    v = tags.get(vorbis_key.lower())
                    if v and str(v[0] if isinstance(v, list) else v).strip():
                        score += 1
                elif is_mp4 and mp4_key:
                    v = tags.get(mp4_key)
                    if v and str(v[0] if isinstance(v, list) else v).strip():
                        score += 1
                else:
                    f = tags.get(id3_key)
                    if f and str(f).strip():
                        score += 1
            except Exception:
                pass
        _has("TIT2", "title",       "©nam")
        _has("TPE1", "artist",      "©ART")
        _has("TALB", "album",       "©alb")
        _has("TBPM", "bpm",         "tmpo")
        _has("TKEY", "initialkey",  "----:com.apple.iTunes:initialkey")
        _has("TDRC", "date",        "©day")
        _has("TCON", "genre",       "©gen")
        return score
    except Exception:
        return 0


# ── Canonical keeper ranking (single source of truth) ─────────────────────────

def dedupe_sort_key(path: Path, rank: str) -> tuple:
    """
    The canonical "which copy to keep" ordering for a duplicate group.
    Higher sorts first; the highest is the keeper.

    This is the ONE definition shared by the duplicate detector (which writes
    the KEEP recommendation into the report a human reviews) and the pruner
    (which executes the deletion). Both rank by this exact key so the keeper a
    human sees in the CSV is precisely the keeper that survives — see
    duplicate_detector._build/_rank sites, which import this.

    Order of precedence: format quality tier, then file size, then RARP rank
    (PN > MIK > RAW), then tag completeness.
    """
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0.0
    return (
        FORMAT_TIER.get(path.suffix.lower(), 0),
        size_mb,
        RARP_SCORE.get(rank, 0),
        _count_tags(path),
    )


# ── Public: load report ───────────────────────────────────────────────────────

def load_report(csv_path: Path, archive=None) -> list[DupeGroup]:
    """
    Read a duplicate_report.csv, enrich each entry with live disk and DB data,
    re-rank within each group by quality, and return the structured groups.

    archive is an optional FableGear database, used to flag which files are
    currently tracked in fg_content.
    """
    # Build a lookup of paths that exist in the database
    db_paths: set[str] = set()
    if archive is not None:
        try:
            db_paths = archive.known_paths()
        except Exception:
            pass  # DB unavailable — just skip in_db flagging

    groups: dict[str, DupeGroup] = {}

    # Track which group IDs are flagged keep_in_trash from the CSV column.
    # Collected separately so we can set it after all entries are loaded.
    trash_flagged_groups: set[str] = set()

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fp = row.get("file_path", "").strip()
            if not fp:
                continue
            entry = DupeEntry(
                group_id     = row.get("group_id", "").strip(),
                action       = row.get("action", "").strip(),
                rank         = row.get("rank", "RAW").strip(),
                file_path    = fp,
                file_size_mb = float(row.get("file_size_mb") or 0),
                bpm          = row.get("bpm") or None,
                key          = row.get("key") or None,
                filename     = row.get("filename", Path(fp).name),
            )
            p = Path(fp)
            entry.format_ext     = p.suffix.lower()
            entry.format_tier    = FORMAT_TIER.get(entry.format_ext, 0)
            entry.exists_on_disk = p.exists()
            entry.in_db          = fp in db_paths
            # Compute tag completeness score from live file tags
            entry.tag_completeness = _count_tags(p)

            gid = entry.group_id
            if gid not in groups:
                groups[gid] = DupeGroup(gid)
            groups[gid].entries.append(entry)

            if row.get("keep_in_trash", "").strip().upper() == "YES":
                trash_flagged_groups.add(gid)

    # Re-rank: sort each group by quality descending, reassign KEEP to #1
    for group in groups.values():
        group.entries.sort(key=lambda e: e.quality_score, reverse=True)
        for i, entry in enumerate(group.entries):
            entry.action = "KEEP" if i == 0 else "REVIEW_REMOVE"
        # Apply trash lock — locked regardless of re-ranking outcome
        if group.group_id in trash_flagged_groups:
            group.keep_in_trash = True

    # Drop groups with only one entry (nothing to prune)
    return [g for g in groups.values() if len(g.entries) > 1]


# ── Where duplicates go ──────────────────────────────────────────────────────

def duplicates_folder(library_root: Path | None, sample_path: Path | None = None) -> Path:
    """The "FableGear Duplicates" folder for a library: NEXT TO the library root.

    Beside the library, not inside it, so a scan of the library never sees it
    (scanners also skip the folder by name, see config.SKIP_DIRS). When the library
    root is a whole drive (a mount point, or a folder directly under /Volumes) there
    is no "next to", so the folder sits at the top of that drive instead.
    """
    root = Path(library_root) if library_root else None
    if root is None and sample_path is not None:
        parts = Path(sample_path).parts
        if len(parts) >= 3 and parts[1] == "Volumes":
            root = Path(*parts[:3])
    if root is None:
        from config import MUSIC_ROOT
        root = Path(MUSIC_ROOT)
    parent = root.parent
    is_drive_root = os.path.ismount(root) or parent == Path("/Volumes")
    base = root if is_drive_root else parent
    return base / DUPLICATES_DIR_NAME


def _archive_destination(src: Path, library_root: Path | None, dup_dir: Path) -> Path:
    """Where src goes: its path relative to the library root when it has one, so the
    original location is obvious and same-named files from different folders never collide."""
    rel = None
    if library_root:
        try:
            rel = src.relative_to(library_root)
        except ValueError:
            rel = None
    base = dup_dir / (Path(library_root).name if library_root and rel else "") / (rel or src.name)
    dest = base
    n = 2
    while dest.exists():
        dest = base.with_name(f"{base.stem} ({n}){base.suffix}")
        n += 1
    return dest


def _move_verified(src: Path, dest: Path) -> None:
    """Move src to dest. Same drive: an instant rename. Another drive: copy, check
    the size, and only then remove the original, so an interrupted move never loses audio."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        return
    except OSError:
        pass  # different filesystem
    shutil.copy2(src, dest)
    if dest.stat().st_size != src.stat().st_size:
        dest.unlink(missing_ok=True)
        raise OSError(f"copy of {src.name} did not match the original size; original left in place")
    src.unlink()


# ── Moving a duplicate's data onto the track that is kept ────────────────────
# Before a duplicate's fg_content row is removed, everything that points at it
# is moved to the kept track or it would be lost (the tables cascade on delete).
#
#   playlists   a track in a playlist the keeper is already in: drop the duplicate's slot,
#               otherwise re-point it to the keeper.
#   cues        hot cues are slotted (kind 1, slot N): the keeper's cue wins a slot
#               conflict. Memory cues and loops have no slot: kept unless the keeper
#               already has one at the same position (and kind).
#   beat grid   the keeper's grid wins; the duplicate's is copied only if the keeper has none.
#   metadata    only fields the keeper has empty are filled in. The keeper's own
#               values always win.

_BACKFILL_COLUMNS = (
    "artist", "album", "genre", "label", "year", "comment", "bpm", "key", "title",
    "track_number", "disc_number", "rating",
)


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == 0


def _backfill_metadata(cur, remove_id: int, keeper_id: int, emit) -> int:
    cols = ", ".join(_BACKFILL_COLUMNS)
    dup = cur.execute(f"SELECT {cols} FROM fg_content WHERE id = ?", (remove_id,)).fetchone()
    keep = cur.execute(f"SELECT {cols} FROM fg_content WHERE id = ?", (keeper_id,)).fetchone()
    if dup is None or keep is None:
        return 0
    updates = {}
    for name, dup_value, keep_value in zip(_BACKFILL_COLUMNS, dup, keep):
        if _empty(keep_value) and not _empty(dup_value):
            updates[name] = dup_value
    if updates:
        assignments = ", ".join(f"{name} = ?" for name in updates)
        cur.execute(
            f"UPDATE fg_content SET {assignments}, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (*updates.values(), keeper_id),
        )
        emit(f"      ↪  {len(updates)} metadata field(s) backfilled onto keeper")
    return len(updates)


def _rethread_playlists(cur, remove_id: int, keeper_id: int) -> tuple[int, int, set[int]]:
    """Returns (re-pointed, dropped, ids of the playlists touched)."""
    rows = cur.execute(
        "SELECT id, playlist_id FROM fg_playlist_song WHERE content_id = ?", (remove_id,)
    ).fetchall()
    moved = dropped = 0
    touched: set[int] = set()
    for row_id, playlist_id in rows:
        touched.add(playlist_id)
        already = cur.execute(
            "SELECT 1 FROM fg_playlist_song WHERE content_id = ? AND playlist_id = ?",
            (keeper_id, playlist_id),
        ).fetchone()
        if already:
            cur.execute("DELETE FROM fg_playlist_song WHERE id = ?", (row_id,))
            dropped += 1
        else:
            cur.execute("UPDATE fg_playlist_song SET content_id = ? WHERE id = ?", (keeper_id, row_id))
            moved += 1
    return moved, dropped, touched


def _rethread_cues(cur, remove_id: int, keeper_id: int) -> tuple[int, int]:
    """Returns (copied, dropped). Keeper wins any conflict."""
    dup = cur.execute("SELECT id, kind, slot, in_msec FROM fg_cue WHERE content_id = ?", (remove_id,)).fetchall()
    if not dup:
        return 0, 0
    keep = cur.execute("SELECT kind, slot, in_msec FROM fg_cue WHERE content_id = ?", (keeper_id,)).fetchall()
    used_slots = {slot for kind, slot, _ in keep if kind == 1 and slot is not None}
    used_positions = {(kind, in_msec) for kind, _slot, in_msec in keep if kind != 1}
    copied = dropped = 0
    for cue_id, kind, slot, in_msec in dup:
        if kind == 1 and slot is not None:  # hot cue: the slot is the pad number
            conflict = slot in used_slots
            used_slots.add(slot)
        else:
            conflict = (kind, in_msec) in used_positions
            used_positions.add((kind, in_msec))
        if conflict:
            cur.execute("DELETE FROM fg_cue WHERE id = ?", (cue_id,))
            dropped += 1
        else:
            cur.execute("UPDATE fg_cue SET content_id = ? WHERE id = ?", (keeper_id, cue_id))
            copied += 1
    return copied, dropped


def _rethread_beatgrid(cur, remove_id: int, keeper_id: int) -> int:
    """Move the duplicate's beat grid over only if the keeper has none. Returns rows moved."""
    if cur.execute("SELECT 1 FROM fg_beatgrid WHERE content_id = ? LIMIT 1", (keeper_id,)).fetchone():
        return 0
    cur.execute("UPDATE fg_beatgrid SET content_id = ? WHERE content_id = ?", (keeper_id, remove_id))
    return cur.rowcount


def _has_associations(cur, content_id: int) -> bool:
    for table in ("fg_playlist_song", "fg_cue"):
        if cur.execute(f"SELECT 1 FROM {table} WHERE content_id = ? LIMIT 1", (content_id,)).fetchone():
            return True
    return False


def _refresh_playlist_counts(archive, cur, playlist_ids) -> None:
    for playlist_id in playlist_ids:
        archive._refresh_playlist_count(cur, playlist_id)


# ── Public: remove duplicates ────────────────────────────────────────────────

def prune_files(
    file_paths: list[str],
    archive,
    log=None,
    keeper_map: dict[str, str] | None = None,
    should_cancel=None,
    library_root: str | Path | None = None,
) -> dict:
    """
    Move each duplicate file into the FableGear Duplicates folder beside the
    library and remove its row from the FableGear database.

    archive: the FableGear database (fablegear_database.FableGearDatabase).
    keeper_map (optional): {duplicate_path -> kept_path}. When given, the
      duplicate's playlist memberships, cues, beat grid and any metadata the
      kept track is missing are moved onto the kept track before the row is
      removed. The keeper's own values always win. Without a keeper, a track
      that is in a playlist or has cues is protected: it is left exactly where
      it is, and nothing is removed.

    For each file, the file is moved first and the database changed second, in
    one transaction. If the database step fails the file is moved back, so a file
    and its row never disagree.

    Returns { db_removed, files_moved, skipped, errors, duplicates_dir,
              playlists_rethreaded, associations_rethreaded, metadata_backfilled,
              cancelled }
    """

    def emit(msg: str) -> None:
        if log:
            log(msg)
        else:
            _log.info(msg)

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    root = Path(library_root) if library_root else None
    dup_dir = duplicates_folder(root, Path(file_paths[0]) if file_paths else None)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    emit(f"Duplicates folder → {dup_dir}")

    db_removed = files_moved = skipped = 0
    playlists_rethreaded = associations_rethreaded = metadata_backfilled = 0
    errors: list[str] = []
    moves: list[dict] = []

    for path in file_paths:
        if cancelled():
            emit("  ⚠  Cancel requested — stopping. Files already moved stay in the Duplicates folder.")
            break
        src = Path(path)
        name = src.name
        keeper_path = (keeper_map or {}).get(path)

        def _row(cur, p: str):
            return cur.execute("SELECT id FROM fg_content WHERE file_path = ?", (p,)).fetchone()

        # Safety gate, before anything moves: a track that is in a playlist or has cues
        # needs a kept track to receive them.
        try:
            with archive.connection() as conn:
                cur = conn.cursor()
                dup_row = _row(cur, path)
                keeper_row = _row(cur, keeper_path) if keeper_path else None
                if dup_row is not None and keeper_row is None and _has_associations(cur, dup_row[0]):
                    skipped += 1
                    emit(f"    Skip — {name} is in playlists or has cues, and its kept copy "
                         f"isn't in the database. Nothing was changed.")
                    continue
        except Exception as exc:
            errors.append(f"Database check failed for {name}: {exc}")
            emit(f"    ✗  {errors[-1]}")
            continue

        if not src.exists():
            skipped += 1
            emit(f"    Skip — not found on disk: {name}")
            continue

        dest = _archive_destination(src, root, dup_dir)
        try:
            _move_verified(src, dest)
        except Exception as exc:
            errors.append(f"Could not move {name}: {exc}")
            emit(f"    Move ✗  {errors[-1]}")
            continue

        # Database step, one transaction. Any failure puts the file back.
        try:
            path_playlists = path_assoc = path_meta = 0
            removed = False
            with archive.transaction() as conn:
                cur = conn.cursor()
                dup_row = _row(cur, path)
                keeper_row = _row(cur, keeper_path) if keeper_path else None
                if dup_row is not None:
                    remove_id = dup_row[0]
                    touched: set[int] = set()
                    if keeper_row is not None:
                        keeper_id = keeper_row[0]
                        path_meta = _backfill_metadata(cur, remove_id, keeper_id, emit)
                        moved, _dropped, touched = _rethread_playlists(cur, remove_id, keeper_id)
                        path_playlists = moved
                        cue_copied, cue_dropped = _rethread_cues(cur, remove_id, keeper_id)
                        grid_moved = _rethread_beatgrid(cur, remove_id, keeper_id)
                        path_assoc = cue_copied + (1 if grid_moved else 0)
                        if cue_copied or cue_dropped:
                            emit(f"      ↪  cues: {cue_copied} copied, {cue_dropped} dropped (keeper wins)")
                        if grid_moved:
                            emit("      ↪  beat grid copied (keeper had none)")
                    cur.execute("DELETE FROM fg_content WHERE id = ?", (remove_id,))
                    removed = cur.rowcount > 0
                    _refresh_playlist_counts(archive, cur, touched)
            if removed:
                db_removed += 1
                emit(f"    DB ✓  {name}")
            else:
                emit(f"    DB —  {name}  (not in database — file only)")
        except Exception as exc:
            try:
                _move_verified(dest, src)
                back = "The file was put back where it was."
            except Exception as back_exc:  # pragma: no cover - last-resort message
                back = f"The file could not be put back ({back_exc}); it is at {dest}."
            errors.append(f"Database error for {name}: {exc}. {back}")
            emit(f"    DB ✗  {errors[-1]}")
            continue

        playlists_rethreaded += path_playlists
        associations_rethreaded += path_assoc
        metadata_backfilled += path_meta
        files_moved += 1
        moves.append({
            "when": stamp, "original": str(src), "moved_to": str(dest),
            "kept": keeper_path, "in_database": removed,
        })
        emit(f"    Moved ✓  {name}")
        try:
            archive.log_operation(
                "prune", path, status="ok",
                metadata={"moved_to": str(dest), "kept": keeper_path},
            )
        except Exception as exc:
            _log.warning("Archive journal failed for %s: %s", path, exc)

    if moves:
        try:
            with open(dup_dir / "moves.jsonl", "a", encoding="utf-8") as fh:
                for entry in moves:
                    fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            errors.append(f"Could not write the moves log: {exc}")

    emit("")
    emit("═══ DE-DUPLICATE SUMMARY ═══")
    emit(f"  Database entries removed        : {db_removed}")
    if playlists_rethreaded:
        emit(f"  Playlist slots moved to keeper  : {playlists_rethreaded}")
    if associations_rethreaded:
        emit(f"  Cues / grids moved to keeper    : {associations_rethreaded}")
    if metadata_backfilled:
        emit(f"  Metadata fields backfilled      : {metadata_backfilled}")
    emit(f"  Files moved to Duplicates folder: {files_moved}")
    if skipped:
        emit(f"  Skipped                         : {skipped}")
    if errors:
        emit(f"  Errors                          : {len(errors)}")
        for err in errors:
            emit(f"    ⚠  {err}")
    emit(f"  Duplicates folder               : {dup_dir}")
    emit("════════════════════════════")

    if files_moved > 0:
        try:
            archive.log_operation(
                "prune_batch",
                metadata={
                    "db_removed": db_removed, "files_moved": files_moved,
                    "playlists_rethreaded": playlists_rethreaded,
                    "associations_rethreaded": associations_rethreaded,
                    "metadata_backfilled": metadata_backfilled,
                    "duplicates_dir": str(dup_dir),
                },
            )
        except Exception as exc:
            _log.warning("Archive batch journal failed: %s", exc)

    return {
        "db_removed": db_removed,
        "files_moved": files_moved,
        "skipped": skipped,
        "errors": errors,
        "duplicates_dir": str(dup_dir),
        "playlists_rethreaded": playlists_rethreaded,
        "associations_rethreaded": associations_rethreaded,
        "metadata_backfilled": metadata_backfilled,
        "cancelled": cancelled(),
    }
