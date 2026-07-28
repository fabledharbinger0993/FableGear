# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
"""
playlist_recovery.py — reconstruct a DJ's playlists ("crates") from the
exported media they left behind, after a destructive edit unthreaded them from
the live library.

Every exported stick freezes its playlists at export time, independent of what
later happened to the master database. This engine scans for those exports,
reads the crates out of each, unions them across all sources (richest version
of each crate wins; extra tracks from other sticks are merged in), and resolves
each track against FableGear's archive so the crates can be rebuilt.

Read sources, in preference order per stick:
  1. exportLibrary.db  (OneLibrary / SQLCipher) — carries full playlist
     membership. Read directly with the public OneLibrary key.
  2. export.pdb        (DeviceSQL) — fallback for old sticks with no
     exportLibrary.db; membership via chop_shop.devicesql_reader.read_playlists.

This module is READ-ONLY over the export media and the archive. The rebuild
(writing recovered crates into the archive) is done by the caller (cli.py),
which owns the dry-run / checkpoint / undo / audit-log safety.

Public interface:
    find_export_sources(roots) -> List[ExportSource]
    read_crates(source) -> List[RecoveredCrate]
    union_crates(crates, strategy="richest") -> List[RecoveredCrate]
    resolve_against_archive(crates, database) -> ResolutionStats
    recover(roots, database=None, strategy="richest") -> RecoveryReport
"""
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Trailing Rekordbox duplicate-name suffix, e.g. "best of house (2)" / "  (3)".
_NUM_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")

log = logging.getLogger(__name__)


@dataclass
class CrateTrack:
    title: Optional[str] = None
    artist: Optional[str] = None
    filename: Optional[str] = None       # basename of the export path
    source_path: Optional[str] = None    # path as recorded in the export
    order: int = 0
    content_id: Optional[int] = None     # resolved FableGear archive id (or None)

    @property
    def key(self) -> str:
        """Identity used for dedup/union/resolve — filename if present, else
        title+artist. Lowercased, whitespace-normalised."""
        if self.filename:
            return "f:" + self.filename.strip().lower()
        return "t:" + " ".join(((self.title or "") + " " + (self.artist or "")).lower().split())


@dataclass
class RecoveredCrate:
    name: str
    tracks: List[CrateTrack] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    @property
    def norm_name(self) -> str:
        return " ".join(self.name.strip().lower().split())


@dataclass
class ExportSource:
    path: str
    kind: str          # "onelibrary" | "pdb"
    mtime: float = 0.0


@dataclass
class ResolutionStats:
    total_tracks: int = 0
    resolved: int = 0
    unresolved: int = 0


@dataclass
class RecoveryReport:
    sources: List[ExportSource] = field(default_factory=list)
    crates: List[RecoveredCrate] = field(default_factory=list)
    resolution: ResolutionStats = field(default_factory=ResolutionStats)
    notes: List[str] = field(default_factory=list)


_EXPORT_NAMES = ("exportLibrary.db", "export.pdb", "master.db")


def find_export_sources(roots) -> List[ExportSource]:
    """Find every exportLibrary.db / export.pdb under the given roots. When a
    PIONEER/rekordbox/ dir has both, prefer the OneLibrary DB (richer) and skip
    the sibling pdb. A root may also be a direct path to an export file."""
    found: Dict[str, ExportSource] = {}
    pdb_dirs_with_onelib = set()
    def _kind(name: str) -> str:
        return {"exportLibrary.db": "onelibrary", "export.pdb": "pdb",
                "master.db": "masterdb"}[name]

    def _is_real_masterdb(p) -> bool:
        # skip the blank ~4KB template shipped inside rekordbox.app
        return "rekordbox.app" not in str(p) and _mtime(p) and os.path.getsize(p) > 5_000_000

    for root in roots:
        root = Path(root)
        # direct file path
        if root.is_file() and root.name in _EXPORT_NAMES:
            if root.name == "master.db" and not _is_real_masterdb(root):
                continue
            found[str(root)] = ExportSource(path=str(root), kind=_kind(root.name), mtime=_mtime(root))
            continue
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(root):
            if "exportLibrary.db" in files:
                p = os.path.join(dirpath, "exportLibrary.db")
                found[p] = ExportSource(path=p, kind="onelibrary", mtime=_mtime(p))
                pdb_dirs_with_onelib.add(dirpath)
        # second pass: pdb only where no sibling onelibrary
        for dirpath, _dirs, files in os.walk(root):
            if "export.pdb" in files and dirpath not in pdb_dirs_with_onelib:
                p = os.path.join(dirpath, "export.pdb")
                found[p] = ExportSource(path=p, kind="pdb", mtime=_mtime(p))
    return sorted(found.values(), key=lambda s: s.mtime)


