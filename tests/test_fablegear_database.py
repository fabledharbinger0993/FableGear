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
    "fg_cue",
    "fg_beatgrid",
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


def test_bulk_relink_content_rows_findable_by_new_path(db):
    rid1 = db.insert_content(_track("/music/a.mp3", title="A"))
    rid2 = db.insert_content(_track("/music/b.mp3", title="B"))
    rid3 = db.insert_content(_track("/music/c.mp3", title="C"))

    updated = db.bulk_relink_content(
        [
            (rid1, "/new/a.mp3"),
            (rid2, "/new/b.mp3"),
            (rid3, "/new/c.mp3"),
        ],
        chunk_size=2,
    )
    assert updated == 3
    assert db.get_content_by_path("/new/a.mp3") is not None
    assert db.get_content_by_path("/new/b.mp3") is not None
    assert db.get_content_by_path("/new/c.mp3") is not None


def test_bulk_log_operations_accepts_tuple_form(db):
    inserted = db.bulk_log_operations(
        [
            ("relocate", "/new/a.mp3", "ok", None, {"from": "/old/a.mp3"}),
            ("relocate", "/new/b.mp3", "ok", None, {"from": "/old/b.mp3"}),
            ("relocate", "/new/c.mp3", "ok", None, {"from": "/old/c.mp3"}),
            ("relocate", "/new/d.mp3", "ok", None, {"from": "/old/d.mp3"}),
            ("relocate", "/new/e.mp3", "ok", None, {"from": "/old/e.mp3"}),
        ],
        chunk_size=2,
    )
    assert inserted == 5
    assert db.count_operations("relocate") == 5


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


# --------------------------------------------------------------------------- #
# Performance Metadata & Relations (Cues, Loops, Beatgrids, Color)
# --------------------------------------------------------------------------- #

from fablegear_database.database import CueRecord, BeatGridRecord

def test_color_column_roundtrip(db):
    track = _track("/music/color_track.mp3", title="Color Track")
    track.color = "#ff007f"
    rid = db.insert_content(track)
    
    loaded = db.get_content_by_id(rid)
    assert loaded.color == "#ff007f"

def test_cue_point_roundtrip(db):
    track = _track("/music/cues_track.mp3")
    rid = db.insert_content(track)
    
    cues = [
        CueRecord(kind=0, in_msec=1000, comment="Memory Cue"),
        CueRecord(kind=1, slot=2, in_msec=5000, color="#00f3ff", comment="Hot Cue C"),
        CueRecord(kind=2, in_msec=12000, out_msec=16000, comment="Loop")
    ]
    
    db.bulk_upsert_cues(rid, cues)
    
    loaded_cues = db.get_cues_for_content(rid)
    assert len(loaded_cues) == 3
    assert loaded_cues[0].comment == "Memory Cue"
    assert loaded_cues[1].color == "#00f3ff"
    assert loaded_cues[1].slot == 2
    assert loaded_cues[2].out_msec == 16000

def test_beatgrid_roundtrip(db):
    track = _track("/music/grid_track.mp3")
    rid = db.insert_content(track)
    
    grid = [
        BeatGridRecord(beat_number=1, time_msec=120, bpm=124.0),
        BeatGridRecord(beat_number=2, time_msec=600, bpm=124.0),
        BeatGridRecord(beat_number=3, time_msec=1080, bpm=125.0), # dynamic tempo!
    ]
    
    db.bulk_upsert_beatgrids(rid, grid)
    
    loaded_grid = db.get_beatgrid_for_content(rid)
    assert len(loaded_grid) == 3
    assert loaded_grid[0].time_msec == 120
    assert loaded_grid[2].bpm == 125.0

