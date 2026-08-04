"""
Tests for chop_shop/db_migrator.py's migration safety checks.

Focus: the size-verification fix for the pre-delete check. Before this fix,
step 6 only checked that master.db existed at the destination -- not that it
copied at full size -- before shutil.rmtree()-ing the original. A truncated
copy (destination drive filling up mid-copytree without shutil surfacing a
hard failure) could pass that check and then the original would be deleted
with a corrupt copy left in its place.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import chop_shop.db_migrator as db_migrator

# Captured before the autouse fixture below patches _rb_is_running, so the
# fail-closed test at the bottom of this file can still exercise the real
# implementation.
_real_rb_is_running = db_migrator._rb_is_running


@pytest.fixture(autouse=True)
def _rekordbox_not_running(monkeypatch):
    monkeypatch.setattr(db_migrator, "_rb_is_running", lambda: False)


def _make_source(home: Path, master_db_content: bytes = b"x" * 1000) -> Path:
    src = home / "Library" / "Pioneer" / "rekordbox"
    src.mkdir(parents=True)
    (src / "master.db").write_bytes(master_db_content)
    (src / "other_file.txt").write_text("hello")
    return src


def _drive_target(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    """
    Return (drive_root, target_path_str). _drive_root_from_path() only
    recognizes a real filesystem-root /Volumes/<name>/... shape, which a
    tmp_path-nested fake volume can't match -- monkeypatch it directly so
    these tests aren't coupled to that path-depth heuristic.
    """
    drive_root = tmp_path / "Volumes" / "TestDrive"
    target_dir = drive_root / "Music Library"
    target_dir.mkdir(parents=True)
    monkeypatch.setattr(db_migrator, "_drive_root_from_path", lambda _target: drive_root)
    return drive_root, str(target_dir)


def _run(gen):
    lines = []
    done = None
    for chunk in gen:
        if '"done"' in chunk:
            import json
            done = json.loads(chunk[len("data: "):].strip())
        else:
            import json
            lines.append(json.loads(chunk[len("data: "):].strip())["line"])
    return lines, done


def test_successful_migration_verifies_size_and_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    src = _make_source(tmp_path)
    drive_root, target = _drive_target(tmp_path, monkeypatch)

    _lines, done = _run(db_migrator.migrate(target))

    assert done == {"done": True, "exit_code": 0}
    assert src.is_symlink()
    dst = drive_root / "Pioneer" / "rekordbox"
    assert src.resolve() == dst.resolve()
    assert (dst / "master.db").read_bytes() == b"x" * 1000


def test_aborts_before_deleting_source_on_size_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    src = _make_source(tmp_path, master_db_content=b"x" * 1000)
    _drive_root, target = _drive_target(tmp_path, monkeypatch)

    # Simulate a truncated copy: after the real copytree runs, shrink the
    # destination master.db to look like a short/interrupted copy.
    real_copytree = db_migrator.shutil.copytree

    def truncating_copytree(src_arg, dst_arg, *a, **k):
        result = real_copytree(src_arg, dst_arg, *a, **k)
        (Path(dst_arg) / "master.db").write_bytes(b"x" * 10)  # truncated
        return result

    monkeypatch.setattr(db_migrator.shutil, "copytree", truncating_copytree)

    lines, done = _run(db_migrator.migrate(target))

    assert done["exit_code"] == 1
    assert any("size mismatch" in ln for ln in lines)
    # The original must still be intact -- not deleted, not a symlink.
    assert src.exists()
    assert not src.is_symlink()
    assert (src / "master.db").read_bytes() == b"x" * 1000


def test_rekordbox_running_blocks_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(db_migrator, "_rb_is_running", lambda: True)
    _make_source(tmp_path)
    _drive_root, target = _drive_target(tmp_path, monkeypatch)

    lines, done = _run(db_migrator.migrate(target))

    assert done["exit_code"] == 1
    assert any("Rekordbox is running" in ln for ln in lines)


def test_already_migrated_is_a_clean_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    src_parent = tmp_path / "Library" / "Pioneer"
    src_parent.mkdir(parents=True)
    drive_root, target = _drive_target(tmp_path, monkeypatch)
    dst = drive_root / "Pioneer" / "rekordbox"
    dst.mkdir(parents=True)
    (dst / "master.db").write_bytes(b"x" * 100)

    src = src_parent / "rekordbox"
    src.symlink_to(dst)

    lines, done = _run(db_migrator.migrate(target))

    assert done == {"done": True, "exit_code": 0}
    assert any("Already migrated" in ln for ln in lines)


def test_rb_is_running_fails_closed_when_pgrep_missing(monkeypatch):
    def fake_run(*a, **k):
        raise FileNotFoundError("pgrep not found")

    monkeypatch.setattr(db_migrator.subprocess, "run", fake_run)
    assert _real_rb_is_running() is True
