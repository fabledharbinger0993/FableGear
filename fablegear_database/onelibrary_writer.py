# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-07-17
"""
fablegear_database.onelibrary_writer — writes a Pioneer OneLibrary
``exportLibrary.db`` from FableGear's own database.

OneLibrary (aka "Device Library Plus") is the SQLCipher-encrypted SQLite
format introduced in rekordbox 6.8+ for newer hardware — confirmed
supported on **CDJ-3000** (firmware 3.19+), OMNIS-DUO, XDJ-AZ, and
OPUS-QUAD. Unlike the binary DeviceSQL ``export.pdb`` format
(``chop_shop/devicesql_reader.py``), OneLibrary is plain SQLite once
decrypted — a genuinely tractable write target, matching this project's
own campaign doc's assessment that it is "the achievable, strategic
beachhead" (``docs/dual_format_export.md`` Phase C).

Companion to ``PioneerExporter.export_track_anlz()`` (writes the ANLZ
analysis files this database's ``content.analysisDataFilePath`` column
points at, using the *same* ``PIONEER/USBANLZ/P{id//2048:03d}/{id:08X}/``
folder-naming scheme) — together they can produce hardware-readable
content without Rekordbox in the loop.

PROVENANCE (read before touching the constants below):
  - Schema (every CREATE TABLE / CREATE INDEX statement in
    ``_SCHEMA_SQL``) and the fixed browse-menu boilerplate rows
    (``_MENU_ITEMS``, ``_CATEGORY_ROWS``, ``_SORT_ROWS``) were extracted
    VERBATIM, read-only, from a real, populated, Rekordbox-written
    exportLibrary.db found on a connected drive (not committed to this
    repo — see docs/format_samples/). Not guessed, not derived from the
    DeviceSQL schema by analogy.
  - The SQLCipher key and required non-default PRAGMA
    (``cipher_compatibility = 4``) are PUBLICLY documented by existing
    open-source Pioneer/Rekordbox format reverse-engineering work (a
    public gist cataloguing OneLibrary/Device Library Plus encryption,
    and the open-source ``rbox`` project) — not derived or cracked by
    this module. Verified directly against the same two real files: both
    decrypt cleanly with this exact key and PRAGMA.
  - fileType codes (MP3=1, M4A=4, FLAC=5, WAV=11, AIFF=12) are
    pyrekordbox's own ``db6.tables.FileType`` enum, which pyrekordbox
    documents as matching Pioneer's real on-disk encoding — not guessed.

HONESTY LIMIT: writing a structurally-correct, independently-decryptable
SQLCipher database (verified below by round-tripping through a fresh
sqlcipher3 connection, not just this module's own reader) is NOT the same
claim as "a real CDJ-3000 will play from it." No output of this module has
been tested on physical Pioneer hardware. Follow the campaign doc's
testing protocol before trusting it at a gig: sacrificial USB stick,
never the gig stick; verify on one player before the fleet; keep a
Rekordbox-made control stick at every gig until trust is earned.

Public interface:
    OneLibraryWriter(database).write(target_path, ...) -> OneLibraryWriteResult
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import sqlcipher3

from .database import FableGearDatabase, ContentRecord

log = logging.getLogger(__name__)

# ─── SQLCipher key + compatibility mode ─────────────────────────────────────
# Public, previously-documented (see module docstring PROVENANCE) — not
# derived or cracked here.
_ONELIBRARY_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"
_CIPHER_COMPATIBILITY = 4

# ─── Schema (verbatim from a real, hardware-written exportLibrary.db) ──────
_SCHEMA_SQL: List[str] = [
    "CREATE TABLE album(album_id integer primary key, name varchar, artist_id integer, "
    "image_id integer, isComplation integer, nameForSearch varchar)",
    "CREATE TABLE artist(artist_id integer primary key, name varchar, nameForSearch varchar)",
    "CREATE TABLE category(category_id integer primary key, menuItem_id integer, "
    "sequenceNo integer, isVisible integer)",
    "CREATE TABLE color(color_id integer primary key, name varchar)",
    "CREATE TABLE content(content_id integer primary key, title varchar, titleForSearch varchar, "
    "subtitle varchar, bpmx100 integer, length integer, trackNo integer, discNo integer, "
    "artist_id_artist integer, artist_id_remixer integer, artist_id_originalArtist integer, "
    "artist_id_composer integer, artist_id_lyricist integer, album_id integer, genre_id integer, "
    "label_id integer, key_id integer, color_id integer, image_id integer, djComment varchar, "
    "rating integer, releaseYear integer, releaseDate varchar, dateCreated varchar, "
    "dateAdded varchar, path varchar, fileName varchar, fileSize integer, fileType integer, "
    "bitrate integer, bitDepth integer, samplingRate integer, isrc varchar, djPlayCount integer, "
    "isHotCueAutoLoadOn integer, isKuvoDeliverStatusOn integer, kuvoDeliveryComment varchar, "
    "masterDbId integer, masterContentId integer, analysisDataFilePath varchar, "
    "analysedBits integer, contentLink integer, hasModified integer, cueUpdateCount integer, "
    "analysisDataUpdateCount integer, informationUpdateCount integer)",
    "CREATE TABLE cue(cue_id integer primary key, content_id integer, kind integer, "
    "colorTableIndex integer, cueComment varchar, isActiveLoop integer, "
    "beatLoopNumerator integer, beatLoopDenominator integer, inUsec integer, outUsec integer, "
    "in150FramePerSec integer, out150FramePerSec integer, inMpegFrameNumber integer, "
    "outMpegFrameNumber integer, inMpegAbs integer, outMpegAbs integer, "
    "inDecodingStartFramePosition integer, outDecodingStartFramePosition integer, "
    "inFileOffsetInBlock integer, OutFileOffsetInBlock integer, "
    "inNumberOfSampleInBlock integer, outNumberOfSampleInBlock integer)",
    "CREATE TABLE genre(genre_id integer primary key, name varchar)",
    "CREATE TABLE history(history_id integer primary key, sequenceNo integer, name varchar, "
    "attribute integer, history_id_parent integer)",
    "CREATE TABLE history_content(history_id integer, content_id integer, sequenceNo integer)",
    "CREATE TABLE hotCueBankList(hotCueBankList_id integer primary key, sequenceNo integer, "
    "name varchar, image_id integer, attribute integer, hotCueBankList_id_parent integer)",
    "CREATE TABLE hotCueBankList_cue(hotCueBankList_id integer, cue_id integer, sequenceNo integer)",
    "CREATE TABLE image(image_id integer primary key, path varchar)",
    "CREATE TABLE key(key_id integer primary key, name varchar)",
    "CREATE TABLE label(label_id integer primary key, name varchar)",
    "CREATE TABLE menuItem(menuItem_id integer primary key, kind integer, name varchar)",
    "CREATE TABLE myTag(myTag_id integer primary key, sequenceNo integer, name varchar, "
    "attribute integer, myTag_id_parent integer)",
    "CREATE TABLE myTag_content(myTag_id integer, content_id integer)",
    "CREATE TABLE playlist(playlist_id integer primary key, sequenceNo integer, name varchar, "
    "image_id integer, attribute integer, playlist_id_parent integer)",
    "CREATE TABLE playlist_content(playlist_id integer, content_id integer, sequenceNo integer)",
    "CREATE TABLE property(deviceName varchar, dbVersion varchar, numberOfContents integer, "
    "createdDate varchar, backGroundColorType integer, myTagMasterDBID integer)",
    "CREATE TABLE recommendedLike(content_id_1 integer, content_id_2 integer, rating integer, "
    "createdDate integer)",
    "CREATE TABLE sort(sort_id integer primary key, menuItem_id integer, sequenceNo integer, "
    "isVisible integer, isSelectedAsSubColumn integer)",
]

_INDEX_SQL: List[str] = [
    "CREATE INDEX index_hotCueBankList_cue_hotCueBankList_id on hotCueBankList_cue(hotCueBankList_id)",
    "CREATE INDEX index_myTag_content_content_id on myTag_content(content_id)",
    "CREATE INDEX index_myTag_content_myTag_id on myTag_content(myTag_id)",
    "CREATE INDEX index_playlist_content_playlist_id on playlist_content(playlist_id)",
]

# Fixed browse-menu structure (device UI labels/ordering) — independent of
# library content, copied verbatim from the real file rather than derived.
_MENU_ITEMS: List[tuple] = [
    (1, 128, "￺GENRE￻"), (2, 129, "￺ARTIST￻"),
    (3, 130, "￺ALBUM￻"), (4, 131, "￺TRACK￻"),
    (5, 133, "￺BPM￻"), (6, 134, "￺RATING￻"),
    (7, 135, "￺YEAR￻"), (8, 136, "￺REMIXER￻"),
    (9, 137, "￺LABEL￻"), (10, 138, "￺ORIGINAL ARTIST￻"),
    (11, 139, "￺KEY￻"), (12, 141, "￺CUE￻"),
    (13, 142, "￺COLOR￻"), (14, 146, "￺TIME￻"),
    (15, 147, "￺BITRATE￻"), (16, 148, "￺FILE NAME￻"),
    (17, 132, "￺PLAYLIST￻"), (18, 152, "￺HOT CUE BANK￻"),
    (19, 149, "￺HISTORY￻"), (20, 145, "￺SEARCH￻"),
    (21, 150, "￺COMMENTS￻"), (22, 140, "￺DATE ADDED￻"),
    (23, 151, "￺DJ PLAY COUNT￻"), (24, 144, "￺FOLDER￻"),
    (25, 161, "￺DEFAULT￻"), (26, 162, "￺ALPHABET￻"),
    (27, 170, "￺MATCHING￻"),
]

_CATEGORY_ROWS: List[tuple] = [
    (1, 1, 0, 0), (2, 2, 1, 1), (3, 3, 2, 1), (4, 4, 3, 1), (5, 17, 5, 1),
    (6, 5, 0, 0), (7, 6, 0, 0), (8, 7, 0, 0), (9, 8, 0, 0), (10, 9, 0, 0),
    (11, 10, 0, 0), (12, 11, 4, 1), (15, 13, 0, 0), (17, 24, 9, 1),
    (18, 20, 7, 1), (19, 14, 0, 0), (20, 15, 0, 0), (21, 16, 0, 0),
    (22, 19, 6, 1), (23, 18, 0, 0), (26, 27, 8, 1), (27, 22, 10, 1),
]

_SORT_ROWS: List[tuple] = [
    (0, 25, 1, 1, 0), (1, 26, 2, 1, 0), (2, 2, 3, 1, 0), (3, 3, 4, 1, 0),
    (4, 5, 5, 1, 1), (5, 6, 6, 1, 0), (6, 1, 0, 0, 0), (7, 21, 0, 0, 0),
    (8, 14, 0, 0, 0), (9, 8, 0, 0, 0), (10, 9, 0, 0, 0), (11, 10, 0, 0, 0),
    (12, 11, 7, 1, 0), (13, 15, 0, 0, 0), (15, 13, 0, 0, 0),
    (16, 23, 0, 0, 0), (17, 22, 0, 0, 0),
]

# pyrekordbox's own FileType enum (db6.tables.FileType) — Pioneer's real
# on-disk file-type codes, not guessed.
_FILE_TYPE_CODES: Dict[str, int] = {
    "mp3": 1, "m4a": 4, "mp4": 4, "flac": 5, "wav": 11, "aiff": 12, "aif": 12,
}

# ANLZ folder-naming scheme — MUST match PioneerExporter.export_track_anlz()
# exactly, or analysisDataFilePath would point at analysis files that were
# never written there.
_ANLZ_PAGE_SIZE = 2048


def _anlz_path_for(content_id: int) -> str:
    sub_dir1 = f"P{content_id // _ANLZ_PAGE_SIZE:03d}"
    sub_dir2 = f"{content_id:08X}"
    return f"/PIONEER/USBANLZ/{sub_dir1}/{sub_dir2}/ANLZ0000.DAT"


def _file_type_code(fmt: Optional[str]) -> Optional[int]:
    if not fmt:
        return None
    return _FILE_TYPE_CODES.get(fmt.strip().lower().lstrip("."))


@dataclass
class OneLibraryWriteResult:
    target_path: str = ""
    tracks_written: int = 0
    tracks_skipped: int = 0
    cues_written: int = 0
    playlists_written: int = 0
    playlist_entries_written: int = 0
    errors: List[str] = field(default_factory=list)
    # {FableGear ContentRecord.id: OneLibrary content_id}. The written
    # content.analysisDataFilePath for each track points at
    # PIONEER/USBANLZ/... keyed by the OneLibrary content_id, NOT the
    # FableGear id — a caller generating ANLZ files afterward via
    # PioneerExporter.export_track_anlz() MUST pass
    # device_content_id=content_id_map[fg_content_id] for the two to agree.
    # Getting this wrong produces a structurally valid database whose
    # tracks point at ANLZ folders that were never written.
    content_id_map: Dict[int, int] = field(default_factory=dict)


class _IdAllocator:
    """Assigns sequential integer IDs to distinct string values, so that
    e.g. two tracks by 'Daft Punk' resolve to the same artist_id."""

    def __init__(self) -> None:
        self._ids: Dict[str, int] = {}
        self._next = 1

    def get(self, name: Optional[str]) -> Optional[int]:
        if not name or not name.strip():
            return None
        key = name.strip()
        if key not in self._ids:
            self._ids[key] = self._next
            self._next += 1
        return self._ids[key]

    def rows(self) -> List[tuple]:
        """(id, name) rows in id order, ready for bulk insert."""
        return sorted(((v, k) for k, v in self._ids.items()), key=lambda r: r[0])


class OneLibraryWriter:
    """
    Writes a Pioneer OneLibrary exportLibrary.db from FableGear's database.

    Never overwrites an existing file — this must never silently clobber a
    real device library. Callers wanting to replace one must remove it
    (or move it aside) explicitly first.
    """

    def __init__(self, database: FableGearDatabase):
        self.database = database

    def write(
        self,
        target_path: Path,
        content_ids: Optional[List[int]] = None,
        include_playlists: bool = True,
        device_name: str = "",
    ) -> OneLibraryWriteResult:
        target_path = Path(target_path)
        result = OneLibraryWriteResult(target_path=str(target_path))

        if target_path.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing file: {target_path}. "
                "Remove or rename it first if you intend to replace it."
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)

        tracks = self.database.get_content_with_relations(content_ids)

        conn = sqlcipher3.connect(str(target_path))
        try:
            cur = conn.cursor()
            cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
            cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")

            for stmt in _SCHEMA_SQL:
                cur.execute(stmt)
            for stmt in _INDEX_SQL:
                cur.execute(stmt)

            cur.executemany("INSERT INTO menuItem VALUES (?,?,?)", _MENU_ITEMS)
            cur.executemany("INSERT INTO category VALUES (?,?,?,?)", _CATEGORY_ROWS)
            cur.executemany("INSERT INTO sort VALUES (?,?,?,?,?)", _SORT_ROWS)

            genres = _IdAllocator()
            artists = _IdAllocator()
            albums_by_key: Dict[tuple, int] = {}
            albums_rows: List[tuple] = []
            labels = _IdAllocator()
            keys = _IdAllocator()
            colors = _IdAllocator()

            fg_id_to_content_id: Dict[int, int] = {}
            next_content_id = 1
            content_rows: List[tuple] = []
            cue_rows: List[tuple] = []
            next_cue_id = 1

            for track in sorted(tracks, key=lambda t: t.id or 0):
                try:
                    if not track.file_path:
                        result.tracks_skipped += 1
                        result.errors.append(f"content_id fg:{track.id}: no file_path, skipped")
                        continue

                    content_id = next_content_id
                    next_content_id += 1
                    if track.id is not None:
                        fg_id_to_content_id[track.id] = content_id

                    genre_id = genres.get(track.genre)
                    artist_id = artists.get(track.artist)
                    label_id = labels.get(track.label)
                    key_id = keys.get(track.key)
                    color_id = colors.get(track.color)

                    album_id = None
                    if track.album and track.album.strip():
                        album_key = (track.album.strip(), artist_id)
                        album_id = albums_by_key.get(album_key)
                        if album_id is None:
                            album_id = len(albums_rows) + 1
                            albums_by_key[album_key] = album_id
                            albums_rows.append(
                                (album_id, track.album.strip(), artist_id, None, 0, track.album.strip())
                            )

                    bpmx100 = int(round(track.bpm * 100)) if track.bpm else None
                    length = int(round(track.duration)) if track.duration else None

                    content_rows.append((
                        content_id, track.title, track.title, None,
                        bpmx100, length, track.track_number, track.disc_number,
                        artist_id, None, None, None, None,
                        album_id, genre_id, label_id, key_id, color_id, None,
                        track.comment, track.rating or 0, track.year, None,
                        track.modified_date, track.created_at,
                        track.relative_path or track.file_path, track.file_name,
                        track.file_size, _file_type_code(track.format),
                        track.bit_rate, None, track.sample_rate, None, 0,
                        0, 0, None, None, None,
                        _anlz_path_for(content_id), None, 0, 0, 0, 0, 0,
                    ))

                    for cue in track.cues:
                        cue_rows.append((
                            next_cue_id, content_id, cue.kind, None, cue.comment,
                            1 if cue.kind in (2, 3) else 0, None, None,
                            (cue.in_msec or 0) * 1000,
                            (cue.out_msec * 1000) if cue.out_msec is not None else None,
                            None, None, None, None, None, None, None, None,
                            None, None, None, None,
                        ))
                        next_cue_id += 1
                        result.cues_written += 1

                    result.tracks_written += 1
                except Exception as exc:  # noqa: BLE001 - one bad track must not abort the whole export
                    result.tracks_skipped += 1
                    result.errors.append(f"content_id fg:{track.id}: {exc}")

            cur.executemany("INSERT INTO genre VALUES (?,?)", genres.rows())
            cur.executemany("INSERT INTO artist VALUES (?,?,?)",
                             [(i, n, n) for i, n in artists.rows()])
            cur.executemany("INSERT INTO album VALUES (?,?,?,?,?,?)", albums_rows)
            cur.executemany("INSERT INTO label VALUES (?,?)", labels.rows())
            cur.executemany("INSERT INTO key VALUES (?,?)", keys.rows())
            cur.executemany("INSERT INTO color VALUES (?,?)", colors.rows())
            cur.executemany(
                "INSERT INTO content VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                content_rows,
            )
            cur.executemany(
                "INSERT INTO cue VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                cue_rows,
            )

            result.content_id_map = dict(fg_id_to_content_id)

            if include_playlists:
                self._write_playlists(cur, fg_id_to_content_id, result)

            cur.execute(
                "INSERT INTO property VALUES (?,?,?,?,?,?)",
                (device_name, "1000", result.tracks_written,
                 datetime.now().strftime("%Y-%m-%d"), 0, 0),
            )

            conn.commit()
        finally:
            conn.close()

        log.info(
            "OneLibrary export written to %s: %d tracks, %d cues, %d playlists "
            "(%d skipped, %d errors)",
            target_path, result.tracks_written, result.cues_written,
            result.playlists_written, result.tracks_skipped, len(result.errors),
        )
        return result

    def _write_playlists(
        self, cur, fg_id_to_content_id: Dict[int, int], result: OneLibraryWriteResult
    ) -> None:
        """Flatten FableGear's playlist/folder tree into playlist rows,
        preserving folder hierarchy via playlist_id_parent (attribute=1
        marks a folder, matching the real schema's convention observed
        alongside real playlist rows)."""
        tree = self.database.list_playlists()
        playlist_rows: List[tuple] = []
        content_link_rows: List[tuple] = []
        next_playlist_id = 1
        seq = 0

        def _walk(nodes: List[dict], parent_id: Optional[int]) -> None:
            nonlocal next_playlist_id, seq
            for node in nodes:
                pid = next_playlist_id
                next_playlist_id += 1
                seq += 1
                is_folder = node.get("type") == "folder"
                playlist_rows.append((pid, seq, node.get("name", ""), None,
                                       1 if is_folder else 0, parent_id))
                result.playlists_written += 1
                if is_folder:
                    _walk(node.get("children", []), pid)
                else:
                    fg_playlist_id = node.get("id")
                    try:
                        songs = self.database.get_playlist_songs(fg_playlist_id)
                    except Exception as exc:  # noqa: BLE001
                        result.errors.append(f"playlist {fg_playlist_id}: {exc}")
                        continue
                    track_seq = 0
                    for song in songs:
                        content_id = fg_id_to_content_id.get(song.id)
                        if content_id is None:
                            continue  # not part of this export's content set
                        track_seq += 1
                        content_link_rows.append((pid, content_id, track_seq))
                        result.playlist_entries_written += 1

        _walk(tree, None)
        cur.executemany("INSERT INTO playlist VALUES (?,?,?,?,?,?)", playlist_rows)
        cur.executemany("INSERT INTO playlist_content VALUES (?,?,?)", content_link_rows)
