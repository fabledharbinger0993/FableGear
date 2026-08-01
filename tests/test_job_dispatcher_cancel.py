"""
Regression tests for job_dispatcher cancel support.

Before this fix, job_dispatcher.py had no cancel/retry path at all (confirmed
by grep for cancel|stop|abort|retry|requeue turning up nothing) -- once
dispatch() submitted a job to the 4-worker pool, nothing could stop it short
of the job's own timeout or killing the whole MCP server process.
"""
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import job_dispatcher


SLEEPER_CLI = """
import sys, time
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0)
print("slept ok")
"""


@pytest.fixture(autouse=True)
def _isolated_dispatcher(tmp_path, monkeypatch):
    """Point job_dispatcher at a throwaway repo dir with a fake cli.py, and
    disable SQLite persistence so these tests only exercise in-memory state."""
    (tmp_path / "cli.py").write_text(SLEEPER_CLI)
    monkeypatch.setattr(job_dispatcher, "_repo_dir", tmp_path)
    monkeypatch.setattr(job_dispatcher, "_persistence_dir", None)
    monkeypatch.setattr(job_dispatcher, "_db_path", None)
    monkeypatch.setattr(job_dispatcher, "_archive_checkpoints_dir", None)


def _wait_for_state(job_id, states, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        rec = job_dispatcher.get_status(job_id)
        last = rec["state"] if rec else None
        if last in states:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"job never reached {states}, last state was {last!r}")


def test_cancel_running_job_terminates_before_its_sleep_finishes():
    import json
    handle = json.loads(job_dispatcher.dispatch("sleeper", ["5"], timeout=30))
    job_id = handle["job_id"]

    _wait_for_state(job_id, {"running"}, timeout=5.0)

    start = time.monotonic()
    result = job_dispatcher.cancel(job_id)
    assert result["ok"] is True

    final = _wait_for_state(job_id, {"cancelled", "done", "error"}, timeout=5.0)
    elapsed = time.monotonic() - start

    assert final["state"] == "cancelled"
    assert elapsed < 4.0, (
        f"cancel() took {elapsed:.1f}s to settle — the 5s sleep likely ran to "
        f"completion instead of being terminated"
    )


def test_cancel_already_finished_job_is_a_no_op():
    import json
    handle = json.loads(job_dispatcher.dispatch("sleeper", ["0"], timeout=30))
    job_id = handle["job_id"]

    _wait_for_state(job_id, {"done", "error"}, timeout=5.0)

    result = job_dispatcher.cancel(job_id)
    assert result["ok"] is False
    assert result["state"] in ("done", "error")


def test_cancel_unknown_job_id_reports_not_found():
    result = job_dispatcher.cancel("does-not-exist")
    assert result["ok"] is False
    assert result["state"] == "unknown"


def test_pre_cancel_db_schema_is_migrated_to_allow_cancelled_state(tmp_path):
    """
    A fablegear_jobs.db created before cancel support existed has a
    `state TEXT ... CHECK (state IN ('pending','running','done','error'))`
    column -- writing 'cancelled' to it must not silently fail (it would,
    caught by _db_upsert_job's broad `except sqlite3.Error`, dropping the
    persisted state without raising).
    """
    import sqlite3

    db_path = tmp_path / "fablegear_jobs.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE jobs (
          job_id TEXT PRIMARY KEY,
          tool TEXT NOT NULL,
          state TEXT NOT NULL CHECK (state IN ('pending','running','done','error')),
          scope TEXT,
          scope_hash TEXT,
          cli_args_json TEXT NOT NULL,
          dispatched_at TEXT NOT NULL,
          started_at TEXT,
          completed_at TEXT,
          duration_seconds REAL,
          exit_code INTEGER,
          result_summary TEXT,
          result_blob_path TEXT,
          checkpoint_path TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT INTO jobs (job_id, tool, state, cli_args_json, dispatched_at, created_at, updated_at)
        VALUES ('old-job', 'legacy_tool', 'done', '[]', '2026-01-01T00:00:00', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db_path))
    try:
        job_dispatcher._migrate_jobs_state_check(conn)
        # Old row must have survived the rebuild.
        row = conn.execute("SELECT job_id, state FROM jobs WHERE job_id='old-job'").fetchone()
        assert row == ("old-job", "done")
        # And the new constraint must now accept 'cancelled'.
        conn.execute(
            "INSERT INTO jobs (job_id, tool, state, cli_args_json, dispatched_at, created_at, updated_at) "
            "VALUES ('new-job', 't', 'cancelled', '[]', '2026-01-01', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()


def test_cancel_queued_job_never_spawns_subprocess(monkeypatch):
    """Saturate the 4-worker pool, dispatch a 5th job, cancel it while it's
    still queued -- it must never call subprocess.Popen at all."""
    import json

    popen_calls = []
    real_popen = job_dispatcher.subprocess.Popen

    def _tracking_popen(cmd, *a, **k):
        popen_calls.append(cmd)
        return real_popen(cmd, *a, **k)

    monkeypatch.setattr(job_dispatcher.subprocess, "Popen", _tracking_popen)

    saturating_ids = []
    for _ in range(job_dispatcher.MAX_WORKERS):
        h = json.loads(job_dispatcher.dispatch("saturator", ["1.5"], timeout=30))
        saturating_ids.append(h["job_id"])

    # Give the pool a moment to actually pick up the 4 saturating jobs.
    for jid in saturating_ids:
        _wait_for_state(jid, {"running", "done", "error", "cancelled"}, timeout=5.0)

    # A unique marker arg distinguishes this job's Popen call from the 4
    # saturating jobs above, which share the same ["1.5"] args.
    queued = json.loads(job_dispatcher.dispatch("queued", ["1.5", "QUEUED_MARKER"], timeout=30))
    queued_id = queued["job_id"]

    # It should still be pending (all 4 workers busy) -- cancel it now.
    result = job_dispatcher.cancel(queued_id)
    assert result["ok"] is True

    final = _wait_for_state(queued_id, {"cancelled", "running", "done", "error"}, timeout=5.0)
    assert final["state"] == "cancelled"
    assert not any("QUEUED_MARKER" in str(call) for call in popen_calls), (
        "the queued job's subprocess was spawned despite being cancelled before it started"
    )
