"""
Tests for fablegear_database.rekordbox_fixture — the encrypted Rekordbox v6
library builder used by other DB-deep tests.

These pin the two things a stub library must get right or every downstream
DB-deep test is worthless: it must actually open through pyrekordbox with the
application key, and it must be *writable* — i.e. carry the localUpdateCount
agentRegistry row, without which the first USN-bumping write raises
AttributeError deep inside pyrekordbox.

Run from the repo root:
    python3 -m pytest tests/test_rekordbox_fixture.py -v
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("sqlcipher3", reason="SQLCipher driver not installed")
pytest.importorskip("pyrekordbox", reason="pyrekordbox not installed")

from pyrekordbox import Rekordbox6Database

from fablegear_database.rekordbox_fixture import (
    FixtureTrack,
    build_rekordbox_db,
    default_key,
)


def _open(path: Path) -> Rekordbox6Database:
    return Rekordbox6Database(path=str(path), key=default_key())


def test_empty_fixture_opens_and_is_writable(tmp_path):
    """An empty library must open with the app key AND accept a USN-bumping
    write — the localUpdateCount seed is what makes the second part work."""
    db_path = build_rekordbox_db(tmp_path / "master.db")
    db = _open(db_path)
    try:
        assert list(db.get_content()) == []
        # This is the exact call that fails on an unseeded stub:
        new_usn = db.increment_local_usn()
        assert isinstance(new_usn, int)
    finally:
        db.close()


def test_seeded_tracks_are_readable(tmp_path):
    db_path = build_rekordbox_db(
        tmp_path / "master.db",
        tracks=[
            FixtureTrack("/Volumes/DJ/Music/a.mp3", title="Alpha", artist="Nula", bpm=128.0),
            FixtureTrack("/Volumes/DJ/Music/b.aiff", title="Beta", artist="Nula", bpm=124.0),
            FixtureTrack("/Volumes/DJ/Music/c.flac", title="Gamma", artist="Other"),
        ],
    )
    db = _open(db_path)
    try:
        by_path = {c.FolderPath: c for c in db.get_content()}
        assert set(by_path) == {
            "/Volumes/DJ/Music/a.mp3",
            "/Volumes/DJ/Music/b.aiff",
            "/Volumes/DJ/Music/c.flac",
        }
        a = by_path["/Volumes/DJ/Music/a.mp3"]
        assert a.Title == "Alpha"
        assert a.Artist.Name == "Nula"
        assert a.BPM == 12800  # rekordbox stores hundredths of a BPM

        # Two tracks share one artist → exactly one DjmdArtist row, reused.
        assert by_path["/Volumes/DJ/Music/b.aiff"].ArtistID == a.ArtistID
        assert by_path["/Volumes/DJ/Music/c.flac"].ArtistID != a.ArtistID
    finally:
        db.close()


def test_path_update_persists_through_encryption(tmp_path):
    """A committed FolderPath change must survive close/reopen — i.e. writes
    land in the encrypted file, not just the in-memory session. (This is the
    DB half of a rename; pyrekordbox's update_content_path adds ANLZ-file
    handling on top, which needs a full rekordbox share/ tree and is out of
    scope for the fixture itself.)"""
    old = "/Volumes/DJ/Music/old.mp3"
    new = "/Volumes/DJ/Music/new.mp3"
    db_path = build_rekordbox_db(
        tmp_path / "master.db",
        tracks=[FixtureTrack(old, title="Song", artist="Artist")],
    )
    db = _open(db_path)
    try:
        row = db.get_content(FolderPath=old).one()
        row.FolderPath = new
        # Commit via the underlying session: pyrekordbox's own db.commit()
        # refuses to write while Rekordbox is running, which is irrelevant to
        # whether the encrypted file round-trips a committed change.
        db.session.commit()
    finally:
        db.close()

    db2 = _open(db_path)
    try:
        assert db2.get_content(FolderPath=old).all() == []
        assert len(db2.get_content(FolderPath=new).all()) == 1
    finally:
        db2.close()


def test_fablegear_db_connection_opens_fixture(tmp_path):
    """FableGear opens libraries through db_connection.read_db — the fixture
    must satisfy that path too, not just a raw pyrekordbox handle."""
    from db_connection import read_db

    db_path = build_rekordbox_db(
        tmp_path / "master.db",
        tracks=[FixtureTrack("/Volumes/DJ/z.mp3", title="Zed", artist="Zee")],
    )
    with read_db(db_path) as db:
        rows = db.get_content(FolderPath="/Volumes/DJ/z.mp3").all()
        assert len(rows) == 1
        assert rows[0].Title == "Zed"
