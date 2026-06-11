"""
Tests for the app-wide network boundary (app.py _enforce_network_boundary)
and the dead-file scanner's fail-loud behavior on unreadable databases.

Run from the repo root:
    pip install pytest && python3 -m pytest tests/ -v

The boundary tests boot the real Flask app against a throwaway
~/.fablegear/config.json (HOME is redirected to a tmp dir before any
FableGear module is imported), then simulate loopback vs LAN clients
via the test client. This pins the security contract added after the
June 2026 audit:

    loopback           -> full access
    /api/mobile/*      -> Bearer auth (FableGo)
    GET /static/*      -> public
    everything else    -> 403 off-box unless Bearer token or
                          allow_lan_ui opt-in
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TEST_TOKEN = "test-token-fablegear"
LOOPBACK = "127.0.0.1"
LAN = "192.168.1.50"


@pytest.fixture(scope="session")
def flask_app(tmp_path_factory):
    """Boot the real app once against a sandboxed HOME + minimal config."""
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
        "mobile_token": TEST_TOKEN,
    }))

    # HOME must be redirected BEFORE user_config computes CONFIG_FILE.
    os.environ["HOME"] = str(home)
    for mod in list(sys.modules):
        if mod in ("app", "user_config", "config", "helpers", "routes_mobile",
                   "routes_tools", "routes_player", "routes_rekordbox"):
            del sys.modules[mod]
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

    from app import app  # noqa: PLC0415
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


def _hit(client, path, addr, method="GET", token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    fn = getattr(client, method.lower())
    return fn(path, environ_overrides={"REMOTE_ADDR": addr}, headers=headers)


# ── Loopback: unchanged behavior ─────────────────────────────────────────────

def test_loopback_ui_page_allowed(client):
    assert _hit(client, "/", LOOPBACK).status_code in (200, 302)


def test_loopback_tools_route_reaches_handler(client):
    # 404 "No active scan" proves routing happened (handler-level response,
    # not a boundary block).
    r = _hit(client, "/api/cancel", LOOPBACK, method="POST")
    assert r.status_code in (200, 400, 404, 409, 500)
    assert r.status_code != 403


# ── LAN: destructive and UI routes blocked ───────────────────────────────────

@pytest.mark.parametrize("path,method", [
    ("/api/run/prune", "GET"),
    ("/api/run/rename", "GET"),
    ("/api/cancel", "POST"),
    ("/", "GET"),
])
def test_lan_blocked_without_token(client, path, method):
    assert _hit(client, path, LAN, method=method).status_code == 403


def test_lan_blocked_with_wrong_token(client):
    r = _hit(client, "/api/cancel", LAN, method="POST", token="wrong")
    assert r.status_code == 403


# ── LAN: deliberate access paths still work ──────────────────────────────────

def test_lan_mobile_ping_open(client):
    assert _hit(client, "/api/mobile/ping", LAN).status_code == 200


def test_lan_mobile_requires_bearer(client):
    assert _hit(client, "/api/mobile/folders", LAN).status_code == 401


def test_lan_mobile_with_token(client):
    assert _hit(client, "/api/mobile/jobs", LAN, token=TEST_TOKEN).status_code == 200


def test_lan_static_assets_public(client):
    assert _hit(client, "/static/fablegear.css", LAN).status_code == 200


def test_lan_bearer_escape_hatch_reaches_handler(client):
    r = _hit(client, "/api/cancel", LAN, method="POST", token=TEST_TOKEN)
    assert r.status_code != 403   # boundary passed; handler decides the rest


# ── Dead-file scanner: fail loud on unreadable DB ────────────────────────────

def test_dead_file_scan_raises_on_unreadable_db(flask_app, tmp_path):
    """Regression for the shadowed-fix consolidation: an existing-but-corrupt
    DB must abort the scan (RuntimeError), never warn-and-continue, because a
    silently shrunken known-paths set misclassifies tracked files as dead."""
    from dead_file_scanner import scan_dead_files  # noqa: PLC0415

    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"this is not a rekordbox database")
    music = tmp_path / "music"
    music.mkdir()
    (music / "track.aiff").write_bytes(b"\x00" * 64)

    with pytest.raises(RuntimeError, match="could not read DB"):
        scan_dead_files([music], db_paths=[corrupt_db])


def test_dead_file_scan_skips_missing_db(flask_app, tmp_path):
    """A db path that doesn't exist is skipped (debug log), not fatal —
    only existing-but-unreadable databases abort."""
    from dead_file_scanner import scan_dead_files  # noqa: PLC0415

    music = tmp_path / "music"
    music.mkdir()
    (music / "track.aiff").write_bytes(b"\x00" * 64)

    result = scan_dead_files([music], db_paths=[tmp_path / "nonexistent.db"])
    assert result is not None
