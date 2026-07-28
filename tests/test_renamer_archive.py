"""
Tests for the renamer → Archive edge.

Run from the repo root:
    python3 -m pytest tests/test_renamer_archive.py -v

Verifies that the renamer logs every rename/quarantine to the FableGear
processing log AND relinks fg_content.file_path so the Record Room sees
the new truth. Uses the real FableGearDatabase on a temp DB, but a
minimal rename scenario (no mutagen / app config needed — we call
_archive_rename directly and _rename_one with a pre-built RenameResult
path).
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.schema import DatabaseConfig

sys.path.insert(0, str(REPO_ROOT / "chop_shop"))
from renamer import _archive_rename


@pytest.fixture
def archive(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))


def _add(archive, path_str, title="Track"):
    archive.insert_content(ContentRecord(
        file_path=path_str,
        file_name=Path(path_str).name,
        file_size=100,
        title=title,
    ))


def test_archive_rename_relinks_and_logs(archive, tmp_path):
    old = tmp_path / "old_name.mp3"
    new = tmp_path / "Artist - Title.mp3"
    old.write_bytes(b"audio")
    _add(archive, str(old))

    _archive_rename(archive, old, new, "renamed")

    assert archive.get_content_by_path(str(old)) is None
    rec = archive.get_content_by_path(str(new))
    assert rec is not None
    assert rec.title == "Track"
    assert archive.count_operations("rename") == 1


def test_archive_rename_logs_quarantine(archive, tmp_path):
    old = tmp_path / "junk.mp3"
    new = tmp_path / "No-Name tracks for Tagging" / "junk.mp3"
    old.write_bytes(b"audio")
    _add(archive, str(old))

    _archive_rename(archive, old, new, "quarantined")
    assert archive.count_operations("rename") == 1
    assert archive.get_content_by_path(str(new)) is not None


def test_archive_rename_noop_when_archive_is_none(tmp_path):
    _archive_rename(None, tmp_path / "a.mp3", tmp_path / "b.mp3", "renamed")


def test_archive_rename_tolerates_unknown_path(archive, tmp_path):
    _archive_rename(archive, tmp_path / "not_in_db.mp3", tmp_path / "x.mp3", "renamed")
    assert archive.count_operations("rename") == 1
