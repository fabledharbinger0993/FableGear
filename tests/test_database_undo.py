import pytest
from pathlib import Path
from fablegear_database import DatabaseConfig, FableGearDatabase, ContentRecord
from fablegear_database.undo import DatabaseUndoManager

@pytest.fixture
def temp_db(tmp_path):
    config = DatabaseConfig(db_path=tmp_path / "fablegear.db")
    db = FableGearDatabase(config)
    # Patch transaction history path for test isolation
    undo_mgr = DatabaseUndoManager(db)
    undo_mgr.history._history_file = tmp_path / "transaction_history.json.gz"
    undo_mgr.history._legacy_history_file = tmp_path / "transaction_history.json"
    undo_mgr.history.clear_history()
    return db, undo_mgr

def test_record_and_undo_update(temp_db):
    db, undo_mgr = temp_db
    
    # 1. Create a track
    track = ContentRecord(
        file_path="/music/track1.mp3",
        file_name="track1.mp3",
        title="Original Title",
        artist="Original Artist",
        bpm=120.0,
        key="4A",
    )
    db.bulk_upsert_content([track])
    
    # Verify insert
    rec = db.get_content_by_path("/music/track1.mp3")
    assert rec is not None
    assert rec.title == "Original Title"
    assert rec.id is not None
    
    # Save original state for undo
    before_state = rec.to_dict()
    
    # 2. Update track
    updated = ContentRecord(
        id=rec.id,
        file_path="/music/track1.mp3",
        file_name="track1.mp3",
        title="Updated Title",
        artist="Original Artist",
        bpm=125.0,
        key="5A",
    )
    db.bulk_upsert_content([updated])
    
    # Verify update
    rec_updated = db.get_content_by_id(rec.id)
    assert rec_updated.title == "Updated Title"
    assert rec_updated.bpm == 125.0
    
    after_state = rec_updated.to_dict()
    
    # 3. Record transaction
    tx_id = undo_mgr.record_update(
        record_id=rec.id,
        before_state=before_state,
        after_state=after_state,
        description="Update track details test",
    )
    
    # Verify transaction logged
    assert undo_mgr.can_undo()
    assert undo_mgr.get_undo_count() == 1
    
    recent = undo_mgr.history.get_recent_transactions()
    assert len(recent) == 1
    assert recent[0].transaction_id == tx_id
    
    # 4. Perform Undo
    success = undo_mgr.undo_last()
    assert success
    
    # Verify rollback
    rec_rolled_back = db.get_content_by_id(rec.id)
    assert rec_rolled_back.title == "Original Title"
    assert rec_rolled_back.bpm == 120.0
    assert rec_rolled_back.key == "4A"

def test_record_import_stats(temp_db):
    db, undo_mgr = temp_db

    tx_id = undo_mgr.record_import(
        imported_count=5,
        root_paths=[Path("/drives/usb1/music")],
    )

    assert undo_mgr.can_undo()
    recent = undo_mgr.history.get_recent_transactions()
    assert len(recent) == 1
    assert recent[0].operation_type == "import"
    assert recent[0].metadata["imported_count"] == 5


def test_undo_import_reports_failure_not_vacuous_success(temp_db):
    """
    record_import() logs affected_records=[] (no per-record before/after
    state is captured for imports). undo_transaction() must report this
    honestly as a failure -- previously it fell through and returned True,
    so /api/undo/database/revert told the user their revert succeeded
    despite restoring nothing.
    """
    db, undo_mgr = temp_db

    tx_id = undo_mgr.record_import(
        imported_count=3,
        root_paths=[Path("/drives/usb1/music")],
    )

    success = undo_mgr.history.undo_transaction(tx_id)
    assert success is False
