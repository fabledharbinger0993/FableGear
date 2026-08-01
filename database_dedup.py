"""
fablegear / database_dedup.py — Database Library Deduplication

FableGear's Chop Shop already has a duplicate finder (chop_shop/duplicate_detector.py
+ chop_shop/pruner.py). That one treats the filesystem as ground truth: a
"duplicate" is two audio files that sound the same, found by fingerprinting
the files themselves, and resolving one means moving or deleting a file.

This module treats the Rekordbox database as ground truth instead. A
"duplicate" here is two DjmdContent records that share the same artist,
title, and duration but disagree on FolderPath — which happens when a track
gets re-imported after a drive migration, a folder reorganization, or a
double drag-and-drop. Some of those records may point at files that no
longer exist anywhere on disk; that's fine, because this module never reads,
fingerprints, moves, or deletes a single audio file. It only ever decides
which DjmdContent row survives, re-wires every DjmdSongPlaylist membership
that pointed at the rows being removed onto the survivor, and then removes
the now-redundant rows. The playlists a track belonged to are preserved
across the merge — nothing silently disappears from a set list.

Two entry points:
  scan_conflicts(db)                 — read-only. Groups records, flags
                                        conflicts, never writes.
  build_plan(db, max_groups=50)      — read-only. scan_conflicts() plus the
                                        keeper choice for each group.
  execute_plan(db, plan, ...)        — writes. Re-threads playlist rows and
                                        deletes the losing DjmdContent rows.
                                        Caller must run this inside
                                        db_connection.write_db() so the
                                        Rekordbox-closed check, the
                                        pre-write backup, and the
                                        rollback-on-exception guard all
                                        apply automatically.

Used by routes_player.py:
  GET  /api/library/integrity/canonical-paths/plan     (read-only preview)
  POST /api/library/integrity/canonical-paths/execute   (writes, via write_db())
"""

import logging
import os

log = logging.getLogger(__name__)


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


# ── Scan ─────────────────────────────────────────────────────────────────────

def scan_conflicts(db) -> tuple[int, list[dict]]:
    """
    Group DjmdContent records with non-empty Title and FolderPath by
    (artist, title, duration) and return the groups that resolve to more than
    one distinct FolderPath.
    Read-only — no writes are ever made here.

    Returns (tracks_scanned, conflict_groups). Each group:
      {
        "signature": {"artist": str, "title": str, "duration": int},
        "path_count": int,
        "entries": [
          {"content_id": str, "path": str, "exists_on_disk": bool,
           "playlist_ref_count": int},
          ...
        ],
      }
    """
    tracks = db.get_content().all()
    grouped: dict[tuple, list] = {}
    for track in tracks:
        title = _norm(getattr(track, "Title", ""))
        try:
            artist_name = _norm(track.Artist.Name if track.Artist else "")
        except Exception as exc:
            # Falling back to "" risks grouping this record with unrelated
            # tracks that share title+duration but a different real artist —
            # log it so a bad merge/delete downstream can be traced back here.
            log.warning(
                "database_dedup: could not resolve Artist for ContentID=%s — %s",
                getattr(track, "ID", "?"), exc,
            )
            artist_name = ""
        duration = int(getattr(track, "Length", 0) or 0)
        path = str(getattr(track, "FolderPath", "") or "").strip()

        if not title or not path:
            continue

        grouped.setdefault((artist_name, title, duration), []).append(track)

    conflicts = []
    for signature, rows in grouped.items():
        distinct_paths = {
            str(getattr(row, "FolderPath", "") or "").strip()
            for row in rows
            if str(getattr(row, "FolderPath", "") or "").strip()
        }
        if len(distinct_paths) <= 1:
            continue

        items = []
        for row in rows:
            row_path = str(getattr(row, "FolderPath", "") or "").strip()
            if not row_path:
                continue
            playlist_refs = db.get_playlist_songs(ContentID=row.ID).all()
            items.append({
                "content_id": str(row.ID),
                "path": row_path,
                "exists_on_disk": os.path.isfile(row_path),
                "playlist_ref_count": len(playlist_refs),
            })

        artist_name, title, duration = signature
        conflicts.append({
            "signature": {"artist": artist_name, "title": title, "duration": duration},
            "path_count": len(distinct_paths),
            "entries": items,
        })

    conflicts.sort(key=lambda g: (g["path_count"], len(g["entries"])), reverse=True)
    return len(tracks), conflicts


# ── Keeper selection ───────────────────────────────────────────────────────────

def choose_keeper(entries: list[dict]) -> tuple[dict | None, bool]:
    """
    Pick which entry in a conflict group should survive.

    Preference order: a record whose file still resolves on disk beats one
    that doesn't; among ties, the record with more playlist references (the
    more "curated" copy) wins; a shortest-path comparison breaks any
    remaining tie so the choice is deterministic.

    Returns (keeper, ambiguous). ambiguous=True means two or more entries
    tied on both exists_on_disk and playlist_ref_count — automatically
    picking between them would be a coin flip, so callers should leave the
    group for a human to resolve instead of guessing.
    """
    if not entries:
        return None, False

    def _rank(e):
        return (1 if e.get("exists_on_disk") else 0, int(e.get("playlist_ref_count", 0) or 0))

    best_rank = max(_rank(e) for e in entries)
    tied = [e for e in entries if _rank(e) == best_rank]
    ambiguous = len(tied) > 1

    keeper = min(
        tied,
        key=lambda e: (len(str(e.get("path") or "")), str(e.get("path") or "").lower()),
    )
    return keeper, ambiguous


