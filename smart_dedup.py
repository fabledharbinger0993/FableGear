"""
smart_dedup — a non-breaking duplicate resolver for a Rekordbox ``master.db``.

Every mainstream DJ tool (Rekordbox, Serato, Traktor) resolves a duplicate by
deleting the ``DjmdContent`` record — which silently orphans every
``DjmdSongPlaylist`` row that referenced it. The track vanishes from every
playlist it was in, with no warning. That is how curated crates get quietly
gutted (and, at scale, how this very library was damaged).

This resolver never does that. For each duplicate group it picks a survivor,
**re-wires every playlist membership of the doomed records onto the survivor,
verifies nothing is left pointing at them, and only then deletes the record.**
A track that was in five crates is still in five crates afterward — now via the
surviving copy.

Two library models, surfaced as `DedupMode`:

* ``DATABASE`` — Rekordbox's actual model: records reference files that may or
  may not exist. A "duplicate" is two records for the same track regardless of
  file state. Resolution keeps the record with the best available path and the
  richest playlist membership, and never touches an audio file. This is the
  safe default.
* ``PHYSICAL`` — the filesystem is the source of truth: a duplicate means two
  files exist on disk. Resolution still re-wires memberships first, but a
  survivor is only chosen among records whose files actually exist. (Deleting
  the redundant *file* is left to the caller/UI with explicit confirmation —
  this module only ever removes DB records.)

Design: the decision logic (grouping, survivor choice, re-wire planning) is
pure and operates on plain :class:`TrackRec` values, so it is fully unit-tested
without a database. :class:`SmartDedup` is the thin adapter that reads those
values out of a live pyrekordbox database and applies an approved plan back.
No audio file is ever moved, copied, or deleted by this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Canonical library location — a copy living here is preferred as the survivor.
CANONICAL_HINT = "Music for Dj's"


class DedupMode(Enum):
    PHYSICAL = "physical"   # two files on disk; survivor must exist on disk
    DATABASE = "database"   # two records; survivor = best path + richest crates


@dataclass
class TrackRec:
    """The minimal projection of a DjmdContent row the resolver reasons about."""
    id: int
    title: Optional[str]
    artist: Optional[str]
    folder_path: Optional[str]
    playlist_ids: Tuple[int, ...] = ()


@dataclass
class GroupPlan:
    """A resolved (or flagged) duplicate group."""
    key: Tuple[str, str]
    records: List[TrackRec]
    survivor: Optional[TrackRec] = None
    to_remove: List[TrackRec] = field(default_factory=list)
    resolution: str = "flagged"          # 'auto' | 'flagged'
    reason: str = ""
    # {removed_id: {"move": [playlist_id...], "drop": [playlist_id...]}}
    rewires: Dict[int, Dict[str, List[int]]] = field(default_factory=dict)

    @property
    def links_rewired(self) -> int:
        return sum(len(v["move"]) for v in self.rewires.values())

    @property
    def links_dropped(self) -> int:
        return sum(len(v["drop"]) for v in self.rewires.values())


@dataclass
class DedupPlan:
    mode: DedupMode
    groups: List[GroupPlan] = field(default_factory=list)

    @property
    def auto(self) -> List[GroupPlan]:
        return [g for g in self.groups if g.resolution == "auto"]

    @property
    def flagged(self) -> List[GroupPlan]:
        return [g for g in self.groups if g.resolution == "flagged"]

    def summary(self) -> dict:
        auto = self.auto
        return {
            "mode": self.mode.value,
            "duplicate_groups": len(self.groups),
            "auto_resolvable": len(auto),
            "flagged_for_review": len(self.flagged),
            "records_to_remove": sum(len(g.to_remove) for g in auto),
            "memberships_rewired": sum(g.links_rewired for g in auto),
            "duplicate_memberships_dropped": sum(g.links_dropped for g in auto),
        }


# ── Pure decision logic ─────────────────────────────────────────────────────

def normalize_key(title: Optional[str], artist: Optional[str]) -> Tuple[str, str]:
    """The identity used to group duplicates: case/space-folded title+artist."""
    return ((title or "").strip().lower(), (artist or "").strip().lower())


def find_duplicate_groups(tracks: List[TrackRec]) -> List[List[TrackRec]]:
    """Group tracks by normalized title+artist; return only groups of 2+.
    Records with an empty title are never grouped (nothing to match on)."""
    buckets: Dict[Tuple[str, str], List[TrackRec]] = {}
    for t in tracks:
        key = normalize_key(t.title, t.artist)
        if not key[0]:
            continue
        buckets.setdefault(key, []).append(t)
    return [g for g in buckets.values() if len(g) > 1]


def choose_survivor(
    records: List[TrackRec],
    mode: DedupMode,
    path_exists: Callable[[str], bool],
) -> Tuple[Optional[TrackRec], str]:
    """Pick which record to keep. Returns ``(survivor, reason)`` or
    ``(None, reason)`` when the group can't be auto-resolved and must be flagged.

    Order of preference:
      1. the copy whose file actually exists on disk (the only rule in PHYSICAL
         mode — with no existing file there is nothing to keep),
      2. the copy in the canonical library location,
      3. the copy with the most playlist memberships (most curated),
      4. otherwise flag for manual review (never guess between equals).
    """
    def exists(r: TrackRec) -> bool:
        return bool(r.folder_path and path_exists(r.folder_path))

    present = [r for r in records if exists(r)]

    if mode is DedupMode.PHYSICAL:
        # Physical model: only real files count. Need >=2 present to be a true
        # physical duplicate; keep exactly one and remove the rest's records.
        if len(present) < 2:
            return None, "not a physical duplicate (fewer than two files on disk)"
        pool = present
    else:
        # Database model: exactly one present file is the clean, unambiguous win.
        if len(present) == 1:
            return present[0], "only copy whose file exists"
        pool = present if present else list(records)

    # Rule 2: canonical location.
    canonical = [r for r in pool if r.folder_path and CANONICAL_HINT in r.folder_path]
    if len(canonical) == 1:
        return canonical[0], "in canonical library location"
    pool2 = canonical or pool

    # Rule 3: most playlist memberships.
    pool2 = sorted(pool2, key=lambda r: len(r.playlist_ids), reverse=True)
    if len(pool2) == 1 or len(pool2[0].playlist_ids) > len(pool2[1].playlist_ids):
        return pool2[0], "most playlist memberships"

    # Rule 4: genuine tie — don't auto-resolve.
    return None, "ambiguous (equal candidates) — needs manual review"


def plan_group(
    records: List[TrackRec],
    mode: DedupMode,
    path_exists: Callable[[str], bool],
) -> GroupPlan:
    """Build the re-wire plan for one duplicate group (pure; no side effects)."""
    key = normalize_key(records[0].title, records[0].artist)
    survivor, reason = choose_survivor(records, mode, path_exists)
    gp = GroupPlan(key=key, records=records, reason=reason)
    if survivor is None:
        gp.resolution = "flagged"
        return gp

    gp.survivor = survivor
    gp.to_remove = [r for r in records if r.id != survivor.id]
    gp.resolution = "auto"

    # Walk the doomed records, moving each membership onto the survivor unless
    # the survivor is already in that playlist (then the dupe entry is dropped).
    keep_playlists = set(survivor.playlist_ids)
    for r in gp.to_remove:
        move, drop = [], []
        for pid in r.playlist_ids:
            if pid in keep_playlists:
                drop.append(pid)
            else:
                move.append(pid)
                keep_playlists.add(pid)
        gp.rewires[r.id] = {"move": move, "drop": drop}
    return gp


def build_plan(
    tracks: List[TrackRec],
    mode: DedupMode = DedupMode.DATABASE,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> DedupPlan:
    """Full pure plan: group, choose survivors, plan re-wires. No DB access."""
    plan = DedupPlan(mode=mode)
    for group in find_duplicate_groups(tracks):
        plan.groups.append(plan_group(group, mode, path_exists))
    return plan


# ── pyrekordbox adapter ─────────────────────────────────────────────────────

class SmartDedup:
    """Reads TrackRecs from a live pyrekordbox database and applies an approved
    :class:`DedupPlan`. Construct with an open ``Rekordbox6Database`` (not a
    path) so it can run against a copy in tests without touching production."""

    def __init__(self, db, mode: DedupMode = DedupMode.DATABASE):
        self.db = db
        self.mode = mode
        from pyrekordbox.db6 import tables  # noqa: F401 — imported lazily
        self._tables = tables

    def _read_tracks(self) -> List[TrackRec]:
        tables = self._tables
        # playlist memberships per content id
        members: Dict[int, List[int]] = {}
        for sp in self.db.query(tables.DjmdSongPlaylist).with_entities(
                tables.DjmdSongPlaylist.ContentID, tables.DjmdSongPlaylist.PlaylistID):
            members.setdefault(sp.ContentID, []).append(sp.PlaylistID)
        recs: List[TrackRec] = []
        for c in self.db.query(tables.DjmdContent).with_entities(
                tables.DjmdContent.ID, tables.DjmdContent.Title,
                tables.DjmdContent.ArtistName, tables.DjmdContent.FolderPath):
            recs.append(TrackRec(
                id=c.ID, title=c.Title, artist=c.ArtistName,
                folder_path=c.FolderPath,
                playlist_ids=tuple(members.get(c.ID, ())),
            ))
        return recs

    def plan(self, path_exists: Callable[[str], bool] = os.path.exists) -> DedupPlan:
        """Scan + plan. Read-only."""
        return build_plan(self._read_tracks(), self.mode, path_exists)

    def execute(self, plan: DedupPlan) -> dict:
        """Apply the auto-resolvable groups: re-wire memberships onto each
        survivor, verify the doomed records are fully detached, then delete
        them. Commits once. The caller owns the Rekordbox-closed gate + backup
        (see rekordbox_safe_write). Returns an audit dict."""
        tables = self._tables
        SP = tables.DjmdSongPlaylist
        removed = rewired = dropped = 0

        for g in plan.auto:
            keep_id = g.survivor.id
            keep_pls = {pid for (cid, pid) in
                        self.db.query(SP.ContentID, SP.PlaylistID)
                        .filter(SP.ContentID == keep_id)}
            for r in g.to_remove:
                for sp in self.db.query(SP).filter(SP.ContentID == r.id).all():
                    if sp.PlaylistID in keep_pls:
                        self.db.session.delete(sp)   # survivor already there
                        dropped += 1
                    else:
                        sp.ContentID = keep_id        # move membership onto survivor
                        keep_pls.add(sp.PlaylistID)
                        rewired += 1
                # Verify nothing still references the doomed record BEFORE delete.
                remaining = self.db.query(SP).filter(SP.ContentID == r.id).count()
                if remaining:
                    raise RuntimeError(
                        f"Re-wire incomplete for content {r.id}: {remaining} left; "
                        f"aborting before delete (no record removed).")
                content = self.db.get_content(ID=r.id)
                if content is not None:
                    self.db.session.delete(content)
                removed += 1

        self.db.commit()
        return {"records_removed": removed, "memberships_rewired": rewired,
                "duplicate_memberships_dropped": dropped,
                "groups_resolved": len(plan.auto)}
