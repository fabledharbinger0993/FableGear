"""
Tests for fablegear_database/onelibrary_writer.py.

Every test round-trips through a genuinely INDEPENDENT sqlcipher3
connection (the same PRAGMA key + cipher_compatibility a real device or
any other tool would use) rather than reading the file back through this
module's own writer internals — proving the output is a standalone,
correctly-encrypted SQLite database, not just internally self-consistent
with the code that produced it.

Schema and boilerplate rows (menuItem/category/sort) are verbatim from a
real, populated, Rekordbox-written exportLibrary.db found on a connected
drive (not committed to this repo — see onelibrary_writer.py module
docstring PROVENANCE). The SQLCipher key is publicly documented by
existing open-source Pioneer format reverse-engineering work, not derived
here. No test in this file has been validated against physical CDJ-3000
hardware — see the module's HONESTY LIMIT.
"""

import sys
from pathlib import Path

import pytest
import sqlcipher3

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import FableGearDatabase, ContentRecord, CueRecord  # noqa: E402
from fablegear_database.schema import DatabaseConfig  # noqa: E402
from fablegear_database.onelibrary_writer import (  # noqa: E402
    OneLibraryWriter,
    _ONELIBRARY_KEY,
    _CIPHER_COMPATIBILITY,
    _anlz_path_for,
)


@pytest.fixture
def fg_db(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))


def _open_independent(path: Path):
    """A fresh sqlcipher3 connection using only the public key + PRAGMA —
    simulates any other tool (or, eventually, real hardware) reading the
    file, not this module's own code path."""
    conn = sqlcipher3.connect(str(path))
    cur = conn.cursor()
    cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
    cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
    return conn, cur


def _make_track(**overrides) -> ContentRecord:
    defaults = dict(
        file_path="/music/track.mp3", file_name="track.mp3", file_size=1000,
        title="Track", artist="Artist", album="Album", bpm=128.0, key="8A",
        genre="House", label="Label", year=2024, track_number=1,
        duration=180.0, format="mp3", bit_rate=320, sample_rate=44100, rating=3,
        color="#ff0000",
    )
    defaults.update(overrides)
    return ContentRecord(**defaults)


# ── Basic round trip ──────────────────────────────────────────────────────

def test_single_track_round_trips_correctly(fg_db, tmp_path):
    rid = fg_db.insert_content(_make_track())
    fg_db.bulk_upsert_cues(rid, [CueRecord(kind=0, in_msec=1000, comment="intro")])

    target = tmp_path / "exportLibrary.db"
    result = OneLibraryWriter(fg_db).write(target, include_playlists=False)

    assert result.tracks_written == 1
    assert result.tracks_skipped == 0
    assert not result.errors

    conn, cur = _open_independent(target)
    cur.execute("SELECT title, bpmx100, path, analysisDataFilePath, trackNo, discNo, rating "
                "FROM content;")
    row = cur.fetchone()
    assert row == ("Track", 12800, "/music/track.mp3", _anlz_path_for(1), 1, None, 3)

    cur.execute("SELECT name FROM artist;")
    assert cur.fetchall() == [("Artist",)]
    cur.execute("SELECT name FROM genre;")
    assert cur.fetchall() == [("House",)]

    cur.execute("SELECT content_id, kind, cueComment, inUsec, outUsec FROM cue;")
    assert cur.fetchall() == [(1, 0, "intro", 1000000, None)]
    conn.close()


def test_loop_cue_converts_in_and_out_msec(fg_db, tmp_path):
    rid = fg_db.insert_content(_make_track())
    fg_db.bulk_upsert_cues(rid, [CueRecord(kind=2, slot=0, in_msec=5000, out_msec=9000)])

    target = tmp_path / "exportLibrary.db"
    OneLibraryWriter(fg_db).write(target, include_playlists=False)

    conn, cur = _open_independent(target)
    cur.execute("SELECT kind, inUsec, outUsec, isActiveLoop FROM cue;")
    assert cur.fetchall() == [(2, 5000000, 9000000, 1)]
    conn.close()