def build_plan(db, max_groups: int = 50) -> dict:
    """Read-only consolidation plan built on top of scan_conflicts()."""
    tracks_scanned, conflicts = scan_conflicts(db)

    plans = []
    for group in conflicts[:max_groups]:
        entries = group.get("entries") or []
        if len(entries) < 2:
            continue

        keeper, ambiguous = choose_keeper(entries)
        remove_candidates = [e for e in entries if e is not keeper]
        estimated_rethread = sum(
            int(e.get("playlist_ref_count", 0) or 0) for e in remove_candidates
        )

        plans.append({
            "signature": group.get("signature") or {},
            "keeper": keeper,
            "remove_candidates": remove_candidates,
            "ambiguous": ambiguous,
            "estimated_playlist_slots_to_rethread": estimated_rethread,
        })

    return {
        "total_tracks_scanned": tracks_scanned,
        "total_conflict_groups": len(conflicts),
        "planned_groups": len(plans),
        "plans": plans,
    }


# ── Execution ──────────────────────────────────────────────────────────────

def _rewire_group(db, keeper_id: int, remove_ids: list[int], emit) -> int:
    """
    Re-point every DjmdSongPlaylist row referencing remove_ids onto
    keeper_id (dropping it instead when keeper_id is already in that
    playlist), verify no membership rows remain, then delete the
    now-redundant DjmdContent rows. Never touches a file on disk.

    Returns the number of playlist slots re-threaded (drops don't count).
    """
    rethreaded = 0
    for remove_id in remove_ids:
        song_rows = db.get_playlist_songs(ContentID=remove_id).all()
        for song_row in song_rows:
            already_there = db.get_playlist_songs(
                ContentID=keeper_id, PlaylistID=song_row.PlaylistID
            ).all()
            if already_there:
                db.session.delete(song_row)
            else:
                song_row.ContentID = keeper_id
                rethreaded += 1

        remaining = db.get_playlist_songs(ContentID=remove_id).all()
        if remaining:
            raise RuntimeError(
                f"Re-wire incomplete for ContentID={remove_id}: "
                f"{len(remaining)} playlist membership(s) remain"
            )

        row = db.get_content(ID=remove_id)
        if row is not None:
            db.session.delete(row)
            emit(f"  removed ContentID={remove_id} -> kept ContentID={keeper_id}")

    return rethreaded


def execute_plan(
    db,
    plan: dict,
    *,
    signatures: list[dict] | None = None,
    log_fn=None,
) -> dict:
    """
    Execute a consolidation plan built by build_plan().

    signatures, if given, restricts execution to only the plan entries whose
    signature (artist + title + duration) matches one in the list — lets a
    caller consolidate a reviewed subset instead of everything found in one
    pass. Ambiguous groups (see choose_keeper) are always skipped regardless
    of the signatures filter; they need a human pick, not a guess.

    The caller is responsible for committing (typically by running this
    inside db_connection.write_db(), which also performs the pre-write
    backup, the Rekordbox-closed check, and rolls back automatically if this
    function raises).
    """
    def emit(msg):
        if log_fn:
            log_fn(msg)

    def _sig_key(sig):
        return (
            str(sig.get("artist", "")).strip().lower(),
            str(sig.get("title", "")).strip().lower(),
            int(sig.get("duration", 0) or 0),
        )

    wanted = {_sig_key(s) for s in signatures} if signatures is not None else None

    groups_resolved = 0
    groups_skipped_ambiguous = 0
    groups_skipped_filtered = 0
    content_removed = 0
    playlists_rethreaded = 0

    for entry in plan.get("plans", []):
        sig = entry.get("signature") or {}

        if wanted is not None and _sig_key(sig) not in wanted:
            groups_skipped_filtered += 1
            continue

        if entry.get("ambiguous"):
            groups_skipped_ambiguous += 1
            emit(
                f"  SKIP (ambiguous — needs manual pick): "
                f"{sig.get('artist')} - {sig.get('title')}"
            )
            continue

        keeper = entry.get("keeper")
        remove = entry.get("remove_candidates") or []
        if not keeper or not remove:
            continue

        keeper_id = int(keeper["content_id"])
        remove_ids = [int(e["content_id"]) for e in remove]

        emit(f"Resolving: {sig.get('artist')} - {sig.get('title')} (keep ContentID={keeper_id})")
        rethreaded = _rewire_group(db, keeper_id, remove_ids, emit)

        playlists_rethreaded += rethreaded
        content_removed += len(remove_ids)
        groups_resolved += 1

    return {
        "groups_resolved": groups_resolved,
        "groups_skipped_ambiguous": groups_skipped_ambiguous,
        "groups_skipped_filtered": groups_skipped_filtered,
        "content_removed": content_removed,
        "playlists_rethreaded": playlists_rethreaded,
    }
