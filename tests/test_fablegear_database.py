"""
Tests for the database-first Record Room layer (``fablegear_database``).

Run from the repo root:
    pip install pytest && python3 -m pytest tests/test_fablegear_database.py -v

These pin the foundational contract of the lightweight database: the schema
must actually build, and the core CRUD / search / duplicate-detection / stats
operations must round-trip. The two schema bugs that previously left the
database with zero tables (multi-statement ``cursor.execute`` and the
``fg_playlist_song`` constraint ordering) are both covered here — every test
below fails on the unpatched code because ``create_schema`` returns False and
no tables exist.

Every test uses an explicit on-disk DatabaseConfig under a pytest ``tmp_path``
so the real ``~/.fablegear`` database is never touched.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.schema import DatabaseConfig, DatabaseSchema

EXPECTED_TABLES = {
    "fg_content",
    "fg_artist",
    "fg_album",
    "fg_genre",
    "fg_key",
    "fg_label",
    "fg_playlist",
    "fg_playlist_song",
    "fg_metadata",
    "fg_processing_log",
}


@pytest.fixture
def db(tmp_path):
    """A fresh FableGearDatabase backed by an isolated tmp file."""
    config = DatabaseConfig(db_path=tmp_path / "fablegear.db")
    return FableGearDatabase(config)


def _track(path, **overrides):
    base = dict(
        file_path=path,
        file_name=Path(path).name,
        file_size=1024,
        title="Untitled",
        artist="Unknown",
    )
    base.update(overrides)
    return ContentRecord(**base)


# --------------------------------------------------------------------------- #
# Schema creation — the two crasher bugs live here
# --------------------------------------------------------------------------- #

def test_create_schema_builds_every_table(tmp_path):
    """create_schema must succeed and create all tables (incl. fg_playlist_song,
    fg_metadata, fg_processing_log which followed the broken table in DDL order)."""
    import sqlite3

    db_path = tmp_path / "schema.db"
    assert DatabaseSchema.create_schema(db_path) is True

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()

    missing = EXPECTED_TABLES - tables
    assert not missing, f"schema is missing tables: {missing}"


def test_create_schema_builds_indexes(tmp_path):
    """The CREATE INDEX statements bundled into each table string must run too."""
    import sqlite3

    db_path = tmp_path / "schema.db"
    assert DatabaseSchema.create_schema(db_path) is True

    conn = sqlite3.connect(db_path)
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        conn.close()

    for expected in ("idx_file_hash", "idx_acoustic_fp", "idx_playlist_song_content"):
        assert expected in indexes, f"missing index {expected}"


def test_validate_schema_passes_on_fresh_db(tmp_path):
    db_path = tmp_path / "schema.db"
    DatabaseSchema.create_schema(db_path)
    assert DatabaseSchema.validate_schema(db_path) == []


def test_database_initializes_and_records_metadata(db):
    """Constructing FableGearDatabase writes schema_version into fg_metadata —
    this path crashed with 'no such table: fg_metadata' before the fix."""
    assert db._get_metadata("schema_version") == DatabaseSchema.get_schema_version()


# --------------------------------------------------------------------------- #
# Core CRUD
# --------------------------------------------------------------------------- #

def test_insert_and_get_by_path(db):
    rid = db.insert_content(_track("/music/a.mp3", title="Song A", artist="Artist A"))
    assert rid == 1

    got = db.get_content_by_path("/music/a.mp3")
    assert got is not None
    assert got.title == "Song A"
    assert got.artist == "Artist A"
    assert got.file_name == "a.mp3"


def test_get_by_id(db):
    rid = db.insert_content(_track("/music/b.mp3", title="Song B"))
    got = db.get_content_by_id(rid)
    assert got is not None and got.title == "Song B"


def test_get_missing_returns_none(db):
    assert db.get_content_by_path("/nope.mp3") is None
    assert db.get_content_by_id(9999) is None


def test_update_content(db):
    rid = db.insert_content(_track("/music/c.mp3", rating=0))
    assert db.update_content(rid, {"rating": 5, "genre": "House"}) is True

    got = db.get_content_by_id(rid)
    assert got.rating == 5
    assert got.genre == "House"


def test_unique_file_path_constraint(db):
    db.insert_content(_track("/music/dup.mp3"))
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.insert_content(_track("/music/dup.mp3"))


# --------------------------------------------------------------------------- #
# Search, duplicates, statistics
# --------------------------------------------------------------------------- #

def test_search_content_matches_multiple_fields(db):
    db.insert_content(_track("/music/x.mp3", title="Deep Cut", artist="Moodymann"))
    db.insert_content(_track("/music/y.mp3", title="Other", artist="Someone"))

    by_artist = {r.file_name for r in db.search_content("Moodymann")}
    assert by_artist == {"x.mp3"}

    by_title = {r.file_name for r in db.search_content("Deep")}
    assert by_title == {"x.mp3"}


def test_find_duplicates_by_hash(db):
    db.insert_content(_track("/music/a.mp3", file_hash="HASH1"))
    db.insert_content(_track("/music/b.mp3", file_hash="HASH1"))
    db.insert_content(_track("/music/c.mp3", file_hash="UNIQUE"))

    dupes = db.find_duplicates_by_hash()
    assert len(dupes) == 1
    file_hash, ids = dupes[0]
    assert file_hash == "HASH1"
    assert sorted(ids) == [1, 2]


def test_find_duplicates_by_fingerprint(db):
    db.insert_content(_track("/music/a.mp3", acoustic_fingerprint="FP1"))
    db.insert_content(_track("/music/b.mp3", acoustic_fingerprint="FP1"))
    db.insert_content(_track("/music/c.mp3", acoustic_fingerprint="FP2"))

    dupes = db.find_duplicates_by_fingerprint()
    assert len(dupes) == 1
    assert dupes[0][0] == "FP1"
    assert sorted(dupes[0][1]) == [1, 2]


def test_statistics(db):
    db.insert_content(_track("/music/a.mp3", in_rekordbox=True, format="mp3"))
    db.insert_content(_track("/music/b.mp3", in_rekordbox=False, format="mp3"))
    db.insert_content(
        _track("/music/c.flac", is_corrupted=True, format="flac",
               acoustic_fingerprint="FP")
    )

    stats = db.get_statistics()
    assert stats["total_tracks"] == 3
    assert stats["in_rekordbox"] == 1
    assert stats["not_in_rekordbox"] == 2
    assert stats["corrupted"] == 1
    assert stats["fingerprinted"] == 1
    assert stats["format_counts"]["mp3"] == 2


def test_pagination_and_ordering(db):
    for i in range(5):
        db.insert_content(_track(f"/music/{i}.mp3", title=f"T{i}"))

    first_two = db.get_all_content(limit=2, offset=0, order_by="id", ascending=True)
    assert [r.title for r in first_two] == ["T0", "T1"]

    desc = db.get_all_content(limit=1, order_by="id", ascending=False)
    assert desc[0].title == "T4"


# --------------------------------------------------------------------------- #
# Backup / restore
# --------------------------------------------------------------------------- #

def test_backup_and_restore_roundtrip(db):
    db.insert_content(_track("/music/keep.mp3", title="Keep"))
    backup = db.create_backup()
    assert backup.exists()

    db.insert_content(_track("/music/extra.mp3", title="Extra"))
    assert db.get_statistics()["total_tracks"] == 2

    assert db.restore_backup(backup) is True
    assert db.get_statistics()["total_tracks"] == 1
    assert db.get_content_by_path("/music/keep.mp3") is not None
    assert db.get_content_by_path("/music/extra.mp3") is None


# --------------------------------------------------------------------------- #
# Bulk operations
# --------------------------------------------------------------------------- #

def test_bulk_relink_content_is_chunked_and_updates_rows(db):
    """bulk_relink_content should update all rows across multiple chunks."""
    ids = []
    for i in range(5):
        rid = db.insert_content(_track(f"/old/track{i}.mp3", title=f"T{i}"))
        ids.append(rid)

    updates = [(rid, f"/new/track{i}.mp3") for i, rid in enumerate(ids)]
    rows_updated = db.bulk_relink_content(updates, chunk_size=2)

    assert rows_updated == 5
    for i, rid in enumerate(ids):
        rec = db.get_content_by_id(rid)
        assert rec is not None
        assert rec.file_path == f"/new/track{i}.mp3"
        assert rec.processing_status == "relinked"


def test_bulk_log_operations_inserts_all_rows(db):
    """bulk_log_operations should insert all log rows across multiple chunks."""
    ops = [
        {
            "operation_type": "relocate",
            "file_path": f"/music/track{i}.mp3",
            "status": "ok",
            "metadata": {"strategy": "exact"},
        }
        for i in range(7)
    ]
    inserted = db.bulk_log_operations(ops, chunk_size=3)

    assert inserted == 7
    assert db.count_operations("relocate") == 7
