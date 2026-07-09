import gzip
import json
from pathlib import Path

import checkpoint as checkpoint_mod
import job_dispatcher
from fablegear_database.undo import TransactionHistory
from pipeline_wizard.checkpoint_integration import PipelineCheckpointManager


def test_checkpoint_round_trip_uses_gzip(tmp_path, monkeypatch):
    # _CHECKPOINT_BASE is resolved at import time, so patching Path.home is
    # ineffective — patch the constant itself or the test writes into the
    # user's real ~/.fablegear/checkpoints.
    monkeypatch.setattr(checkpoint_mod, "_CHECKPOINT_BASE", tmp_path / "checkpoints")
    ck = checkpoint_mod.Checkpoint("duplicates", [Path("/music")], {"match_mode": "all"})

    ck.save({"completed": 12, "total": 34, "fp_map": {"a": 1}})

    assert ck.path.suffix == ".gz"
    assert ck.path.exists()
    with gzip.open(ck.path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    assert data["completed"] == 12
    assert ck.load()["total"] == 34


def test_transaction_history_persists_compressed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    history = TransactionHistory(database=object(), max_history=10)
    history.record_transaction(
        operation_type="rename",
        description="Rename a track",
        affected_records=[1],
        before_state={1: {"file_path": "/old.mp3"}},
        after_state={1: {"file_path": "/new.mp3"}},
    )

    assert history._history_file.suffix == ".gz"
    assert history._history_file.exists()
    with gzip.open(history._history_file, "rt", encoding="utf-8") as f:
        data = json.load(f)
    assert data[0]["operation_type"] == "rename"


class _StubDatabase:
    """Records update_content calls so undo restoration can be asserted."""

    def __init__(self):
        self.updates = []

    def update_content(self, record_id, state):
        self.updates.append((record_id, state))
        return True


def test_undo_restores_records_after_reload_from_disk(tmp_path, monkeypatch):
    """Undo must still work on history reloaded from disk — JSON coerces the
    int record-id keys of before_state to strings, which used to make undo a
    silent no-op that still reported success."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db = _StubDatabase()
    history = TransactionHistory(database=db, max_history=10)
    tx_id = history.record_transaction(
        operation_type="rename",
        description="Rename a track",
        affected_records=[1],
        before_state={1: {"file_path": "/old.mp3"}},
        after_state={1: {"file_path": "/new.mp3"}},
    )

    # Fresh instance = reload from the persisted .json.gz
    reloaded = TransactionHistory(database=db, max_history=10)
    assert reloaded.undo_transaction(tx_id) is True
    assert db.updates == [(1, {"file_path": "/old.mp3"})]


def test_pipeline_checkpoint_round_trip_uses_gzip(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    mgr = PipelineCheckpointManager("weekly_maintenance", {"tools": ["audit", "import"]})
    assert mgr.save_pipeline_checkpoint(["audit"], 0, [{"tool": "audit"}]) is True
    assert mgr._checkpoint_path.suffix == ".gz"
    loaded = mgr.load_pipeline_checkpoint()
    assert loaded is not None
    assert loaded["completed_tools"] == ["audit"]


def test_job_dispatcher_finds_compressed_checkpoint(tmp_path, monkeypatch):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    monkeypatch.setattr(job_dispatcher, "_archive_checkpoints_dir", checkpoints)
    monkeypatch.setattr(job_dispatcher, "_persistence_dir", None)

    payload = {
        "job_id": "job123",
        "tool": "tag_tracks",
        "state": "done",
        "scope": "/music",
        "scope_hash": job_dispatcher._scope_hash("/music"),
        "completed_at": "2026-07-08T11:00:00+00:00",
        "result_summary": "ok",
    }
    path = checkpoints / f"tag_tracks_{job_dispatcher._scope_hash('/music')}_20260708T110000_job123.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)

    found = job_dispatcher.find_checkpoint("tag_tracks", "/music")
    assert found is not None
    assert found["job_id"] == "job123"