def test_bulk_relations_load(db):
    rid1 = db.insert_content(_track("/music/bulk1.mp3"))
    rid2 = db.insert_content(_track("/music/bulk2.mp3"))
    
    cues1 = [CueRecord(kind=0, in_msec=1000, comment="Track 1 memory")]
    cues2 = [CueRecord(kind=1, slot=0, in_msec=2000, comment="Track 2 hotcue")]
    
    grid1 = [BeatGridRecord(beat_number=1, time_msec=150, bpm=120.0)]
    grid2 = [BeatGridRecord(beat_number=1, time_msec=200, bpm=128.0)]
    
    db.bulk_upsert_cues(rid1, cues1)
    db.bulk_upsert_cues(rid2, cues2)
    db.bulk_upsert_beatgrids(rid1, grid1)
    db.bulk_upsert_beatgrids(rid2, grid2)
    
    # Batch load with optimized method
    tracks = db.get_content_with_relations([rid1, rid2])
    assert len(tracks) == 2
    
    track_map = {t.id: t for t in tracks}
    assert len(track_map[rid1].cues) == 1
    assert track_map[rid1].cues[0].comment == "Track 1 memory"
    assert len(track_map[rid1].beatgrid) == 1
    assert track_map[rid1].beatgrid[0].bpm == 120.0
    
    assert len(track_map[rid2].cues) == 1
    assert track_map[rid2].cues[0].comment == "Track 2 hotcue"
    assert len(track_map[rid2].beatgrid) == 1
    assert track_map[rid2].beatgrid[0].bpm == 128.0

