# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-10-20
"""
fablegear / device_export.py

A CDJ-ready USB stick from the Record Room's playlists: OneLibrary ``exportLibrary.db``, the DeviceSQL
``export.pdb`` / ``exportExt.pdb`` that CDJ-3000 firmware 3.2x reads instead, the audio under
``Contents/``, and an ANLZ set per track under ``PIONEER/USBANLZ/`` -- the same writers as
``cli.py export-onelibrary`` (OneLibraryWriter, devicesql_writer, PioneerExporter).

The selected tracks are assembled in a throwaway FableGear database inside the job's work folder:
a track FableGear already knows keeps FableGear's record (Iron's BPM and key, its beat grid and
cues, and its id, so the per-track waveform cache is reused); any other track gets the metadata the
playlist source supplied. Nothing is written to the user's FableGear database or to Rekordbox.

Targets: a drive with no ``PIONEER`` folder, or a stick this module wrote before (it leaves
``PIONEER/rekordbox/FABLEGEAR_EXPORT.txt``). A Rekordbox-made export is never touched. Re-exporting
to a FableGear stick replaces the three databases and the USBANLZ tree; audio already copied is
kept when its size matches.

HONESTY LIMIT: as for every device-library writer here, no deck has loaded a FableGear-only stick
yet (dev/docs/devicesql_export_writer.md section 4, deck tests A and B).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MARKER_NAME = "FABLEGEAR_EXPORT.txt"
_LIBRARY_FILES = ("exportLibrary.db", "exportLibrary.db-wal", "exportLibrary.db-shm",
                  "export.pdb", "exportExt.pdb")


def marker_path(drive: Path) -> Path:
    return Path(drive) / "PIONEER" / "rekordbox" / MARKER_NAME


def is_fablegear_export(drive: Path) -> bool:
    return marker_path(drive).is_file()


@dataclass
class SourceTrack:
    """One track as the playlist source (e.g. Rekordbox) describes it."""
    path: Path
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    label: str | None = None
    bpm: float | None = None
    key: str | None = None
    duration: float | None = None
    year: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    comment: str | None = None
    rating: int = 0
    color: str | None = None
    bit_rate: int | None = None
    sample_rate: int | None = None


@dataclass
class SourcePlaylist:
    """A playlist or folder; ``track_paths`` in play order (folders have none)."""
    key: str
    name: str
    parent_key: str | None = None
    is_folder: bool = False
    track_paths: list[str] = field(default_factory=list)


@dataclass
class DeviceExportResult:
    tracks_written: int = 0
    playlists_written: int = 0
    anlz_written: int = 0
    audio_copied: int = 0
    tracks_from_fablegear: int = 0
    errors: list[str] = field(default_factory=list)


def _record_from_source(t: SourceTrack):
    from fablegear_database.database import ContentRecord

    size = t.path.stat().st_size if t.path.exists() else 0
    return ContentRecord(
        file_path=str(t.path), file_name=t.path.name, file_size=size, duration=t.duration,
        format=t.path.suffix.lstrip(".").lower() or None, bit_rate=t.bit_rate, sample_rate=t.sample_rate,
        title=t.title or t.path.stem, artist=t.artist, album=t.album, genre=t.genre, label=t.label,
        bpm=t.bpm, key=t.key, year=t.year, track_number=t.track_number, disc_number=t.disc_number,
        comment=t.comment, rating=t.rating or 0, color=t.color,
    )


def build_export_database(work_dir: Path, tracks: list[SourceTrack], playlists: list[SourcePlaylist],
                          fablegear_db=None):
    """A throwaway FableGear database holding exactly the export set. Returns (db, from_fablegear_count)."""
    from fablegear_database.database import FableGearDatabase
    from fablegear_database.schema import DatabaseConfig

    db = FableGearDatabase(DatabaseConfig(db_path=Path(work_dir) / "export.db"))
    by_path: dict[str, int] = {}
    known = 0
    used_ids: set[int] = set()
    pending: list[SourceTrack] = []
    for t in tracks:
        if str(t.path) in by_path:
            continue
        rec = fablegear_db.get_content_by_path(str(t.path)) if fablegear_db is not None else None
        if rec is None or rec.id is None:
            pending.append(t)
            continue
        full = fablegear_db.get_content_with_relations([rec.id])
        rec = full[0] if full else rec
        # FableGear's own analysis wins; the source fills only what FableGear lacks.
        for attr in ("title", "artist", "album", "genre", "label", "bpm", "key", "duration", "year",
                     "track_number", "disc_number", "comment", "color", "bit_rate", "sample_rate"):
            if getattr(rec, attr, None) in (None, "") and getattr(t, attr, None) not in (None, ""):
                setattr(rec, attr, getattr(t, attr))
        cues, grid = list(rec.cues), list(rec.beatgrid)
        cid = db.insert_content(rec)
        if cues:
            db.bulk_upsert_cues(cid, cues)
        if grid:
            db.bulk_upsert_beatgrids(cid, grid)
        by_path[str(t.path)] = cid
        used_ids.add(cid)
        known += 1
    next_id = max(used_ids, default=0) + 1
    if fablegear_db is not None:
        try:
            with fablegear_db.connection() as conn:
                next_id = max(next_id, (conn.execute("SELECT MAX(id) FROM fg_content").fetchone()[0] or 0) + 1)
        except Exception:
            pass
    for t in pending:
        if str(t.path) in by_path:
            continue
        rec = _record_from_source(t)
        rec.id = next_id  # clear of every FableGear id, so no other track's waveform cache is reused
        next_id += 1
        by_path[str(t.path)] = db.insert_content(rec)

    ids_by_key: dict[str, int] = {}
    order = {p.key: i for i, p in enumerate(playlists)}
    remaining = sorted(playlists, key=lambda p: order[p.key])
    while remaining:
        progress = False
        for p in list(remaining):
            if p.parent_key and p.parent_key not in ids_by_key and any(q.key == p.parent_key for q in remaining):
                continue
            parent = ids_by_key.get(p.parent_key) if p.parent_key else None
            pid = db.create_playlist(p.name or ("Folder" if p.is_folder else "Playlist"), parent_id=parent,
                                     playlist_type="folder" if p.is_folder else "playlist")
            ids_by_key[p.key] = pid
            for path in p.track_paths:
                cid = by_path.get(path)
                if cid is not None:
                    db.add_song(pid, cid)
            remaining.remove(p)
            progress = True
        if not progress:  # a parent cycle: attach what is left at the root
            for p in remaining:
                p.parent_key = None
    return db, known


def _clear_previous_export(drive: Path) -> None:
    rb = drive / "PIONEER" / "rekordbox"
    for name in _LIBRARY_FILES:
        (rb / name).unlink(missing_ok=True)
    anlz = drive / "PIONEER" / "USBANLZ"
    if anlz.is_dir():
        shutil.rmtree(anlz)


def write_usb_library(
    drive: Path,
    export_db,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    still_mounted: Callable[[], bool] | None = None,
) -> DeviceExportResult:
    """Write the whole Pioneer stick for ``export_db`` onto ``drive``. See the module docstring."""
    from chop_shop.devicesql_writer import write_device_library
    from fablegear_database.device_identity import write_dj_profile, write_rbfltr
    from fablegear_database.exporter import PioneerExporter
    from fablegear_database.onelibrary_writer import OneLibraryWriter

    drive = Path(drive)
    pioneer = drive / "PIONEER"
    rb = pioneer / "rekordbox"
    if pioneer.exists() and not is_fablegear_export(drive):
        raise FileExistsError(f"{drive} already holds a Pioneer library FableGear did not write; "
                              "use a blank drive")
    if is_fablegear_export(drive):
        _clear_previous_export(drive)
    rb.mkdir(parents=True, exist_ok=True)
    # The marker goes down first: if this export fails half way, the next one recognises the
    # half-written library as FableGear's and replaces it, instead of refusing the drive.
    marker_path(drive).write_text(
        "This USB library was written by FableGear. FableGear may replace it on the next export.\n"
        f"Written: {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")

    res = DeviceExportResult()
    if progress:
        progress(0, 0, "Copying audio and writing the library…")
    one = OneLibraryWriter(export_db).write(rb / "exportLibrary.db", include_playlists=True,
                                            stage_audio_to=drive)
    res.tracks_written, res.playlists_written = one.tracks_written, one.playlists_written
    res.audio_copied = one.audio_files_copied
    res.errors += one.errors
    write_device_library(rb, one.device_library, overwrite=True)
    write_rbfltr(pioneer)
    write_dj_profile(pioneer, display_name="FableGear")

    exporter = PioneerExporter(export_db)
    paths = {t.id: t.file_path for t in one.device_library.tracks}
    total = len(one.content_id_map)
    for done, (fg_id, device_id) in enumerate(one.content_id_map.items(), 1):
        if still_mounted is not None and not still_mounted():
            res.errors.append(f"{drive} disappeared during the analysis files ({done - 1}/{total} written)")
            break
        if progress:
            progress(done - 1, total, Path(paths.get(device_id, "")).name)
        try:
            if exporter.export_track_anlz(content_id=fg_id, target_root=drive,
                                          relative_audio_path=paths.get(device_id, ""), device_content_id=device_id):
                res.anlz_written += 1
            else:
                res.errors.append(f"ANLZ not written for {Path(paths.get(device_id, '')).name}")
        except Exception as exc:
            res.errors.append(f"ANLZ for {Path(paths.get(device_id, '')).name}: {exc}")
    if progress:
        progress(total, total, "")
    return res


def export_playlists_to_drive(
    drive: Path,
    tracks: list[SourceTrack],
    playlists: list[SourcePlaylist],
    *,
    fablegear_db=None,
    progress: Callable[[int, int, str], None] | None = None,
    still_mounted: Callable[[], bool] | None = None,
) -> DeviceExportResult:
    """The Record Room's CDJ-ready export: assemble, write, clean up the work folder."""
    with tempfile.TemporaryDirectory(prefix="fablegear_usb_") as work:
        db, known = build_export_database(Path(work), tracks, playlists, fablegear_db)
        res = write_usb_library(drive, db, progress=progress, still_mounted=still_mounted)
        res.tracks_from_fablegear = known
        return res
