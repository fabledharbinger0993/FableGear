"""
FableGear / pruner.py

Loads a duplicate_report.csv, enriches entries with live file metadata,
and executes confirmed prune operations:

  1. Removes selected tracks from DjmdContent (with DB backup via write_db)
  2. Moves selected files to ~/Trash/FableGear_Pruned_[timestamp]/
     — NOT a permanent delete. The folder stays in Trash until the user
       empties it on their own schedule.

Called from app.py. Never called directly by the user.

Trash rescue gate:
  Before any prune can execute, trash_rescue_preflight(csv_path) must be
  called and return an empty issues list. If the companion rescue report
  has unresolved items, or the CSV contains keep_in_trash=YES rows, the
  preflight raises TrashRescueRequired. The prune will not proceed until
  the user has reviewed and cleared those items. FableGear does not offer
  an automated rescue step — the user must act manually.

Public interface:
  trash_rescue_preflight(csv_path) -> None   (raises TrashRescueRequired)
  load_report(csv_path, db=None) -> list[DupeGroup]
  prune_files(file_paths, db, log=None) -> dict
"""

import csv
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import text

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
    bpm:           Optional[str]  # CSV-sourced text like "127.00"; TrackInfo/FG-DB use float — do not compare directly
    key:           Optional[str]
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
    def keep(self) -> Optional[DupeEntry]:
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

def load_report(csv_path: Path, db=None) -> list[DupeGroup]:
    """
    Read a duplicate_report.csv, enrich each entry with live disk and DB data,
    re-rank within each group by quality, and return the structured groups.

    db is an optional read-only database connection used to flag which files
    are currently referenced in DjmdContent.
    """
    # Build a lookup of paths that exist in the database
    db_paths: set[str] = set()
    if db is not None:
        try:
            db_paths = {row.FolderPath for row in db.get_content()}
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


# ── Public: prune files ───────────────────────────────────────────────────────


# ── Metadata backfill ─────────────────────────────────────────────────────────
# Field-level DjmdContent columns eligible for gap-filling from a duplicate
# before it's deleted. Deliberately excludes file-identity / operational
# columns (ID, FolderPath, FileType, DeviceID, rb_file_id, etc.) — only
# descriptive track data that a DJ would notice missing.
_BACKFILL_FIELDS = [
    "ArtistID", "AlbumID", "GenreID", "BPM", "Commnt", "Rating",
    "KeyID", "LabelID", "ComposerID", "RemixerID", "ReleaseYear", "ISRC",
]


def _backfill_metadata(remove_content, keeper_content, emit) -> int:
    """
    Copy descriptive track-data fields from a duplicate onto the keeper,
    but only into fields where the keeper is empty/null. The keeper's own
    non-empty values always win — this never overwrites populated data.

    Returns the number of fields filled.
    """
    filled = 0
    for field_name in _BACKFILL_FIELDS:
        keeper_value = getattr(keeper_content, field_name, None)
        if keeper_value not in (None, ""):
            continue
        dup_value = getattr(remove_content, field_name, None)
        if dup_value in (None, ""):
            continue
        setattr(keeper_content, field_name, dup_value)
        filled += 1
    if filled:
        emit(f"      ↪  {filled} metadata field(s) backfilled onto keeper")
    return filled


# ── Association re-threading ────────────────────────────────────────────────
# Before a duplicate content row is hard-deleted, every other table that
# references its ContentID must be re-pointed to the keeper or it becomes a
# silent orphan (pyrekordbox declares no cascade on these relationships, and
# Rekordbox itself never surfaces orphaned rows again). Three shapes of table:
#
#   "group"  — a join table with its own list/group id (playlist, my-tag,
#              history, sampler, related-tracks, hot-cue-banklist): if the
#              keeper is already a member of that same group, drop the
#              duplicate's slot; otherwise re-point ContentID in place.
#   "simple" — one attribute row per track with no group concept (mixer
#              param, active censor, tag list): copy over only if the keeper
#              has no row of its own; otherwise the keeper's existing row
#              wins and the duplicate's is dropped.
#   cues     — special-cased below; hot cues are slotted by Kind, memory
#              cues are positional and simply accumulate.

