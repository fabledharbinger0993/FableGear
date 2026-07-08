import gzip
import json
from pathlib import Path

import checkpoint as checkpoint_mod
import job_dispatcher
from fablegear_database.undo import TransactionHistory
from pipeline_wizard.checkpoint_integration import PipelineCheckpointManager


def test_checkpoint_round_trip_uses_gzip(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint_mod.Path, "home", lambda: tmp_path)
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
