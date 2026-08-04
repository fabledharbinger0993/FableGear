"""
Regression test for importer_database.sync_fablegear_to_rekordbox().

Guards against the bug fixed in this commit: the function built and wrote
DjmdContent rows via _import_track() (which explicitly does not commit —
"caller owns the batch commit") but never called rdb.commit() itself, so
every "synced" track was silently discarded when the write_db() context
manager closed the connection. total_exported still counted as if the
sync succeeded.
"""
import sys
from pathlib import Path

import pytest
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables as rb_tables
from sqlalchemy import create_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rekordbox_meta_support import relaxed_rekordbox_nullability

from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.schema import DatabaseConfig


@pytest.fixture
def fg_db(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))


@pytest.fixture
def rdb_path(tmp_path):
    """Creates a mock unencrypted Rekordbox master.db with correct schema."""
    db_path = tmp_path / "master.db"
    engine = create_engine(f"sqlite:///{db_path}")

    with relaxed_rekordbox_nullability():
        rb_tables.Base.metadata.create_all(engine)
    engine.dispose()

    handle = Rekordbox6Database(path=str(db_path), unlock=False)
    from datetime import datetime
    item = rb_tables.DjmdMenuItems(
        ID="track-menu-id",
        Class="track-class",
        Name="TRACK",
        UUID="track-menu-uuid",
        rb_data_status=0,
        rb_local_data_status=0,
        rb_local_deleted=0,
        rb_local_synced=0,
        usn=0,
        rb_local_usn=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    device = rb_tables.DjmdDevice(
        ID="device-id",
        Name="Mac",
        UUID="device-uuid",
        rb_data_status=0,
        rb_local_data_status=0,
        rb_local_deleted=0,
        rb_local_synced=0,
        usn=0,
        rb_local_usn=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    # Rekordbox6Database.commit() (as opposed to the lower-level
    # .session.commit()) bumps a global USN counter stored in this row.
    # A real master.db always has it (Rekordbox itself maintains it); a
    # bare from-scratch test schema doesn't, so seed it here.
    # pyrekordbox's DateTime column type unconditionally calls
    # value.astimezone() in process_bind_param, even for columns left at
    # their None default — every DateTime column needs an explicit value.
    registry = rb_tables.AgentRegistry(
        registry_id="localUpdateCount",
        int_1=0,
        date_1=datetime.utcnow(),
        date_2=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    handle.session.add(item)
    handle.session.add(device)
    handle.session.add(registry)
    handle.session.commit()
    handle.close()

    return db_path


@pytest.fixture(autouse=True)
def patch_db_connection(rdb_path, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOCAL_DB", rdb_path)
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def force_unencrypted_rekordbox(monkeypatch):
    original_init = Rekordbox6Database.__init__

    def mocked_init(self, *args, **kwargs):
        kwargs["unlock"] = False
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(Rekordbox6Database, "__init__", mocked_init)


def test_sync_writes_are_actually_committed(fg_db, rdb_path, tmp_path):
    from importer_database import sync_fablegear_to_rekordbox

    track_file = tmp_path / "synced_track.mp3"
    track_file.touch()
    fg_db.insert_content(ContentRecord(
        file_path=str(track_file),
        file_name="synced_track.mp3",
        file_size=999,
        duration=180.0,
        title="Committed Track",
        artist="Regression Test",
    ))

    stats = sync_fablegear_to_rekordbox(fg_db)

    assert stats["errors"] == []
    assert stats["total_exported"] == 1

    # Open a *fresh* connection — if sync_fablegear_to_rekordbox never
    # committed, this would come back empty even though total_exported
    # claimed success.
    verify_db = Rekordbox6Database(path=str(rdb_path), unlock=False)
    try:
        row = verify_db.get_content(FolderPath=str(track_file)).first()
        assert row is not None, "track was reported as synced but was never committed to master.db"
        assert row.Title == "Committed Track"
    finally:
        verify_db.close()


def test_sync_skips_tracks_already_in_rekordbox(fg_db, rdb_path, tmp_path):
    from importer_database import sync_fablegear_to_rekordbox

    track_file = tmp_path / "already_there.mp3"
    track_file.touch()

    seed_db = Rekordbox6Database(path=str(rdb_path), unlock=False)
    seed_db.add_content(track_file, Title="Already In Rekordbox")
    seed_db.commit()
    seed_db.close()

    fg_db.insert_content(ContentRecord(
        file_path=str(track_file),
        file_name="already_there.mp3",
        file_size=111,
        title="Already In Rekordbox (fg copy)",
        artist="Regression Test",
    ))

    stats = sync_fablegear_to_rekordbox(fg_db)

    assert stats["errors"] == []
    assert stats["total_exported"] == 0