_GROUP_TABLES = [
    # (getter name on db, group id field, label)
    ("get_playlist_songs",         "PlaylistID",        "playlist"),
    ("get_history_songs",          "HistoryID",          "history"),
    ("get_my_tag_songs",           "MyTagID",            "my tag"),
    ("get_sampler_songs",          "SamplerID",          "sampler"),
    ("get_related_tracks_songs",   "RelatedTracksID",    "related tracks"),
    ("get_hot_cue_banklist_songs", "HotCueBanklistID",   "hot cue banklist"),
]

_SIMPLE_TABLES = [
    # (getter name on db, label)
    ("get_mixer_param",   "mixer params"),
    ("get_active_censor", "active censors"),
    ("get_tag_list_songs","tag list entries"),
]


def _rethread_group_table(db, getter_name: str, group_field: str, remove_id: str, keeper_id: str) -> tuple[int, int]:
    """
    Generic re-threader for join-style tables (group id + ContentID + TrackNo).
    Same rule as playlists: drop the duplicate's slot if the keeper is already
    a member of that same group, otherwise re-point ContentID to the keeper.

    Returns (rethreaded, dropped).
    """
    getter = getattr(db, getter_name)
    rows = getter(ContentID=remove_id).all()
    if not rows:
        return 0, 0
    rethreaded = 0
    dropped = 0
    for row in rows:
        group_id = getattr(row, group_field)
        already_there = getter(ContentID=keeper_id, **{group_field: group_id}).all()
        if already_there:
            db.session.delete(row)
            dropped += 1
        else:
            row.ContentID = keeper_id
            rethreaded += 1
    return rethreaded, dropped


def _copy_or_drop_simple(db, getter_name: str, remove_id: str, keeper_id: str) -> tuple[int, int]:
    """
    Generic handler for groupless singleton-style tables. If the keeper has
    no row of its own, the duplicate's row(s) are re-pointed over; otherwise
    the keeper's existing row wins and the duplicate's is dropped.

    Returns (copied, dropped).
    """
    getter = getattr(db, getter_name)
    dup_rows = getter(ContentID=remove_id).all()
    if not dup_rows:
        return 0, 0
    keeper_rows = getter(ContentID=keeper_id).all()
    if keeper_rows:
        for row in dup_rows:
            db.session.delete(row)
        return 0, len(dup_rows)
    for row in dup_rows:
        row.ContentID = keeper_id
    return len(dup_rows), 0


def _rethread_cues(db, remove_id: str, keeper_id: str) -> tuple[int, int]:
    """
    Hot cues are slotted by djmdCue.Kind (0 = memory cue; a nonzero Kind is
    the hot cue pad number, e.g. 1=A, 2=B, ... — a CDJ has one physical pad
    per Kind value, so two cues sharing a Kind genuinely conflict).
    Memory cues have no slot concept and just accumulate along the timeline;
    they're deduped by exact position (InMsec) to avoid literal duplicates.

    Conflict rule (same slot occupied on both sides): keeper's cue wins,
    duplicate's is dropped — the same gap-filling-only philosophy used for
    track metadata, extended to cues.

    Returns (copied, dropped).
    """
    dup_cues = db.get_cue(ContentID=remove_id).all()
    if not dup_cues:
        return 0, 0
    keeper_cues = db.get_cue(ContentID=keeper_id).all()
    keeper_hot_kinds = {c.Kind for c in keeper_cues if c.Kind}
    keeper_memory_positions = {c.InMsec for c in keeper_cues if not c.Kind}

    copied = 0
    dropped = 0
    for cue in dup_cues:
        if cue.Kind:  # hot cue — Kind is the slot
            if cue.Kind in keeper_hot_kinds:
                db.session.delete(cue)
                dropped += 1
            else:
                cue.ContentID = keeper_id
                keeper_hot_kinds.add(cue.Kind)
                copied += 1
        else:  # memory cue — positional, not slotted
            if cue.InMsec in keeper_memory_positions:
                db.session.delete(cue)
                dropped += 1
            else:
                cue.ContentID = keeper_id
                keeper_memory_positions.add(cue.InMsec)
                copied += 1
    return copied, dropped


