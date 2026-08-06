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

    from app import app
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


def _hit(client, path, addr, method="GET", token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    fn = getattr(client, method.lower())
    return fn(path, environ_overrides={"REMOTE_ADDR": addr}, headers=headers)


def _setup_state_file() -> Path:
    return Path(os.environ["HOME"]) / ".fablegear" / "fablegear-state.json"


def _write_setup_state(*, setup_complete, db_read=None, db_write=None, drive_scan=False):
    _setup_state_file().write_text(json.dumps({
        "setup_complete": setup_complete,
        "db_read": db_read,
        "db_write": db_write,
        "drive_scan": drive_scan,
    }))


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


def test_lan_bearer_does_not_bypass_non_mobile_boundary(client):
    r = _hit(client, "/api/cancel", LAN, method="POST", token=TEST_TOKEN)
    assert r.status_code == 403


def test_root_redirects_to_onboarding_when_setup_incomplete(client):
    _write_setup_state(setup_complete=False, db_read=None, db_write=None)
    r = _hit(client, "/", LOOPBACK)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/onboarding")


def test_onboarding_redirects_home_when_setup_complete_unless_reconfigure(client):
    _write_setup_state(setup_complete=True, db_read=True, db_write=True)

    normal = _hit(client, "/onboarding", LOOPBACK)
    assert normal.status_code == 302
    assert normal.headers["Location"].endswith("/")

    reconfigure = _hit(client, "/onboarding?reconfigure=1", LOOPBACK)
    assert reconfigure.status_code == 200


def test_setup_status_includes_gate_reason_for_incomplete_state(client):
    _write_setup_state(setup_complete=False, db_read=False, db_write=False)
    data = _hit(client, "/api/setup-status", LOOPBACK).get_json()
    assert data["setup_complete"] is False
    assert data["gate_reason"] == "setup_incomplete"


def test_setup_gate_logs_and_falls_back_when_config_check_fails(client, monkeypatch, caplog):
    import user_config as _user_config

    _write_setup_state(setup_complete=True, db_read=True, db_write=True)

    def _boom():
        raise RuntimeError("config check exploded")

    monkeypatch.setattr(_user_config, "config_exists", _boom)

    status = _hit(client, "/api/setup-status", LOOPBACK).get_json()
    assert status["setup_complete"] is False
    assert status["gate_reason"] == "config_check_failed"

    with caplog.at_level("ERROR"):
        r = _hit(client, "/", LOOPBACK)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/onboarding")
    assert any("Setup gate check failed while reading config state" in rec.getMessage() for rec in caplog.records)


def test_onboarding_save_config_defaults_backup_to_archive_savepoints(client):
    music_root = "/Volumes/MainLibrary/Music Library"
    r = client.post("/api/onboarding/save-config", json={
        "local_db": "/tmp/local.db",
        "device_db": "/tmp/device.db",
        "music_root": music_root,
        "db_read": True,
        "db_write": False,
    })
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    cfg = json.loads((Path(os.environ["HOME"]) / ".fablegear" / "config.json").read_text())
    assert cfg["backup_dir"] == "/Volumes/MainLibrary/FableGear Archive/Savepoints"
    assert cfg["snapshot_cadence"] == "monthly"
    assert cfg["snapshot_include_master_db"] is False


def test_onboarding_save_config_persists_and_normalizes_snapshot_options(client):
    music_root = "/Volumes/MainLibrary/Music Library"
    r = client.post("/api/onboarding/save-config", json={
        "local_db": "/tmp/local.db",
        "device_db": "/tmp/device.db",
        "music_root": music_root,
        "db_read": True,
        "db_write": False,
        "snapshot_cadence": "  Weekly  ",  # mixed whitespace — should normalize to "weekly"
        "snapshot_include_master_db": "yes",  # string bool — should coerce to True
    })
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    cfg = json.loads((Path(os.environ["HOME"]) / ".fablegear" / "config.json").read_text())
    assert cfg["backup_dir"] == "/Volumes/MainLibrary/FableGear Archive/Savepoints"
    assert cfg["snapshot_cadence"] == "weekly"
    assert cfg["snapshot_include_master_db"] is True


def test_api_config_uses_archive_reports_path(client):
    data = _hit(client, "/api/config", LOOPBACK).get_json()
    assert data["reports"].endswith("/FableGear Archive/Reports")
    assert data["snapshot_cadence"] == "monthly"
    assert data["snapshot_include_master_db"] is False


def test_api_settings_persists_snapshot_options(client):
    r = client.post("/api/settings", json={
        "archive_mode": "custom",
        "custom_archive_dir": "/Volumes/MainLibrary/Archives",
        "snapshot_cadence": "weekly",
        "snapshot_include_master_db": True,
        "excluded_dirs": [],
        "acoustid_api_key": "",
    })
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    cfg = json.loads((Path(os.environ["HOME"]) / ".fablegear" / "config.json").read_text())
    assert cfg["archive_mode"] == "custom"
    assert cfg["custom_archive_dir"] == "/Volumes/MainLibrary/Archives"
    assert cfg["snapshot_cadence"] == "weekly"
    assert cfg["snapshot_include_master_db"] is True


def test_api_settings_preserves_acoustid_when_omitted(client):
    cfg_path = Path(os.environ["HOME"]) / ".fablegear" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["acoustid_api_key"] = "existing-key-123"
    cfg_path.write_text(json.dumps(cfg))

    r = client.post("/api/settings", json={
        "archive_mode": "custom",
        "custom_archive_dir": "/Volumes/MainLibrary/Archives",
        "snapshot_cadence": "weekly",
        "snapshot_include_master_db": True,
        "excluded_dirs": [],
        # acoustid_api_key intentionally omitted: should preserve existing value.
    })
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    cfg_after = json.loads(cfg_path.read_text())
    assert cfg_after["acoustid_api_key"] == "existing-key-123"


@pytest.mark.parametrize(
    "input_cadence, expected_cadence",
    [
        ("Monthly", "monthly"),       # case normalization
        ("monthly ", "monthly"),      # trimming whitespace
        ("bi-weekly", "biweekly"),    # hyphen stripped → "biweekly"
        ("never", "monthly"),         # invalid falls back to default
    ],
)
def test_api_settings_normalizes_snapshot_cadence(client, input_cadence, expected_cadence):
    r = client.post(
        "/api/settings",
        json={
            "archive_mode": "custom",
            "custom_archive_dir": "/Volumes/MainLibrary/Archives",
            "snapshot_cadence": input_cadence,
            "snapshot_include_master_db": False,
            "excluded_dirs": [],
            "acoustid_api_key": "",
        },
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    cfg = json.loads((Path(os.environ["HOME"]) / ".fablegear" / "config.json").read_text())
    assert cfg["snapshot_cadence"] == expected_cadence
    assert cfg["snapshot_include_master_db"] is False


@pytest.mark.parametrize(
    "input_value, expected_bool",
    [
        ("yes", True),
        ("YES", True),
        ("no", False),
        ("No", False),
        (1, True),
        (0, False),
        (True, True),
        (False, False),
        (None, False),  # falls back to default False
    ],
)
def test_api_settings_coerces_snapshot_include_master_db(client, input_value, expected_bool):
    r = client.post(
        "/api/settings",
        json={
            "archive_mode": "custom",
            "custom_archive_dir": "/Volumes/MainLibrary/Archives",
            "snapshot_cadence": "monthly",
            "snapshot_include_master_db": input_value,
            "excluded_dirs": [],
            "acoustid_api_key": "",
        },
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    cfg = json.loads((Path(os.environ["HOME"]) / ".fablegear" / "config.json").read_text())
    assert cfg["snapshot_cadence"] == "monthly"
    assert cfg["snapshot_include_master_db"] is expected_bool


def test_api_error_contract_is_sanitized(client, monkeypatch):
    import audit as _audit

    def _boom(*args, **kwargs):
        raise RuntimeError("/Users/dj/Secrets/rekordbox/master.db exploded")

    monkeypatch.setattr(_audit, "find_dead_roots", _boom)

    resp = _hit(client, "/api/audit/path-roots", LOOPBACK)
    data = resp.get_json()

    assert resp.status_code == 500
    assert data["ok"] is False
    assert data["error"] == "internal_error"
    assert data["message"] == "Something went wrong."
    assert "Secrets" not in json.dumps(data)


# ── Dead-file scanner: fail loud on unreadable DB ────────────────────────────

def test_dead_file_scan_raises_on_unreadable_db(flask_app, tmp_path):
    """Regression for the shadowed-fix consolidation: an existing-but-corrupt
    DB must abort the scan (RuntimeError), never warn-and-continue, because a
    silently shrunken known-paths set misclassifies tracked files as dead."""
    from dead_file_scanner import scan_dead_files

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
    from dead_file_scanner import scan_dead_files

    music = tmp_path / "music"
    music.mkdir()
    (music / "track.aiff").write_bytes(b"\x00" * 64)

    result = scan_dead_files([music], db_paths=[tmp_path / "nonexistent.db"])
    assert result is not None


# ── USB inspector: dual-format detection ─────────────────────────────────────

def _fake_stick(tmp_path, devicesql=True, onelibrary=True):
    """Build a minimal fake Pioneer export tree."""
    root = tmp_path / "stick"
    (root / "PIONEER" / "rekordbox").mkdir(parents=True)
    if devicesql:
        # Plausible DeviceSQL header: 4 zero bytes, page=4096, tables=20
        header = b"\x00" * 4 + (4096).to_bytes(4, "little") + (20).to_bytes(4, "little")
        (root / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(
            header + b"\x00" * 4084
        )
    if onelibrary:
        import sqlite3 as _sq
        db = root / "PIONEER" / "rekordbox" / "exportLibrary.db"
        con = _sq.connect(db)
        con.execute("CREATE TABLE content (id INTEGER PRIMARY KEY, title TEXT)")
        con.commit()
        con.close()
    anlz = root / "PIONEER" / "USBANLZ" / "P016" / "0000875E"
    anlz.mkdir(parents=True)
    (anlz / "ANLZ0000.DAT").write_bytes(b"PMAI" + b"\x00" * 60)
    return root


def test_usb_inspector_dual_format(flask_app, tmp_path):
    from usb_inspector import inspect_usb

    report = inspect_usb(_fake_stick(tmp_path))
    assert report.has_pioneer_dir
    assert report.devicesql.valid is True
    assert report.onelibrary.valid is True
    assert report.anlz_track_count == 1
    assert report.dual_format


def test_usb_inspector_rejects_garbage_pdb(flask_app, tmp_path):
    from usb_inspector import inspect_usb

    root = _fake_stick(tmp_path, devicesql=False, onelibrary=False)
    (root / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"NOTAPDB!" * 8)
    report = inspect_usb(root)
    assert report.devicesql.present
    assert report.devicesql.valid is False
    assert not report.cdj3000_ready


def test_usb_inspector_not_a_mount(flask_app, tmp_path):
    import pytest as _pt
    from usb_inspector import NotAMountError, inspect_usb

    with _pt.raises(NotAMountError):
        inspect_usb(tmp_path / "does-not-exist")


# ── Update checker: the v1.0.0 self-update loop regression ───────────────────

def test_update_sha_not_misparsed_as_version(flask_app, monkeypatch):
    """SHAs starting with a digit (12aff07, 9abf982) must NOT be read as
    versions older than every release — that caused the perpetual update loop."""
    import update_checker
    from update_checker import _is_newer, _is_semver_tag

    # _is_newer's is_git=True path is decided by _local_tag_is_current(), which
    # runs real `git rev-parse`/`git merge-base` subprocess calls against the
    # actual checkout. This test is about the SHA-vs-semver guard specifically
    # (the comment below), not about git ancestry, so pin the git-side answer
    # to the case under test rather than depending on whether "v1.0.0" happens
    # to be a resolvable tag in whatever checkout runs the suite.
    monkeypatch.setattr(update_checker, "_local_tag_is_current", lambda tag: None)

    for sha in ("12aff07", "9abf982", "7d62c0a", "0deadbe"):
        assert _is_semver_tag(sha) is False
        # tag not locally resolvable as the SHA -> semver guard rejects SHA -> False
        assert _is_newer("v1.0.0", sha, is_git=True) is False


def test_update_equal_version_no_loop(flask_app, monkeypatch):
    import update_checker
    from update_checker import _is_newer

    # The scenario this guards: the build IS the release, so HEAD already
    # contains the release tag's commit -- the authoritative git check says
    # True. Pin that directly rather than depending on real repo state.
    monkeypatch.setattr(update_checker, "_local_tag_is_current", lambda tag: True)
    assert _is_newer("v1.0.0", "v1.0.0", is_git=True) is False


def test_update_real_upgrade_detected(flask_app):
    from update_checker import _semver_gt
    assert _semver_gt("v1.1.0", "v1.0.0") is True
    assert _semver_gt("v1.0.0", "v1.0") is False  # length-tolerant


def test_update_zip_no_nag_without_version(flask_app):
    from update_checker import _is_newer
    assert _is_newer("v1.0.0", None, is_git=False) is False
    assert _is_newer("v1.0.0", "v1.0.0", is_git=False) is False
    assert _is_newer("v1.0.0", "v0.9", is_git=False) is True


def test_fs_browse_root_skips_boot_volume_alias(client, monkeypatch, tmp_path):
    import config as _config
    import routes_player as _player

    boot_alias = tmp_path / "boot-alias"
    boot_alias.symlink_to("/", target_is_directory=True)

    monkeypatch.setattr(_config, "MUSIC_ROOT", tmp_path / "music")
    monkeypatch.setattr(
        _player,
        "get_connected_volumes",
        lambda: [{"name": "Macintosh HD", "path": str(boot_alias)}],
    )

    resp = _hit(client, "/api/library/fs-browse", LOOPBACK)
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["is_volumes_root"] is True
    assert data["volumes"] == []


def test_enumerate_drive_audio_skips_boot_volume_alias(monkeypatch, tmp_path):
    import config as _config
    import routes_player as _player

    music_root = tmp_path / "music"
    music_root.mkdir()
    track = music_root / "song.mp3"
    track.write_bytes(b"ID3")

    boot_alias = tmp_path / "boot-alias"
    boot_alias.symlink_to("/", target_is_directory=True)

    scanned_roots: list[str] = []
    real_boot = os.path.realpath(str(boot_alias))

    def _fake_walk(root, followlinks=False):
        root_path = Path(root)
        real_root = os.path.realpath(str(root_path))
        scanned_roots.append(real_root)
        if real_root == real_boot:
            raise AssertionError("boot-volume alias should not be scanned")
        if real_root == os.path.realpath(str(music_root)):
            yield str(music_root), [], [track.name]

    monkeypatch.setattr(_config, "MUSIC_ROOT", music_root)
    monkeypatch.setattr(
        _player,
        "get_connected_volumes",
        lambda: [{"name": "Macintosh HD", "path": str(boot_alias)}],
    )
    monkeypatch.setattr(_player.os, "walk", _fake_walk)

    entries, total_estimate, truncated, volumes = _player._enumerate_drive_audio(limit=10)

    assert [entry[0].name for entry in entries] == ["song.mp3"]
    assert total_estimate == 1
    assert truncated is False
    assert volumes == []
    assert scanned_roots == [os.path.realpath(str(music_root))]


def test_fs_browse_recursive_path_uses_guarded_walk(client, monkeypatch, tmp_path):
    import config as _config
    import routes_player as _player

    music_root = tmp_path / "music"
    browse_root = music_root / "crate"
    browse_root.mkdir(parents=True)
    track = browse_root / "song.mp3"
    track.write_bytes(b"ID3")

    scanned_roots: list[str] = []

    def _fake_walk(root, followlinks=False):
        scanned_roots.append(os.path.realpath(str(root)))
        yield str(browse_root), [], [track.name]

    def _unexpected_rglob(self, pattern):
        raise AssertionError("recursive folder browse should not use Path.rglob")

    monkeypatch.setattr(_config, "MUSIC_ROOT", music_root)
    monkeypatch.setattr(_player.os, "walk", _fake_walk)
    monkeypatch.setattr(Path, "rglob", _unexpected_rglob)

    resp = _hit(
        client,
        f"/api/library/fs-browse?path={browse_root}&recursive=1",
        LOOPBACK,
    )
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["recursive"] is True
    assert data["truncated"] is False
    assert data["track_count"] == 1
    assert [item["filename"] for item in data["tracks"]] == ["song.mp3"]
    assert scanned_roots == [os.path.realpath(str(browse_root))]


# ── /api/update/apply: dirty-tree gate ───────────────────────────────────────
#
# Untracked files (scratch notes, tool working-state dirs, generated reports
# that predate a .gitignore entry, ...) can never conflict with a fast-forward
# pull. Only tracked-file modifications — which a pull could silently
# overwrite — should block the update. Regression test for the bug where any
# `git status --porcelain` output at all, including untracked-only trees,
# permanently blocked the updater.

def _fake_git_run(porcelain_output, pull_returncode=1, pull_stderr="stopped for test"):
    """Build a subprocess.run stand-in that answers git status/pull calls
    without touching the real repo, and passes everything else through."""
    import subprocess as _subprocess

    real_run = _subprocess.run

    def _run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == "git":
            if cmd[1] == "status":
                return _subprocess.CompletedProcess(cmd, 0, stdout=porcelain_output, stderr="")
            if cmd[1] == "pull":
                # Never actually pull/relaunch from a test.
                return _subprocess.CompletedProcess(cmd, pull_returncode, stdout="", stderr=pull_stderr)
        return real_run(cmd, *args, **kwargs)

    return _run


def test_update_apply_blocked_by_tracked_modification(client, monkeypatch):
    import app as _app

    monkeypatch.setattr(_app.subprocess, "run", _fake_git_run(" M app.py\n"))
    resp = _hit(client, "/api/update/apply", LOOPBACK, method="POST")
    data = resp.get_json()

    assert resp.status_code == 409
    assert data["ok"] is False
    assert "uncommitted changes" in data["error"]


def test_update_apply_not_blocked_by_untracked_only(client, monkeypatch):
    import app as _app

    # Only untracked files present (e.g. DESIGN.md, .impeccable/) — this used
    # to trip the same 409 as a real tracked-file conflict.
    monkeypatch.setattr(
        _app.subprocess, "run",
        _fake_git_run("?? DESIGN.md\n?? .impeccable/\n"),
    )
    resp = _hit(client, "/api/update/apply", LOOPBACK, method="POST")
    data = resp.get_json()

    # Falls through to the (stubbed) git pull, which we made fail on purpose —
    # the point is it's NOT rejected at the dirty-tree gate with 409.
    assert resp.status_code != 409
    assert data["ok"] is False
    assert "uncommitted changes" not in data["error"]