# ── Lookup-table dedup ────────────────────────────────────────────────────

def test_shared_artist_gets_one_id_across_tracks(fg_db, tmp_path):
    fg_db.insert_content(_make_track(file_path="/a.mp3", title="A", artist="Daft Punk"))
    fg_db.insert_content(_make_track(file_path="/b.mp3", title="B", artist="Daft Punk"))
    fg_db.insert_content(_make_track(file_path="/c.mp3", title="C", artist="Someone Else"))

    target = tmp_path / "exportLibrary.db"
    OneLibraryWriter(fg_db).write(target, include_playlists=False)

    conn, cur = _open_independent(target)
    cur.execute("SELECT COUNT(*) FROM artist;")
    assert cur.fetchone() == (2,)
    cur.execute("SELECT artist_id_artist FROM content WHERE title IN ('A','B') ORDER BY title;")
    ids = [r[0] for r in cur.fetchall()]
    assert ids[0] == ids[1]
    conn.close()


def test_no_genre_no_album_leaves_null_fks(fg_db, tmp_path):
    fg_db.insert_content(_make_track(genre=None, album=None, key=None, label=None, color=None))
    target = tmp_path / "exportLibrary.db"
    result = OneLibraryWriter(fg_db).write(target, include_playlists=False)
    assert result.tracks_written == 1

    conn, cur = _open_independent(target)
    cur.execute("SELECT genre_id, album_id, key_id, label_id, color_id FROM content;")
    assert cur.fetchone() == (None, None, None, None, None)
    cur.execute("SELECT COUNT(*) FROM genre;")
    assert cur.fetchone() == (0,)
    conn.close()


def test_missing_bpm_and_duration_are_null_not_zero(fg_db, tmp_path):
    fg_db.insert_content(_make_track(bpm=None, duration=None))
    target = tmp_path / "exportLibrary.db"
    OneLibraryWriter(fg_db).write(target, include_playlists=False)

    conn, cur = _open_independent(target)
    cur.execute("SELECT bpmx100, length FROM content;")
    assert cur.fetchone() == (None, None)
    conn.close()


def test_track_without_file_path_is_skipped_not_fatal(fg_db, tmp_path):
    fg_db.insert_content(_make_track(file_path="", title="Bad"))
    fg_db.insert_content(_make_track(file_path="/ok.mp3", title="Good"))

    target = tmp_path / "exportLibrary.db"
    result = OneLibraryWriter(fg_db).write(target, include_playlists=False)

    assert result.tracks_written == 1
    assert result.tracks_skipped == 1
    assert result.errors  # explains why, not silently dropped

    conn, cur = _open_independent(target)
    cur.execute("SELECT title FROM content;")
    assert cur.fetchall() == [("Good",)]
    conn.close()


# ── Playlists ──────────────────────────────────────────────────────────────

def test_playlist_membership_round_trips(fg_db, tmp_path):
    rid1 = fg_db.insert_content(_make_track(file_path="/a.mp3", title="A"))
    rid2 = fg_db.insert_content(_make_track(file_path="/b.mp3", title="B"))
    pid = fg_db.create_playlist("My Set")
    fg_db.add_song(pid, rid1)
    fg_db.add_song(pid, rid2)

    target = tmp_path / "exportLibrary.db"
    result = OneLibraryWriter(fg_db).write(target, include_playlists=True)

    assert result.playlists_written == 1
    assert result.playlist_entries_written == 2

    conn, cur = _open_independent(target)
    cur.execute("SELECT name, attribute, playlist_id_parent FROM playlist;")
    assert cur.fetchall() == [("My Set", 0, None)]
    cur.execute(
        "SELECT c.title FROM playlist_content pc JOIN content c ON c.content_id = pc.content_id "
        "ORDER BY pc.sequenceNo;"
    )
    assert cur.fetchall() == [("A",), ("B",)]
    conn.close()


