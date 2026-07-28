"""
Tests for the database-first Local view of the library browser.

Run from the repo root:
    pip install pytest && python3 -m pytest tests/test_library_browser.py -v

These prove the Local view renders straight from the FableGear database — no
filesystem re-scan, no tag re-extraction — which is the point of the
database-first Record Room library list.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.schema import DatabaseConfig
from library_browser.core import LibraryBrowser, ViewMode


@pytest.fixture
def db(tmp_path):
    database = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))
    database.insert_content(ContentRecord(
        file_path="/Volumes/USB_A/a.mp3", file_name="a.mp3", file_size=100,
        title="Alpha", artist="DJ One", format="mp3", drive="USB_A",
        bpm=128.0, in_rekordbox=True,
    ))
    database.insert_content(ContentRecord(
        file_path="/Volumes/USB_A/b.flac", file_name="b.flac", file_size=200,
        title="Beta", artist="DJ Two", format="flac", drive="USB_A",
        bpm=124.0, in_rekordbox=False,
    ))
    database.insert_content(ContentRecord(
        file_path="/Volumes/USB_B/c.wav", file_name="c.wav", file_size=300,
        title="Gamma", artist="DJ One", format="wav", drive="USB_B",
        in_rekordbox=False,
    ))
    return database


@pytest.fixture
def browser(db, tmp_path):
    return LibraryBrowser(database=db, cache_dir=tmp_path / "cache")


def test_browser_with_db_defaults_to_local_mode(browser):
    assert browser.get_view_mode() == ViewMode.LOCAL


def test_local_view_renders_from_database(browser):
    browser.load_local()
    files = browser.get_files()
    assert len(files) == 3

    by_name = {f.file_name: f for f in files}
    assert by_name["a.mp3"].title == "Alpha"
    assert by_name["a.mp3"].format == "mp3"
    assert by_name["a.mp3"].drive == "USB_A"
    assert by_name["a.mp3"].in_rekordbox is True
    assert by_name["b.flac"].in_rekordbox is False


def test_local_view_search(browser):
    browser.load_local()
    results = browser.search("DJ One")
    assert {f.file_name for f in results} == {"a.mp3", "c.wav"}


def test_local_view_sort_descending_by_size(browser):
    browser.load_local()
    browser.sort("file_size", ascending=False)
    files = browser.get_files()
    assert [f.file_name for f in files] == ["c.wav", "b.flac", "a.mp3"]


def test_local_view_statistics(browser):
    browser.load_local()
    stats = browser.get_statistics()
    assert stats["total_files"] == 3
    assert stats["in_rekordbox"] == 1
    assert stats["not_in_rekordbox"] == 2
    assert stats["format_counts"] == {"mp3": 1, "flac": 1, "wav": 1}
    assert stats["drive_counts"] == {"USB_A": 2, "USB_B": 1}


def test_get_tracks_marks_missing_files(browser, db, tmp_path):
    """In LOCAL mode, FileData->TrackData flags whether the file exists."""
    real = tmp_path / "real.mp3"
    real.write_bytes(b"x")
    db.insert_content(ContentRecord(
        file_path=str(real), file_name="real.mp3", file_size=1,
        title="Real", format="mp3", drive="local",
    ))
    browser.load_local()
    tracks = {t.file_path.name: t for t in browser.get_tracks()}
    assert tracks["real.mp3"].file_status == "valid"
    assert tracks["a.mp3"].file_status == "missing"   # /Volumes/USB_A/a.mp3 not present


def test_load_local_without_database_raises(tmp_path):
    browser = LibraryBrowser(database=None, cache_dir=tmp_path / "cache")
    with pytest.raises(ValueError):
        browser.load_local()
