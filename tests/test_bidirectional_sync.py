import sys
import uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables as rb_tables

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import FableGearDatabase, ContentRecord, CueRecord, BeatGridRecord
from fablegear_database.schema import DatabaseConfig
from fablegear_database.rekordbox_sync import RekordboxSyncAdapter
from rekordbox_meta_support import relaxed_rekordbox_nullability


@pytest.fixture
def fg_db(tmp_path):
    """Initializes FableGear database with version 1.1.0 (with cues and beatgrid)."""
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))


@pytest.fixture
def rdb_path(tmp_path):
    """Creates a mock unencrypted Rekordbox master.db with correct schema."""
    db_path = tmp_path / "master.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Relax nullability so the partial menu-item / device inserts below don't
    # trip NOT NULL. The metadata is process-global; relaxed_rekordbox_nullability
    # scopes and restores the mutation so it can't leak into later tests.
    with relaxed_rekordbox_nullability():
        rb_tables.Base.metadata.create_all(engine)
    engine.dispose()
    
    # Add menu item and device required by pyrekordbox add_content
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
        updated_at=datetime.utcnow()
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
        updated_at=datetime.utcnow()
    )
    handle.session.add(item)
    handle.session.add(device)
    handle.session.commit()
    handle.close()

    return db_path


# Helper mock db connection config patch so db_connection uses our test master.db
@pytest.fixture(autouse=True)
def patch_db_connection(rdb_path, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOCAL_DB", rdb_path)
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def force_unencrypted_rekordbox(monkeypatch):
    from pyrekordbox import Rekordbox6Database
    original_init = Rekordbox6Database.__init__
    
    def mocked_init(self, *args, **kwargs):
        kwargs["unlock"] = False
        original_init(self, *args, **kwargs)
        
    monkeypatch.setattr(Rekordbox6Database, "__init__", mocked_init)


def test_sync_new_fg_track_to_rekordbox(fg_db, rdb_path, tmp_path):
    # Touch physical file to satisfy pyrekordbox add_content
    track_file = tmp_path / "fg_track.mp3"
    track_file.touch()

    # Insert a track with cues to FableGear
    track = ContentRecord(
        file_path=str(track_file),
        file_name="fg_track.mp3",
        file_size=12345,
        title="FableGear Track",
        artist="Antigravity",
        color="#ff007f" # Pink
    )
    rid = fg_db.insert_content(track)
    fg_db.bulk_upsert_cues(rid, [
        CueRecord(kind=0, in_msec=1000, comment="Intro Memory"),
        CueRecord(kind=1, slot=0, in_msec=5000, comment="Hot Cue A", color="#00ff00")
    ])
    
    adapter = RekordboxSyncAdapter(fg_db)
    stats = adapter.sync_bidirectional(rdb_path, dry_run=False)
    
    assert stats["tracks_imported_to_rekordbox"] == 1
    assert stats["tracks_imported_to_fablegear"] == 0
    assert stats["cues_synchronized"] == 2
    
    # Check Rekordbox database contains the new track and cues
    rdb = Rekordbox6Database(rdb_path, unlock=False)
    rdb_content = rdb.get_content(FolderPath=str(track_file)).first()
    assert rdb_content is not None
    assert rdb_content.Title == "FableGear Track"
    assert rdb_content.Artist.Name == "Antigravity"
    assert rdb_content.ColorID == "1" # Pink ID is "1"
    
    # Verify cues are populated in Rekordbox
    cues = rdb.get_cue(ContentID=rdb_content.ID).all()
    assert len(cues) == 2
    mem_cue = [c for c in cues if c.Kind == 0][0]
    assert mem_cue.InMsec == 1000
    assert mem_cue.Comment == "Intro Memory"
    
    hot_cue = [c for c in cues if c.Kind == 1][0]
    assert hot_cue.InMsec == 5000
    assert hot_cue.Comment == "Hot Cue A"
    assert hot_cue.Color == 0x00FF00 # Green
    
    rdb.close()


def test_sync_new_rdb_track_to_fablegear(fg_db, rdb_path):
    # Insert a track with cues directly to Rekordbox
    rdb = Rekordbox6Database(rdb_path, unlock=False)
    
    # Create artist
    artist = rdb.add_artist("Antigravity")
    # Add track
    content_id = str(uuid.uuid4())
    content_uuid = str(uuid.uuid4())
    content = rb_tables.DjmdContent(
        ID=content_id,
        UUID=content_uuid,
        FolderPath="/music/rdb_track.mp3",
        Title="Rekordbox Track",
        ArtistID=artist.ID,
        Rating=4,
        Commnt="Import me"
    )
    rdb.session.add(content)
    
    # Add cues
    cue_id1 = str(uuid.uuid4())
    cue1 = rb_tables.DjmdCue(
        ID=cue_id1,
        UUID=str(uuid.uuid4()),
        ContentID=content_id,
        ContentUUID=content_uuid,
        InMsec=2000,
        OutMsec=-1,
        Kind=0, # Memory cue
        Comment="Rdb Memory"
    )
    cue_id2 = str(uuid.uuid4())
    cue2 = rb_tables.DjmdCue(
        ID=cue_id2,
        UUID=str(uuid.uuid4()),
        ContentID=content_id,
        ContentUUID=content_uuid,
        InMsec=6000,
        OutMsec=-1,
        Kind=2, # Hot cue slot 1 (pad B)
        Comment="Rdb Hot Cue B",
        Color=0x0000FF # Blue
    )
    rdb.session.add(cue1)
    rdb.session.add(cue2)
    rdb.session.commit()
    rdb.close()
    
    adapter = RekordboxSyncAdapter(fg_db)
    stats = adapter.sync_bidirectional(rdb_path, dry_run=False)
    
    assert stats["tracks_imported_to_rekordbox"] == 0
    assert stats["tracks_imported_to_fablegear"] == 1
    assert stats["cues_synchronized"] == 2
    
    # Check FableGear database contains the track and cues
    tracks = fg_db.get_content_with_relations()
    assert len(tracks) == 1
    track = tracks[0]
    assert track.file_path == "/music/rdb_track.mp3"
    assert track.title == "Rekordbox Track"
    assert track.artist == "Antigravity"
    assert track.rating == 4
    assert track.comment == "Import me"
    
    assert len(track.cues) == 2
    mem = [c for c in track.cues if c.kind == 0][0]
    assert mem.in_msec == 2000
    assert mem.comment == "Rdb Memory"
    
    hot = [c for c in track.cues if c.kind == 1][0]
    assert hot.slot == 1
    assert hot.in_msec == 6000
    assert hot.comment == "Rdb Hot Cue B"
    assert hot.color.upper() == "#0000FF"


def test_sync_metadata_merging(fg_db, rdb_path):
    # Create same track in both FableGear and Rekordbox but with mismatched metadata
    track = ContentRecord(
        file_path="/music/match.mp3",
        file_name="match.mp3",
        file_size=1000,
        title="FableGear Title",
        artist="FableGear Artist",
        bpm=120.0,
        key="1A",
        rating=5,
        color="#ffaa00", # Orange
        comment="FableGear Comment"
    )
    fg_db.insert_content(track)
    
    rdb = Rekordbox6Database(rdb_path, unlock=False)
    content = rb_tables.DjmdContent(
        ID="match-id",
        UUID="match-uuid",
        FolderPath="/music/match.mp3",
        Title="Rekordbox Title",
        Rating=0, # Unrated (so FableGear rating 5 should win)
        Commnt="" # Empty comment (so FableGear comment should win)
    )
    rdb.session.add(content)
    rdb.session.commit()
    rdb.close()
    
    adapter = RekordboxSyncAdapter(fg_db)
    stats = adapter.sync_bidirectional(rdb_path, dry_run=False)
    
    assert stats["tracks_updated_in_rekordbox"] == 1
    
    # Verify Rekordbox is updated with FableGear's properties
    rdb = Rekordbox6Database(rdb_path, unlock=False)
    content = rdb.get_content(FolderPath="/music/match.mp3").first()
    assert content.Title == "FableGear Title"
    assert content.Artist.Name == "FableGear Artist"
    assert content.BPM == 12000
    assert content.Key.ScaleName == "Am" # 1A maps to Am
    assert content.Rating == 5
    assert content.ColorID == "3" # Orange is "3"
    assert content.Commnt == "FableGear Comment"
    rdb.close()


def test_sync_dry_run_safety(fg_db, rdb_path, tmp_path):
    # Touch physical file to satisfy pyrekordbox add_content
    track_file = tmp_path / "dry_run.mp3"
    track_file.touch()

    # Set up track present in FableGear only
    track = ContentRecord(
        file_path=str(track_file),
        file_name="dry_run.mp3",
        file_size=1000,
        title="Dry Run Title"
    )
    fg_db.insert_content(track)
    
    adapter = RekordboxSyncAdapter(fg_db)
    stats = adapter.sync_bidirectional(rdb_path, dry_run=True)
    
    # Stats should show a simulated import
    assert stats["tracks_imported_to_rekordbox"] == 1
    
    # Check that it was NOT actually written to Rekordbox
    rdb = Rekordbox6Database(rdb_path, unlock=False)
    content = rdb.get_content(FolderPath=str(track_file)).first()
    assert content is None
    rdb.close()