def test_automatic_migration(tmp_path):
    # 1. Create a database file and initialize with legacy schema (schema v1.0.0 without cue/beatgrid/color)
    db_path = tmp_path / "legacy.db"
    import sqlite3
    conn = sqlite3.connect(db_path)
    # Define legacy schema tables
    conn.execute("""
    CREATE TABLE fg_content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL UNIQUE,
        file_name TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        duration REAL,
        format TEXT,
        bit_rate INTEGER,
        sample_rate INTEGER,
        modified_date TEXT,
        file_hash TEXT,
        acoustic_fingerprint TEXT,
        artist TEXT, album TEXT, title TEXT, bpm REAL, key TEXT, genre TEXT, label TEXT,
        year INTEGER, track_number INTEGER, disc_number INTEGER, comment TEXT, rating INTEGER DEFAULT 0,
        drive TEXT, relative_path TEXT, rekordbox_id INTEGER, rekordbox_playlist_id INTEGER,
        in_rekordbox BOOLEAN DEFAULT 0, last_scanned TEXT, fingerprint_quality INTEGER DEFAULT 0,
        is_corrupted BOOLEAN DEFAULT 0, processing_status TEXT DEFAULT 'unprocessed',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    for table in ["fg_artist", "fg_album", "fg_genre", "fg_key", "fg_label", "fg_playlist", "fg_playlist_song"]:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY AUTOINCREMENT);")
    conn.execute("CREATE TABLE fg_metadata (key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("INSERT INTO fg_metadata (key, value) VALUES ('schema_version', '1.0.0');")
    conn.commit()
    conn.close()
    
    # 2. Open it with FableGearDatabase - validation should fail initially, trigger upgrade_schema, and pass re-validation
    config = DatabaseConfig(db_path=db_path)
    db = FableGearDatabase(config)
    
    # 3. Verify color column and tables exist now
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(fg_content)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "color" in cols
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "fg_cue" in tables
        assert "fg_beatgrid" in tables
        
        cursor.execute("SELECT value FROM fg_metadata WHERE key='schema_version'")
        assert cursor.fetchone()[0] == "1.1.0"

def test_export_track_anlz(tmp_path):
    from fablegear_database.exporter import PioneerExporter
    from chop_shop.anlz_reader import parse_anlz_file
    
    # 1. Initialize DB and insert a content record with detailed cues/beatgrid
    config = DatabaseConfig(db_path=tmp_path / "test_export.db")
    db = FableGearDatabase(config)
    
    track = ContentRecord(
        file_path="/music/export_track.wav",
        file_name="export_track.wav",
        file_size=1024 * 1024 * 10,
        color="#FF007F"
    )
    
    cues = [
        # Memory Cue
        CueRecord(kind=0, in_msec=1000, comment="Intro Cue"),
        # Memory Loop
        CueRecord(kind=3, in_msec=5000, out_msec=15000, comment="Active Loop Section"),
        # Hot Cue
        CueRecord(kind=1, slot=0, in_msec=2000, comment="Vocal Drop", color="#00FF00"),
        # Hot Loop
        CueRecord(kind=2, slot=1, in_msec=8000, out_msec=12000, comment="Loop A", color="#0000FF")
    ]
    
    grid = [
        BeatGridRecord(beat_number=1, bpm=120.0, time_msec=0),
        BeatGridRecord(beat_number=2, bpm=120.0, time_msec=500),
        BeatGridRecord(beat_number=3, bpm=124.0, time_msec=1000)
    ]
    
    # Insert track to obtain an ID
    rid = db.insert_content(track)
    db.bulk_upsert_cues(rid, cues)
    db.bulk_upsert_beatgrids(rid, grid)
    
    # Fetch back to make sure relations are populated
    ret_tracks = db.get_content_with_relations([rid])
    assert len(ret_tracks) == 1
    
    # 2. Export ANLZ files
    exporter = PioneerExporter(db)
    target_root = tmp_path / "usb_export"
    success = exporter.export_track_anlz(content_id=1, target_root=target_root, relative_audio_path="Contents/export_track.wav")
    assert success is True
    
    # 3. Check files exist at expected paths
    # Content ID 1 -> sub_dir1: P000, sub_dir2: 00000001
    dat_path = target_root / "PIONEER" / "USBANLZ" / "P000" / "00000001" / "ANLZ0000.DAT"
    ext_path = target_root / "PIONEER" / "USBANLZ" / "P000" / "00000001" / "ANLZ0000.EXT"
    
    assert dat_path.is_file()
    assert ext_path.is_file()
    
    # 4. Verify roundtrip parsing of DAT file
    dat_report = parse_anlz_file(dat_path)
    assert dat_report.exists is True
    assert dat_report.ppth_path == "Contents/export_track.wav"
    
    # Verify beatgrid was parsed correctly
    assert len(dat_report.beat_grid) == 3
    assert dat_report.beat_grid[0].beat_no == 1
    assert dat_report.beat_grid[0].tempo_bpm == 120.0
    assert dat_report.beat_grid[0].time_ms == 0
    assert dat_report.beat_grid[2].beat_no == 3
    assert dat_report.beat_grid[2].tempo_bpm == 124.0
    assert dat_report.beat_grid[2].time_ms == 1000
    
    # 5. Verify roundtrip parsing of EXT file (using pyrekordbox directly since anlz_reader mainly decodes DAT tags)
    from pyrekordbox import AnlzFile
    ext_file = AnlzFile.parse_file(ext_path)
    assert "PCO2" in ext_file.tag_types
    
    # Retrieve PCO2 tags (we have memory cues PCO2 and hotcues PCO2)
    pco2_tags = ext_file.getall_tags("PCO2")
    assert len(pco2_tags) == 2
    
    # Check memory cues tag
    mc_tag = [t for t in pco2_tags if t.content.type == "memory"][0]
    assert mc_tag.content.count == 2
    mc_entries = mc_tag.content.entries
    assert mc_entries[0].time == 1000
    assert mc_entries[0].loop_time == 0xFFFFFFFF
    assert mc_entries[0].comment == "Intro Cue"
    
    assert mc_entries[1].time == 5000
    assert mc_entries[1].loop_time == 15000
    assert mc_entries[1].comment == "Active Loop Section"
    
    # Check hotcues tag
    hc_tag = [t for t in pco2_tags if t.content.type == "hotcue"][0]
    assert hc_tag.content.count == 2
    hc_entries = hc_tag.content.entries
    assert hc_entries[0].hot_cue == 1 # slot 0 -> hot_cue 1
    assert hc_entries[0].time == 2000
    assert hc_entries[0].loop_time == 0xFFFFFFFF
    assert hc_entries[0].comment == "Vocal Drop"
    assert hc_entries[0].color_red == 0
    assert hc_entries[0].color_green == 255
    assert hc_entries[0].color_blue == 0
    
    assert hc_entries[1].hot_cue == 2 # slot 1 -> hot_cue 2
    assert hc_entries[1].time == 8000
    assert hc_entries[1].loop_time == 12000
    assert hc_entries[1].comment == "Loop A"
    assert hc_entries[1].color_red == 0
    assert hc_entries[1].color_green == 0
    assert hc_entries[1].color_blue == 255