def _rethread_cloud_export_playlists(db, remove_id: str, keeper_id: str) -> tuple[int, int]:
    """
    djmdCloudExportSongPlaylist has no pyrekordbox ORM model (as of 0.4.4),
    so this is handled with parameterized raw SQL instead of the ORM. Same
    group-membership rule as _rethread_group_table, keyed on
    CloudExportPlaylistID.
    """
    rows = db.session.execute(
        text("SELECT ID, CloudExportPlaylistID FROM djmdCloudExportSongPlaylist WHERE ContentID = :cid"),
        {"cid": remove_id},
    ).fetchall()
    if not rows:
        return 0, 0
    rethreaded = 0
    dropped = 0
    for row_id, playlist_id in rows:
        already_there = db.session.execute(
            text(
                "SELECT 1 FROM djmdCloudExportSongPlaylist "
                "WHERE ContentID = :kid AND CloudExportPlaylistID = :pid"
            ),
            {"kid": keeper_id, "pid": playlist_id},
        ).fetchone()
        if already_there:
            db.session.execute(
                text("DELETE FROM djmdCloudExportSongPlaylist WHERE ID = :id"), {"id": row_id}
            )
            dropped += 1
        else:
            db.session.execute(
                text("UPDATE djmdCloudExportSongPlaylist SET ContentID = :kid WHERE ID = :id"),
                {"kid": keeper_id, "id": row_id},
            )
            rethreaded += 1
    return rethreaded, dropped


def _rethread_recommend_likes(db, remove_id: str, keeper_id: str) -> tuple[int, int]:
    """
    djmdRecommendLike has no pyrekordbox ORM model (as of 0.4.4), so this is
    handled with parameterized raw SQL. Unlike the other FK tables it's a
    pairwise relation (ContentID1, ContentID2), not a track → group link, so
    it needs its own logic: re-point whichever column referenced the
    duplicate, unless that would create a self-pair (keeper recommended to
    itself) or duplicate an existing pair in either column order — both
    cases drop the row instead.
    """
    rethreaded = 0
    dropped = 0
    for col_self, col_other in (("ContentID1", "ContentID2"), ("ContentID2", "ContentID1")):
        rows = db.session.execute(
            text(f"SELECT ID, {col_other} FROM djmdRecommendLike WHERE {col_self} = :rid"),
            {"rid": remove_id},
        ).fetchall()
        for row_id, other_id in rows:
            if other_id == keeper_id:
                db.session.execute(text("DELETE FROM djmdRecommendLike WHERE ID = :id"), {"id": row_id})
                dropped += 1
                continue
            existing_pair = db.session.execute(
                text(
                    "SELECT 1 FROM djmdRecommendLike WHERE "
                    "(ContentID1 = :a AND ContentID2 = :b) OR (ContentID1 = :b AND ContentID2 = :a)"
                ),
                {"a": keeper_id, "b": other_id},
            ).fetchone()
            if existing_pair:
                db.session.execute(text("DELETE FROM djmdRecommendLike WHERE ID = :id"), {"id": row_id})
                dropped += 1
            else:
                db.session.execute(
                    text(f"UPDATE djmdRecommendLike SET {col_self} = :kid WHERE ID = :id"),
                    {"kid": keeper_id, "id": row_id},
                )
                rethreaded += 1
    return rethreaded, dropped