def _mtime(p) -> float:
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


# ── Readers ────────────────────────────────────────────────────────────────

def _read_onelibrary(path: str) -> List[RecoveredCrate]:
    """Read crates from an exportLibrary.db (OneLibrary/SQLCipher)."""
    try:
        import sqlcipher3
        from fablegear_database.onelibrary_writer import _ONELIBRARY_KEY, _CIPHER_COMPATIBILITY
    except Exception as exc:  # noqa: BLE001
        log.warning("OneLibrary read unavailable (%s): %s", type(exc).__name__, exc)
        return []
    try:
        conn = sqlcipher3.connect(path)
        cur = conn.cursor()
        cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
        cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
        # playlist rows (skip folders: attribute==1 marks a folder)
        pls = cur.execute(
            "SELECT playlist_id, name, attribute FROM playlist"
        ).fetchall()
        rows = cur.execute(
            "SELECT pc.playlist_id, pc.sequenceNo, c.title, a.name, c.path, c.fileName "
            "FROM playlist_content pc "
            "JOIN content c ON c.content_id = pc.content_id "
            "LEFT JOIN artist a ON a.artist_id = c.artist_id_artist "
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read OneLibrary %s: %s", path, exc)
        return []

    name_by_id = {pid: name for pid, name, attr in pls if attr != 1}
    by_pl: Dict[int, List[CrateTrack]] = {}
    for pid, seq, title, artist, cpath, fname in rows:
        if pid not in name_by_id:
            continue
        fn = fname or (os.path.basename(cpath) if cpath else None)
        by_pl.setdefault(pid, []).append(CrateTrack(
            title=title, artist=artist, filename=fn, source_path=cpath, order=seq or 0,
        ))
    crates = []
    for pid, name in name_by_id.items():
        tracks = sorted(by_pl.get(pid, []), key=lambda t: t.order)
        if tracks:
            crates.append(RecoveredCrate(name=name or f"playlist_{pid}", tracks=tracks,
                                         sources=[path]))
    return crates


def _read_pdb(path: str) -> List[RecoveredCrate]:
    """Read crates from an export.pdb via the read-only devicesql reader."""
    try:
        import sys
        cs = str(Path(__file__).resolve().parent / "chop_shop")
        if cs not in sys.path:
            sys.path.insert(0, cs)
        import devicesql_reader as D
    except Exception as exc:  # noqa: BLE001
        log.warning("pdb reader unavailable: %s", exc)
        return []
    rep = D.read_playlists(path)
    crates = []
    for pl in rep.playlists:
        if not pl.tracks:
            continue
        tracks = [CrateTrack(
            title=t.title, artist=None,
            filename=(os.path.basename(t.path) if t.path else None),
            source_path=t.path, order=t.entry_index,
        ) for t in pl.tracks]
        crates.append(RecoveredCrate(name=pl.name or f"playlist_{pl.id}", tracks=tracks,
                                     sources=[path]))
    return crates


def _read_master_db(path: str) -> List[RecoveredCrate]:
    """Read crates from a full rekordbox master.db (SQLCipher, via pyrekordbox).
    This is the richest source — the whole library's playlists with membership,
    not just what one stick carried. Folders (no song links) are skipped."""
    try:
        from pyrekordbox import Rekordbox6Database
        from pyrekordbox.db6 import tables
    except Exception as exc:  # noqa: BLE001
        log.warning("pyrekordbox unavailable for master.db read: %s", exc)
        return []
    try:
        db = Rekordbox6Database(path=path)
        cinfo: Dict[int, tuple] = {}
        for c in db.query(tables.DjmdContent).with_entities(
                tables.DjmdContent.ID, tables.DjmdContent.Title, tables.DjmdContent.FolderPath):
            fn = os.path.basename(c.FolderPath) if c.FolderPath else None
            cinfo[c.ID] = (c.Title, fn)
        names = {pl.ID: (pl.Name or f"playlist_{pl.ID}") for pl in db.query(tables.DjmdPlaylist)}
        mem: Dict[int, list] = {}
        for sp in db.query(tables.DjmdSongPlaylist).with_entities(
                tables.DjmdSongPlaylist.PlaylistID, tables.DjmdSongPlaylist.ContentID,
                tables.DjmdSongPlaylist.TrackNo):
            mem.setdefault(sp.PlaylistID, []).append((sp.TrackNo or 0, sp.ContentID))
        db.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read master.db %s: %s", path, exc)
        return []

    crates = []
    for pid, links in mem.items():
        if not links:
            continue
        tracks = []
        for order, cid in sorted(links):
            title, fn = cinfo.get(cid, (None, None))
            tracks.append(CrateTrack(title=title, filename=fn, order=order))
        crates.append(RecoveredCrate(name=names.get(pid, f"playlist_{pid}"),
                                     tracks=tracks, sources=[path]))
    return crates


def read_crates(source: ExportSource) -> List[RecoveredCrate]:
    if source.kind == "onelibrary":
        return _read_onelibrary(source.path)
    if source.kind == "masterdb":
        return _read_master_db(source.path)
    return _read_pdb(source.path)


# ── Union ──────────────────────────────────────────────────────────────────

def union_crates(crates: List[RecoveredCrate], strategy: str = "richest",
                 merge_numbered: bool = False) -> List[RecoveredCrate]:
    """Combine crates sharing a normalised name. 'richest': base on the version
    with the most tracks, then union in any track (by CrateTrack.key) that other
    versions have and the base lacks, appended in their original order.

    merge_numbered: also collapse Rekordbox '(N)' duplicate-name suffixes, so
    'best of house (3)' unions into 'best of house'. The kept display name is
    the suffix-free one."""
    def group_key(c: RecoveredCrate) -> str:
        n = c.norm_name
        return _NUM_SUFFIX.sub("", n).strip() if merge_numbered else n

    by_name: Dict[str, List[RecoveredCrate]] = {}
    for c in crates:
        by_name.setdefault(group_key(c), []).append(c)

    out: List[RecoveredCrate] = []
    for _norm, versions in by_name.items():
        versions.sort(key=lambda c: len(c.tracks), reverse=True)
        base = versions[0]
        base_name = _NUM_SUFFIX.sub("", base.name).strip() if merge_numbered else base.name
        merged = RecoveredCrate(name=base_name or base.name, tracks=list(base.tracks),
                                sources=list(base.sources))
        seen = {t.key for t in merged.tracks}
        for other in versions[1:]:
            merged.sources.extend(s for s in other.sources if s not in merged.sources)
            if strategy == "richest":
                for t in other.tracks:
                    if t.key not in seen:
                        seen.add(t.key)
                        merged.tracks.append(t)
        # renumber order sequentially
        for i, t in enumerate(merged.tracks, 1):
            t.order = i
        out.append(merged)
    return sorted(out, key=lambda c: len(c.tracks), reverse=True)


# ── Resolve against the FableGear archive ──────────────────────────────────

def resolve_against_archive(crates: List[RecoveredCrate], database) -> ResolutionStats:
    """Match each crate track to an archive content_id by filename, then by
    title+artist. Sets CrateTrack.content_id in place. Returns counts."""
    by_filename: Dict[str, int] = {}
    by_titleartist: Dict[str, int] = {}
    for rec in database.get_content_with_relations(None):
        if rec.id is None:
            continue
        if rec.file_name:
            by_filename.setdefault(rec.file_name.strip().lower(), rec.id)
        ta = " ".join(((rec.title or "") + " " + (rec.artist or "")).lower().split())
        if ta.strip():
            by_titleartist.setdefault(ta, rec.id)

    stats = ResolutionStats()
    for c in crates:
        for t in c.tracks:
            stats.total_tracks += 1
            cid = None
            if t.filename:
                cid = by_filename.get(t.filename.strip().lower())
            if cid is None:
                ta = " ".join(((t.title or "") + " " + (t.artist or "")).lower().split())
                if ta.strip():
                    cid = by_titleartist.get(ta)
            t.content_id = cid
            if cid is not None:
                stats.resolved += 1
            else:
                stats.unresolved += 1
    return stats


@dataclass
class PushReport:
    target: str = ""
    total_crates: int = 0
    skipped_existing: int = 0        # name already in the live library
    crates_planned: int = 0          # would be / were created
    crates_no_match: int = 0         # no track resolvable in the live collection
    links_planned: int = 0           # track links to add
    unresolved_placements: int = 0   # tracks not in the live collection (need import)
    created_folder_id: Optional[str] = None
    created_playlist_ids: List = field(default_factory=list)
    written: bool = False
    detail: str = ""
    sample: List = field(default_factory=list)  # (name, link_count) preview


def push_to_rekordbox(crates: List[RecoveredCrate], target_db_path: Optional[str] = None,
                      dry_run: bool = True, folder_name: str = "Recovered",
                      skip_existing: bool = True):
    """Write recovered crates into a Rekordbox master.db via pyrekordbox.

    Resolves each track against the live collection by filename; creates each
    crate (nested under one folder) and links only tracks that already exist,
    deduped. skip_existing leaves any playlist whose name is already present
    untouched. dry_run computes the full plan and writes nothing.

    Returns a PushReport. The caller owns the Rekordbox-closed gate + backup.
    """
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.db6 import tables

    db = Rekordbox6Database(path=target_db_path) if target_db_path else Rekordbox6Database()
    rep = PushReport(target=target_db_path or "(live master.db)", total_crates=len(crates))
    try:
        by_fn: Dict[str, object] = {}
        for c in db.query(tables.DjmdContent).with_entities(
                tables.DjmdContent.ID, tables.DjmdContent.FolderPath):
            if c.FolderPath:
                by_fn.setdefault(os.path.basename(c.FolderPath).lower(), c.ID)
        existing = {(p.Name or "") for p in db.query(tables.DjmdPlaylist)}

        plan = []  # (name, [content_id, ...])
        for cr in crates:
            if skip_existing and cr.name in existing:
                rep.skipped_existing += 1
                continue
            ids, seen = [], set()
            for t in cr.tracks:
                cid = by_fn.get(t.filename.lower()) if t.filename else None
                if cid is not None:
                    if cid not in seen:
                        seen.add(cid)
                        ids.append(cid)
                else:
                    rep.unresolved_placements += 1
            if not ids:
                rep.crates_no_match += 1
                continue
            plan.append((cr.name, ids))
            rep.crates_planned += 1
            rep.links_planned += len(ids)

        rep.sample = [(n, len(ids)) for n, ids in
                      sorted(plan, key=lambda x: len(x[1]), reverse=True)[:15]]

        if dry_run:
            rep.detail = (f"DRY RUN — would create {rep.crates_planned} crate(s), "
                          f"{rep.links_planned} link(s); skip {rep.skipped_existing} existing; "
                          f"{rep.unresolved_placements} placement(s) need import first")
            return rep

        # ── WRITE ── nest under one folder; commit per crate (bounded, survives
        # via folder_id since ORM objects detach after commit).
        folder = db.create_playlist_folder(folder_name)
        folder_id = folder.ID
        rep.created_folder_id = folder_id
        created = [folder_id]
        db.commit()
        for name, ids in plan:
            pl = db.create_playlist(name, parent=folder_id)
            pid = pl.ID
            created.append(pid)
            for i, cid in enumerate(ids, 1):
                db.add_to_playlist(pl, cid, track_no=i)
            db.commit()
        rep.created_playlist_ids = created
        rep.written = True
        rep.detail = (f"WROTE {rep.crates_planned} crate(s), {rep.links_planned} link(s) "
                      f"under folder {folder_name!r}")
        return rep
    finally:
        db.close()


def recover(roots, database=None, strategy: str = "richest",
            merge_numbered: bool = False) -> RecoveryReport:
    """Full read-only recovery: scan → read → union → (optionally) resolve."""
    report = RecoveryReport()
    report.sources = find_export_sources(roots)
    all_crates: List[RecoveredCrate] = []
    for src in report.sources:
        try:
            all_crates.extend(read_crates(src))
        except Exception as exc:  # noqa: BLE001 — one bad source must not abort
            report.notes.append(f"{src.path}: {exc}")
    report.crates = union_crates(all_crates, strategy=strategy, merge_numbered=merge_numbered)
    if database is not None:
        report.resolution = resolve_against_archive(report.crates, database)
    return report
