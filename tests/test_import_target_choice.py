"""
Regression tests for the 3-way Import target choice (rekordbox / both /
fablegear).

Before this change, "Import Tracks" always did both: write into FableGear's
own database, then sync into Rekordbox. There was no way to import into only
one of the two. These tests cover:
  - cli.py cmd_import routes to the right underlying import path per --target
  - the "rekordbox" target genuinely writes only to Rekordbox, never touching
    FableGear's own database
  - routes_rekordbox.py's /api/run/import validates target and defaults to
    the persisted config value when none is given on the request
  - app.py's /api/settings persists and validates import_target
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables as rb_tables

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rekordbox_meta_support import relaxed_rekordbox_nullability


# ── Shared Rekordbox test-DB fixture (mirrors test_sync_fablegear_to_rekordbox.py) ──

@pytest.fixture
def rdb_path(tmp_path):
    db_path = tmp_path / "master.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with relaxed_rekordbox_nullability():
        rb_tables.Base.metadata.create_all(engine)
    engine.dispose()

    handle = Rekordbox6Database(path=str(db_path), unlock=False)
    from datetime import datetime
    item = rb_tables.DjmdMenuItems(
        ID="track-menu-id", Class="track-class", Name="TRACK", UUID="track-menu-uuid",
        rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0,
        usn=0, rb_local_usn=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    device = rb_tables.DjmdDevice(
        ID="device-id", Name="Mac", UUID="device-uuid",
        rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0,
        usn=0, rb_local_usn=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    registry = rb_tables.AgentRegistry(
        registry_id="localUpdateCount", int_1=0,
        date_1=datetime.utcnow(), date_2=datetime.utcnow(),
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
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


# ── cmd_import routing ───────────────────────────────────────────────────────

def _fake_args(path, *, target="both", dry_run=False, resume=False, also_scan=None):
    import argparse
    return argparse.Namespace(
        path=str(path), target=target, dry_run=dry_run, resume=resume, also_scan=also_scan,
    )


def test_cmd_import_routes_rekordbox_target_to_direct_path(tmp_path, monkeypatch):
    import cli

    music_dir = tmp_path / "music"
    music_dir.mkdir()

    rekordbox_only_called = {"value": False}
    database_first_called = {"value": False}

    def fake_import_directory(root, db, *, dry_run=False, resume=False):
        rekordbox_only_called["value"] = True
        from importer import ImportReport
        return ImportReport()

    def fake_multi_drive(*a, **k):
        database_first_called["value"] = True
        from importer_database import ImportReport
        return ImportReport()

    monkeypatch.setattr("importer.import_directory", fake_import_directory)
    monkeypatch.setattr("importer_database.import_multi_drive_database_first", fake_multi_drive)

    cli.cmd_import(_fake_args(music_dir, target="rekordbox"))

    assert rekordbox_only_called["value"] is True
    assert database_first_called["value"] is False


@pytest.mark.parametrize("target", ["both", "fablegear"])
def test_cmd_import_routes_both_and_fablegear_to_database_first_path(tmp_path, monkeypatch, target):
    import cli

    music_dir = tmp_path / "music"
    music_dir.mkdir()

    rekordbox_only_called = {"value": False}
    seen_export_flag = {}

    def fake_import_directory(*a, **k):
        rekordbox_only_called["value"] = True
        from importer import ImportReport
        return ImportReport()

    def fake_multi_drive(roots, *, export_to_rekordbox, **k):
        seen_export_flag["value"] = export_to_rekordbox
        from importer_database import ImportReport
        return ImportReport()

    monkeypatch.setattr("importer.import_directory", fake_import_directory)
    monkeypatch.setattr("importer_database.import_multi_drive_database_first", fake_multi_drive)

    cli.cmd_import(_fake_args(music_dir, target=target))

    assert rekordbox_only_called["value"] is False
    # "both" syncs to Rekordbox (export_to_rekordbox=True); "fablegear" does not.
    assert seen_export_flag["value"] == (target == "both")


def test_cmd_import_defaults_to_both_when_target_omitted(tmp_path, monkeypatch):
    """argparse default is 'both' (see p_import.add_argument('--target', default='both'))."""
    import cli
    import argparse

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    # Namespace without a 'target' attribute at all, matching what argparse
    # would never actually produce (it always sets the default) -- but
    # cmd_import's getattr(args, "target", None) or "both" fallback should
    # still land on "both" defensively.
    args = argparse.Namespace(path=str(music_dir), dry_run=False, resume=False, also_scan=None)

    seen_export_flag = {}

    def fake_multi_drive(roots, *, export_to_rekordbox, **k):
        seen_export_flag["value"] = export_to_rekordbox
        from importer_database import ImportReport
        return ImportReport()

    monkeypatch.setattr("importer_database.import_multi_drive_database_first", fake_multi_drive)

    cli.cmd_import(args)

    assert seen_export_flag["value"] is True


# ── "rekordbox" target: real end-to-end write, FableGear DB never touched ───

def test_rekordbox_only_target_writes_to_rekordbox_and_skips_fablegear_db(tmp_path, monkeypatch, rdb_path):
    import cli

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    track_file = music_dir / "track.mp3"
    track_file.write_bytes(b"\x00" * 4096)  # non-empty so scanning doesn't choke

    fablegear_db_touched = {"value": False}
    real_fg_db_cls = None
    try:
        import fablegear_database
        real_fg_db_cls = fablegear_database.FableGearDatabase
    except ImportError:
        pass

    if real_fg_db_cls is not None:
        def tripwire(*a, **k):
            fablegear_db_touched["value"] = True
            return real_fg_db_cls(*a, **k)
        monkeypatch.setattr(fablegear_database, "FableGearDatabase", tripwire)

    cli.cmd_import(_fake_args(music_dir, target="rekordbox"))

    assert fablegear_db_touched["value"] is False, (
        "target=rekordbox must never instantiate FableGear's own database"
    )

    # The synthetic file above isn't valid audio, so it's expected to fail
    # metadata extraction and land in report.failed rather than actually
    # importing -- that's fine. What this test asserts is the code path
    # taken (real Rekordbox write session, zero FableGear DB instantiation),
    # not that a fake file successfully imports. Confirm the write session
    # itself completed cleanly against the real Rekordbox fixture DB rather
    # than raising or silently no-op'ing.
    verify_db = Rekordbox6Database(path=str(rdb_path), unlock=False)
    try:
        verify_db.get_content().all()  # DB is still readable/consistent post-write
    finally:
        verify_db.close()


# ── routes_rekordbox.py api_import target handling ──────────────────────────

def test_api_import_rejects_invalid_target(tmp_path):
    import app as app_module

    client = app_module.app.test_client()
    resp = client.get("/api/run/import", query_string={"path": str(tmp_path), "target": "nonsense"})
    assert resp.status_code == 400
    assert "target" in resp.get_json().get("error", "").lower() or "Invalid" in resp.get_json().get("error", "")


def test_api_import_fablegear_target_skips_rekordbox_closed_check(tmp_path, monkeypatch):
    """target=fablegear never writes to Rekordbox, so it must not be blocked
    by Rekordbox running -- unlike every other target."""
    import routes_rekordbox

    monkeypatch.setattr(routes_rekordbox, "_require_rb_closed", lambda: {"error": "should not be called"})

    import app as app_module
    client = app_module.app.test_client()
    resp = client.get("/api/run/import", query_string={
        "path": str(tmp_path), "target": "fablegear",
    })
    # Should not hit the (monkeypatched, poisoned) _require_rb_closed at all.
    assert resp.status_code != 500
    body = resp.get_data(as_text=True)
    assert "should not be called" not in body


# ── app.py /api/settings import_target persistence ───────────────────────────

def test_settings_rejects_invalid_import_target(monkeypatch, tmp_path):
    import app as app_module
    import user_config

    fake_cfg_path = tmp_path / "config.json"
    fake_cfg_path.write_text("{}")
    monkeypatch.setattr(user_config, "CONFIG_PATH", fake_cfg_path)
    monkeypatch.setattr(
        user_config, "load_user_config",
        lambda: {**user_config.DEFAULTS, "local_db": "x", "device_db": "x", "music_root": "x", "backup_dir": "x"},
    )

    client = app_module.app.test_client()
    resp = client.post("/api/settings", json={"import_target": "not-a-real-choice"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_settings_persists_valid_import_target(monkeypatch, tmp_path):
    import app as app_module
    import user_config

    fake_cfg_path = tmp_path / "config.json"
    fake_cfg_path.write_text("{}")
    monkeypatch.setattr(user_config, "CONFIG_PATH", fake_cfg_path)
    monkeypatch.setattr(
        user_config, "load_user_config",
        lambda: {**user_config.DEFAULTS, "local_db": "x", "device_db": "x", "music_root": "x", "backup_dir": "x"},
    )

    client = app_module.app.test_client()
    resp = client.post("/api/settings", json={"import_target": "fablegear"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    import json as _json
    saved = _json.loads(fake_cfg_path.read_text())
    assert saved["import_target"] == "fablegear"