def _rethread_associations(remove_content, keeper_path: str, db, emit) -> dict:
    """
    Before a duplicate content row is deleted: backfill any descriptive
    metadata the keeper is missing, then re-thread every other
    ContentID-referencing table (playlists, cues, mixer params, my tags,
    history, sampler, related tracks, hot cue banklists, active censors, tag
    list, cloud export playlists, recommend-likes) so nothing becomes a
    silent orphan.

    Returns a dict of counts, including "playlists_rethreaded" (kept as its
    own key for backward compatibility with existing callers) and
    "associations_rethreaded" (total across every other table).
    """
    keeper_rows = db.get_content(FolderPath=keeper_path).all()
    if not keeper_rows:
        emit(f"      ⚠  Keeper not in DB ({Path(keeper_path).name}) — associations left intact")
        return {"playlists_rethreaded": 0, "associations_rethreaded": 0, "metadata_backfilled": 0}

    keeper_content = keeper_rows[0]
    remove_id = remove_content.ID
    keeper_id = keeper_content.ID

    metadata_backfilled = _backfill_metadata(remove_content, keeper_content, emit)

    playlists_rethreaded = 0
    associations_rethreaded = 0

    for getter_name, group_field, label in _GROUP_TABLES:
        rethreaded, dropped = _rethread_group_table(db, getter_name, group_field, remove_id, keeper_id)
        if getter_name == "get_playlist_songs":
            playlists_rethreaded += rethreaded
        elif rethreaded or dropped:
            associations_rethreaded += rethreaded
            emit(f"      ↪  {label}: {rethreaded} re-threaded, {dropped} dropped (keeper already present)")

    for getter_name, label in _SIMPLE_TABLES:
        copied, dropped = _copy_or_drop_simple(db, getter_name, remove_id, keeper_id)
        if copied or dropped:
            associations_rethreaded += copied
            emit(f"      ↪  {label}: {copied} copied, {dropped} dropped (keeper already has one)")

    cue_copied, cue_dropped = _rethread_cues(db, remove_id, keeper_id)
    if cue_copied or cue_dropped:
        associations_rethreaded += cue_copied
        emit(f"      ↪  cues: {cue_copied} copied, {cue_dropped} dropped (slot conflict, keeper wins)")

    cloud_rethreaded, cloud_dropped = _rethread_cloud_export_playlists(db, remove_id, keeper_id)
    if cloud_rethreaded or cloud_dropped:
        associations_rethreaded += cloud_rethreaded
        emit(f"      ↪  cloud export playlists: {cloud_rethreaded} re-threaded, {cloud_dropped} dropped")

    like_rethreaded, like_dropped = _rethread_recommend_likes(db, remove_id, keeper_id)
    if like_rethreaded or like_dropped:
        associations_rethreaded += like_rethreaded
        emit(f"      ↪  recommend-likes: {like_rethreaded} re-threaded, {like_dropped} dropped")

    return {
        "playlists_rethreaded": playlists_rethreaded,
        "associations_rethreaded": associations_rethreaded,
        "metadata_backfilled": metadata_backfilled,
    }


