"""
Tests for the shared master.db safe-write envelope (rekordbox_safe_write).

Pin the three guarantees every live-DB write depends on: it refuses to run while
Rekordbox is open, it takes a size-verified backup (all sidecars) before
yielding, and it records an undo manifest.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import rekordbox_safe_write as RSW  # noqa: E402


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "master.db"
    db.write_bytes(b"SQLite format 3\x00" + b"x" * 500)
    (tmp_path / "master.db-wal").write_bytes(b"wal" * 10)
    (tmp_path / "master.db-shm").write_bytes(b"shm" * 10)
    return db


def test_backup_copies_all_sidecars_with_matching_size(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr(RSW, "BACKUP_ROOT", tmp_path / "backups")
    bdir = RSW.backup_master_db(db, "test")
    for name in ("master.db", "master.db-wal", "master.db-shm"):
        assert (bdir / name).is_file()
        assert (bdir / name).stat().st_size == (tmp_path / name).stat().st_size


def test_backup_raises_on_size_mismatch(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr(RSW, "BACKUP_ROOT", tmp_path / "backups")

    # Simulate a truncated copy (e.g. a full disk) for the primary file.
    def _short_copy(src, dst, *a, **k):
        Path(dst).write_bytes(b"truncated")
    monkeypatch.setattr(RSW.shutil, "copy2", _short_copy)

    with pytest.raises(RSW.SafeWriteError):
        RSW.backup_master_db(db, "test")


def test_refuses_when_rekordbox_open(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr(RSW, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(RSW, "rekordbox_running", lambda: True)
    with pytest.raises(RSW.SafeWriteError):
        with RSW.safe_master_write(db, tag="t"):
            pass


def test_missing_target_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(RSW, "rekordbox_running", lambda: False)
    with pytest.raises(RSW.SafeWriteError):
        with RSW.safe_master_write(tmp_path / "nope.db", tag="t"):
            pass


def test_happy_path_backs_up_then_records_manifest(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr(RSW, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(RSW, "rekordbox_running", lambda: False)
    mdir = tmp_path / "manifests"

    with RSW.safe_master_write(db, tag="push", manifest_dir=mdir) as ctx:
        assert ctx.backup_dir.is_dir()                      # backup exists before body
        assert (ctx.backup_dir / "master.db").is_file()
        mpath = ctx.record_manifest({"folder_name": "Recovered X", "crates": 3})

    import json
    data = json.loads(mpath.read_text())
    assert data["folder_name"] == "Recovered X"
    assert data["crates"] == 3
    assert data["target"] == str(db)
    assert data["backup"] == str(ctx.backup_dir)
    assert "timestamp" in data
