"""
Security regression tests for routes_undo.py path handling (audit H1/H2).

H1: /api/undo/trash/restore built ~/.Trash/<folder> from a request-supplied
    'folder' guarded only by a startswith() check — no basename() — so
    "FableGear_Pruned_/../../.." could escape ~/.Trash and have its files
    moved out. The sibling read endpoint already sanitized with basename();
    the MOVE endpoint must too.

H2: /api/undo/savepoint/restore accepted any 'path' whose basename started
    with "master.backup_" and copied it over the live DB — an arbitrary file
    anywhere named that way could clobber master.db. It must be contained
    within a known backup/savepoint directory.

These run against the real Flask app on a sandboxed HOME (same pattern as
tests/test_network_boundary.py), over loopback so the handler — not the
network boundary — is what accepts or rejects.
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = "127.0.0.1"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    music = tmp_path_factory.mktemp("music")
    backups = tmp_path_factory.mktemp("backups")
    local_db = home / "fake_local.db"
    device_db = home / "fake_device.db"
    local_db.touch()
    device_db.touch()

    cfg_dir = home / ".fablegear"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "local_db": str(local_db),
        "device_db": str(device_db),
        "music_root": str(music),
        "backup_dir": str(backups),
        "mobile_token": "test-token",
    }))

    os.environ["HOME"] = str(home)
    for mod in list(sys.modules):
        if mod in ("app", "user_config", "config", "helpers", "routes_mobile",
                   "routes_tools", "routes_player", "routes_rekordbox", "routes_undo"):
            del sys.modules[mod]
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

    from app import app  # noqa: PLC0415
    app.config["TESTING"] = True
    return app.test_client()


def _post(client, path, body):
    return client.post(
        path, json=body, environ_overrides={"REMOTE_ADDR": LOOPBACK}
    )


# ── H1: trash restore traversal ──────────────────────────────────────────────

@pytest.mark.parametrize("folder", [
    "FableGear_Pruned_/../../../../etc",
    "FableGear_Pruned_x/../../../..",
    "../../../etc",
    "FableGear_Pruned_../secrets",
])
def test_trash_restore_rejects_traversal(client, folder):
    r = _post(client, "/api/undo/trash/restore",
              {"folder": folder, "destination": "/tmp"})
    # Must be rejected as invalid/not-found — never a 200 that moved files.
    assert r.status_code in (400, 404)
    assert not (r.get_json() or {}).get("ok")


def test_trash_restore_still_rejects_plain_bad_name(client):
    r = _post(client, "/api/undo/trash/restore",
              {"folder": "not_a_fablegear_folder", "destination": "/tmp"})
    assert r.status_code == 400


# ── H2: savepoint restore containment ────────────────────────────────────────

def test_savepoint_restore_rejects_out_of_tree_path(client, tmp_path):
    # A file named like a savepoint but living OUTSIDE any backup dir.
    rogue = tmp_path / "master.backup_20260101_000000.db"
    rogue.write_bytes(b"rogue")
    r = _post(client, "/api/undo/savepoint/restore", {"path": str(rogue)})
    assert r.status_code == 400
    assert "Invalid savepoint" in (r.get_json() or {}).get("error", "")


def test_savepoint_restore_rejects_wrong_basename(client):
    r = _post(client, "/api/undo/savepoint/restore", {"path": "/etc/passwd"})
    assert r.status_code == 400