def prune_files(
    file_paths: list[str],
    db,
    log=None,
    permanent: bool = False,
    keeper_map: Optional[dict[str, str]] = None,
    should_cancel=None,
    archive=None,
) -> dict:
    """
    Remove file_paths from DjmdContent and move them to a timestamped
    recovery folder inside ~/Trash/.

    keeper_map (optional): {remove_path → keeper_path}
      When provided, every other table that references the duplicate's
      ContentID (playlists, cues, mixer params, my tags, history, sampler,
      related tracks, hot cue banklists, active censors, tag list, cloud
      export playlists, recommend-likes) is re-threaded to point at the
      keeper *before* the duplicate row is deleted, and any descriptive
      metadata (BPM, key, genre, artist, album, comment, rating, etc.) the
      keeper is missing is backfilled from the duplicate first. The keeper's
      own non-empty values always win — nothing populated is ever overwritten.

    Order of operations:
      1. Create recovery folder in Trash.
      2. Backfill metadata and re-thread associations for each duplicate
         that has a keeper.
      3. Remove DB entries (with the backup already created by write_db).
      4. Move files to recovery folder.

    Returns a summary dict:
      { db_removed, files_moved, skipped, errors, trash_dir,
        playlists_rethreaded, associations_rethreaded, metadata_backfilled }
    """

    def emit(msg: str) -> None:
        if log:
            log(msg)

    def _cancel_requested() -> bool:
        if not should_cancel:
            return False
        try:
            return bool(should_cancel())
        except Exception as exc:
            # A broken cancel-check must never abort or hang the prune — default
            # to "not cancelled" — but log it so a flaky should_cancel() is visible.
            _log.warning("should_cancel() raised — treating as not cancelled: %s", exc)
            return False

    stamp     = datetime.now().strftime("%Y%m%d_%H%M%S")
    if permanent:
        trash_dir = None
        emit("⚠  Permanent delete mode — files will NOT be recoverable")
    else:
        trash_dir = Path.home() / ".Trash" / f"FableGear_Pruned_{stamp}"
        trash_dir.mkdir(parents=True, exist_ok=True)
        emit(f"Recovery folder → {trash_dir}")
    emit("")

    db_removed = 0
    files_moved = 0
    skipped    = 0
    playlists_rethreaded = 0
    associations_rethreaded = 0
    metadata_backfilled = 0
    errors: list[str] = []
    protected_paths: set[str] = set()

    # ── Step 1: Remove from database (with association re-threading) ──────
    emit("  Removing from RekordBox database…")
    for path in file_paths:
        if _cancel_requested():
            emit("    ⚠  Cancel requested — rolling back pending database changes.")
            try:
                db.rollback()
            except Exception as exc:
                # Caller's cancelled-path only prints a generic message and never
                # inspects `errors` for this early return, so a silent append here
                # would hide the failure entirely — emit it too.
                msg = f"Rollback failed after cancel request: {exc}"
                errors.append(msg)
                emit(f"    ⚠  {msg}")
            emit("  Prune cancelled before commit. No files were moved.")
            return {
                "db_removed":              0,
                "files_moved":             0,
                "skipped":                 0,
                "errors":                  errors,
                "trash_dir":               str(trash_dir) if trash_dir is not None else None,
                "playlists_rethreaded":    0,
                "associations_rethreaded": 0,
                "metadata_backfilled":     0,
                "cancelled":               True,
            }
        try:
            # SAVEPOINT-scoped: if re-threading a row succeeds but deleting it
            # (or a later row for this same path) then fails, the whole block
            # rolls back to this savepoint on the way out — not just this row,
            # but not the rest of the batch either. Without this, a partial
            # rethread could ride along uncommitted-but-staged into whatever
            # the final db.commit() below picks up from earlier, successful
            # paths in this same run.
            # Accumulated locally and only merged into the outer running totals
            # after the nested block below completes without raising — if a
            # later row for this same path fails, the savepoint rolls back the
            # DB-side changes, but Python variable increments aren't
            # transactional, so accumulating straight into the outer counters
            # would let a rolled-back row's stats survive in the summary.
            path_playlists_rethreaded = 0
            path_associations_rethreaded = 0
            path_metadata_backfilled = 0
            path_deleted = 0

            with db.session.begin_nested():
                rows = db.get_content(FolderPath=path).all()
                if rows:
                    for row in rows:
                        # Re-thread associations (playlists, cues, tags, etc.) before
                        # deleting the content row
                        keeper_path = (keeper_map or {}).get(path)
                        song_rows = db.get_playlist_songs(ContentID=row.ID).all()
                        if keeper_path:
                            result = _rethread_associations(row, keeper_path, db, emit)
                            path_playlists_rethreaded += result["playlists_rethreaded"]
                            path_associations_rethreaded += result["associations_rethreaded"]
                            path_metadata_backfilled += result["metadata_backfilled"]
                            if result["playlists_rethreaded"]:
                                emit(f"      ↪  {result['playlists_rethreaded']} playlist slot(s) re-threaded to keeper")
                        elif song_rows:
                            protected_paths.add(path)
                            skipped += 1
                            emit(
                                "      ⚠  Protected — track is in playlist(s) but no keeper mapping was provided; skipping delete"
                            )
                            continue
                        db.session.delete(row)
                        path_deleted += 1
                    if path_deleted > 0:
                        emit(f"    DB ✓  {Path(path).name}")
                    else:
                        emit(f"    DB —  {Path(path).name}  (protected; no rows deleted)")
                else:
                    emit(f"    DB —  {Path(path).name}  (not in database — file only)")

            # Reached only if the nested block above didn't raise — safe to
            # fold this path's stats into the run-wide totals now.
            playlists_rethreaded += path_playlists_rethreaded
            associations_rethreaded += path_associations_rethreaded
            metadata_backfilled += path_metadata_backfilled
            db_removed += path_deleted
        except Exception as exc:
            msg = f"DB error for {Path(path).name}: {exc}"
            errors.append(msg)
            emit(f"    DB ✗  {msg}")

    # Commit all session.delete() calls — session.close() does NOT commit.
    try:
        db.commit()
        emit(f"  Database commit OK ({db_removed} row(s) removed)")
    except Exception as exc:
        msg = f"Database commit failed — no files will be moved: {exc}"
        errors.append(msg)
        emit(f"  ✗ {msg}")
        return {
            "db_removed":              0,
            "files_moved":             0,
            "skipped":                 skipped,
            "errors":                  errors,
            "trash_dir":               str(trash_dir) if trash_dir is not None else None,
            "playlists_rethreaded":    playlists_rethreaded,
            "associations_rethreaded": associations_rethreaded,
            "metadata_backfilled":     metadata_backfilled,
            "cancelled":               False,
        }

    emit("")

    # ── Step 2: Move/delete files ──────────────────────────────────────────
    if _cancel_requested():
        emit("  ⚠  Cancel requested after database commit — finishing file operations to keep DB and filesystem in sync.")

    def _journal_prune(src_path: str, dest) -> None:
        """Journal each removal the moment it happens — an interrupted prune
        must still leave a record of every file it moved or deleted."""
        if archive is None:
            return
        try:
            rec = archive.get_content_by_path(src_path)
            if rec and rec.id is not None:
                archive.delete_content(rec.id)
            archive.log_operation(
                "prune", src_path, status="ok",
                metadata={
                    "permanent": permanent,
                    "moved_to": str(dest) if dest else None,
                    "trash_dir": str(trash_dir) if trash_dir else None,
                },
            )
        except Exception as exc:
            _log.warning("Archive update failed for prune %s: %s", src_path, exc)

    action_label = "Permanently deleting" if permanent else "Moving files to recovery folder"
    emit(f"  {action_label}…")
    for path in file_paths:
        if path in protected_paths:
            emit(f"    Skip — protected by playlist safety gate: {Path(path).name}")
            continue
        p = Path(path)
        if not p.exists():
            emit(f"    Skip — not found on disk: {p.name}")
            skipped += 1
            continue
        try:
            if permanent:
                p.unlink()
                files_moved += 1
                emit(f"    Deleted ✓  {p.name}")
                _journal_prune(path, dest=None)
            else:
                dest = trash_dir / p.name
                # Handle name collisions within the recovery folder
                if dest.exists():
                    dest = trash_dir / f"{p.stem}__{p.parent.name}{p.suffix}"
                shutil.move(str(p), str(dest))
                files_moved += 1
                emit(f"    Moved ✓  {p.name}")
                _journal_prune(path, dest=dest)
        except Exception as exc:
            msg = f"Could not {'delete' if permanent else 'move'} {p.name}: {exc}"
            errors.append(msg)
            emit(f"    {'Delete' if permanent else 'Move'} ✗  {msg}")

    emit("")
    emit("═══ PRUNE SUMMARY ═══")
    emit(f"  Database entries removed        : {db_removed}")
    if playlists_rethreaded:
        emit(f"  Playlist slots re-threaded     : {playlists_rethreaded}")
    if associations_rethreaded:
        emit(f"  Other associations re-threaded : {associations_rethreaded}")
    if metadata_backfilled:
        emit(f"  Metadata fields backfilled     : {metadata_backfilled}")
    emit(f"  Files {'permanently deleted' if permanent else 'moved to recovery'} : {files_moved}")
    if skipped:
        emit(f"  Skipped (not on disk)    : {skipped}")
    if errors:
        emit(f"  Errors                   : {len(errors)}")
        for err in errors:
            emit(f"    ⚠  {err}")
    emit(f"  Recovery folder          : {trash_dir}")
    emit("═════════════════════")

    if archive is not None and files_moved > 0:
        archive.log_operation(
            "prune_batch",
            metadata={
                "db_removed": db_removed,
                "files_moved": files_moved,
                "permanent": permanent,
                "playlists_rethreaded": playlists_rethreaded,
                "associations_rethreaded": associations_rethreaded,
                "metadata_backfilled": metadata_backfilled,
            },
        )

    return {
        "db_removed":              db_removed,
        "files_moved":             files_moved,
        "skipped":                 skipped,
        "errors":                  errors,
        "trash_dir":               str(trash_dir) if trash_dir is not None else None,
        "playlists_rethreaded":    playlists_rethreaded,
        "associations_rethreaded": associations_rethreaded,
        "metadata_backfilled":     metadata_backfilled,
        "cancelled":               False,
    }