def test_folder_hierarchy_preserved(fg_db, tmp_path):
    rid = fg_db.insert_content(_make_track())
    folder_id = fg_db.create_playlist("Sets", playlist_type="folder")
    pid = fg_db.create_playlist("Inside Folder", parent_id=folder_id)
    fg_db.add_song(pid, rid)

    target = tmp_path / "exportLibrary.db"
    result = OneLibraryWriter(fg_db).write(target, include_playlists=True)

    assert result.playlists_written == 2  # the folder and the playlist inside it
    assert result.playlist_entries_written == 1

    conn, cur = _open_independent(target)
    cur.execute("SELECT name, attribute, playlist_id_parent FROM playlist ORDER BY attribute DESC;")
    rows = cur.fetchall()
    assert rows[0] == ("Sets", 1, None)  # folder: attribute=1, no parent
    folder_id_in_db = None
    cur.execute("SELECT playlist_id FROM playlist WHERE name='Sets';")
    folder_id_in_db = cur.fetchone()[0]
    assert rows[1] == ("Inside Folder", 0, folder_id_in_db)  # playlist: attribute=0, parented
    conn.close()


# ── Safety ─────────────────────────────────────────────────────────────────

def test_refuses_to_overwrite_existing_file(fg_db, tmp_path):
    target = tmp_path / "exportLibrary.db"
    target.write_bytes(b"not actually a device library")

    with pytest.raises(FileExistsError):
        OneLibraryWriter(fg_db).write(target)

    # Untouched — the guard fires before anything is opened for write.
    assert target.read_bytes() == b"not actually a device library"


# ── Schema fidelity ────────────────────────────────────────────────────────

def test_schema_matches_every_table_from_the_real_file(fg_db, tmp_path):
    """Guards against silent schema drift — every table name observed in
    the real hardware sample must exist in the written file."""
    fg_db.insert_content(_make_track())
    target = tmp_path / "exportLibrary.db"
    OneLibraryWriter(fg_db).write(target, include_playlists=False)

    conn, cur = _open_independent(target)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = {r[0] for r in cur.fetchall()}
    expected = {
        "album", "artist", "category", "color", "content", "cue", "genre",
        "history", "history_content", "hotCueBankList", "hotCueBankList_cue",
        "image", "key", "label", "menuItem", "myTag", "myTag_content",
        "playlist", "playlist_content", "property", "recommendedLike", "sort",
    }
    assert expected <= tables
    conn.close()


def test_content_id_map_lets_caller_match_anlz_paths(fg_db, tmp_path):
    """The whole point of exposing content_id_map: a caller generating ANLZ
    files afterward via PioneerExporter.export_track_anlz(device_content_id=...)
    needs this exact mapping, or the written analysisDataFilePath points at
    a folder ANLZ generation never wrote to."""
    rid = fg_db.insert_content(_make_track())
    target = tmp_path / "exportLibrary.db"
    result = OneLibraryWriter(fg_db).write(target, include_playlists=False)

    assert rid in result.content_id_map
    onelibrary_id = result.content_id_map[rid]

    conn, cur = _open_independent(target)
    cur.execute("SELECT analysisDataFilePath FROM content WHERE content_id=?;", (onelibrary_id,))
    assert cur.fetchone() == (_anlz_path_for(onelibrary_id),)
    conn.close()


def test_property_row_reflects_real_track_count(fg_db, tmp_path):
    for i in range(3):
        fg_db.insert_content(_make_track(file_path=f"/{i}.mp3", title=f"T{i}"))
    target = tmp_path / "exportLibrary.db"
    OneLibraryWriter(fg_db).write(target, include_playlists=False)

    conn, cur = _open_independent(target)
    cur.execute("SELECT numberOfContents FROM property;")
    assert cur.fetchone() == (3,)
    conn.close()
