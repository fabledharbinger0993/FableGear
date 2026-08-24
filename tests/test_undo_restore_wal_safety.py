"""
Regression guard: restoring a savepoint must not be silently undone by a
leftover WAL from the state being restored away from.

The defect this guards: master.db is WAL-mode. /api/undo/savepoint/restore
replaced only the main .db file with the savepoint's, leaving whatever
-wal/-shm sidecars were already sitting at the live path untouched. SQLite
does not detect that the leftover WAL predates the restored file — it
replays it on the next open. Reproduced directly: restore a savepoint of an
OLD state, leave the live -wal (holding the "bad" data the restore exists to
undo) in place, open the "restored" database — the bad data reappears
immediately. No error. No warning. The restore silently did nothing.

The route now ends every restore in exactly one of two states for each
sidecar: replaced with the matching sidecar from that savepoint, or removed
entirely — never left over from the pre-restore state.

Runs against the real Flask app on a sandboxed HOME, same pattern as
tests/test_undo_path_safety.py, but with a real WAL-mode sqlite database
instead of an empty placeholder file, since the defect is specifically about
WAL sidecar handling.
"""
import json
<<<<<<< HEAD
=======
import os
>>>>>>> recovered-pr152-check
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = "127.0.0.1"


@pytest.fixture
<<<<<<< HEAD
def client(tmp_path, monkeypatch):
=======
def client(tmp_path):
>>>>>>> recovered-pr152-check
    home = tmp_path / "home"
    music = tmp_path / "music"
    backups = tmp_path / "backups"
    for d in (home, music, backups):
        d.mkdir()
    device_db = home / "master.db"
    local_db = home / "fake_local.db"
    local_db.touch()

    conn = sqlite3.connect(device_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('old-state')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()
    assert not (home / "master.db-wal").exists(), "fixture must start clean"

    cfg_dir = home / ".fablegear"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "local_db": str(local_db),
        "device_db": str(device_db),
        "music_root": str(music),
        "backup_dir": str(backups),
        "mobile_token": "test-token",
    }))

<<<<<<< HEAD
    # monkeypatch.setenv, not a raw os.environ assignment: it auto-restores
    # HOME after this test regardless of outcome, so a redirected HOME can't
    # leak into whatever test runs next in the same process -- which is
    # exactly what broke update_checker's real `git` subprocess calls
    # elsewhere in the suite (see tests/test_network_boundary.py).
    monkeypatch.setenv("HOME", str(home))
=======
    os.environ["HOME"] = str(home)
>>>>>>> recovered-pr152-check
    for mod in list(sys.modules):
        if mod in ("app", "user_config", "config", "helpers", "db_connection",
                   "routes_mobile", "routes_tools", "routes_player",
                   "routes_rekordbox", "routes_undo"):
            del sys.modules[mod]
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

    from app import app
    app.config["TESTING"] = True
    return app.test_client(), device_db


def _post(client, path, body):
    return client.post(path, json=body, environ_overrides={"REMOTE_ADDR": LOOPBACK})


def test_restore_is_not_defeated_by_a_leftover_wal(client, tmp_path, monkeypatch):
    test_client, device_db = client
    import db_connection

    # This test's outcome must not depend on whether Rekordbox happens to
    # be open on whatever machine runs the suite.
    monkeypatch.setattr(db_connection, 'rekordbox_is_running', lambda: False)

    # Take a savepoint of the clean "old" state.
    savepoint_path = db_connection._backup_db(device_db)

    # Make the "bad write" the restore exists to undo, and leave it
    # uncheckpointed on disk -- an unclean exit (crash / force-quit), not a
    # normal close.
    conn = sqlite3.connect(device_db)
    conn.execute("INSERT INTO t VALUES ('bad-write-still-in-wal')")
    conn.commit()
    del conn
    assert (device_db.parent / (device_db.name + "-wal")).exists(), "fixture must leave a live WAL"

    resp = _post(test_client, "/api/undo/savepoint/restore", {"path": str(savepoint_path)})
    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json() or {}).get("ok") is True

    # The moment of truth: does the bad write survive the restore?
    conn = sqlite3.connect(device_db)
    rows = [r[0] for r in conn.execute("SELECT k FROM t")]
    conn.close()

    assert "bad-write-still-in-wal" not in rows, (
        "the restore was silently defeated by a leftover WAL replaying the "
        f"data it was supposed to undo. rows after restore: {rows}"
    )
    assert rows == ["old-state"]


def test_restore_replaces_sidecars_when_the_savepoint_has_its_own(client, tmp_path, monkeypatch):
    """If the savepoint itself carries -wal/-shm (a savepoint taken while ITS
    OWN source had uncheckpointed data), restoring must bring that data back
    too, not silently drop it."""
    test_client, device_db = client
    import db_connection
    monkeypatch.setattr(db_connection, 'rekordbox_is_running', lambda: False)

    # Give the "old" state its own uncheckpointed addition before the
    # savepoint is taken, so the savepoint's backup includes a -wal.
    conn = sqlite3.connect(device_db)
    conn.execute("INSERT INTO t VALUES ('old-state-2-in-wal-at-savepoint-time')")
    conn.commit()
    del conn  # unclean -- so _backup_db must capture this via the sidecar

    savepoint_path = db_connection._backup_db(device_db)
    assert Path(str(savepoint_path) + "-wal").exists(), "savepoint must have captured the sidecar"

    # Now put the live db back into a clean, fully-checkpointed state with
    # DIFFERENT content, simulating time passing / more work happening.
    conn = sqlite3.connect(device_db)
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES ('later-unrelated-state')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()

    resp = _post(test_client, "/api/undo/savepoint/restore", {"path": str(savepoint_path)})
    assert resp.status_code == 200, resp.get_json()

    conn = sqlite3.connect(device_db)
    rows = sorted(r[0] for r in conn.execute("SELECT k FROM t"))
    conn.close()
    assert rows == sorted(["old-state", "old-state-2-in-wal-at-savepoint-time"]), (
        f"restoring a savepoint that itself had WAL-only data must bring that "
        f"data back, not drop it. rows after restore: {rows}"
    )
