"""
Regression tests for the trash-rescue preflight gate.

Prior to this fix, chop_shop/pruner.py's TrashRescueRequired/
trash_rescue_preflight() was fully implemented and correctly raised when
called directly, but nothing on the live prune path (cli.py cmd_prune /
cmd_rekordbox_dedupe, routes_tools.py's /api/run/prune worker) ever called
it -- so a prune could proceed even with unresolved trash-rescue items.
These tests cover both the gate itself and its wiring into cli.py.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chop_shop.pruner import TrashRescueRequired, trash_rescue_preflight


def _write_csv(path: Path, rows: str) -> None:
    path.write_text(
        "group_id,action,rank,file_path,file_size_mb,bpm,key,filename,keep_in_trash\n" + rows
    )


def test_preflight_is_a_noop_with_no_pending_rescue(tmp_path):
    csv_path = tmp_path / "duplicate_report_20260101_000000.csv"
    _write_csv(csv_path, "g1,KEEP,PN,/music/a.mp3,5.0,120,8A,a.mp3,NO\n"
                          "g1,REVIEW_REMOVE,RAW,/music/b.mp3,5.0,120,8A,b.mp3,NO\n")
    # Should not raise.
    trash_rescue_preflight(csv_path)


def test_preflight_blocks_on_companion_rescue_report(tmp_path):
    csv_path = tmp_path / "duplicate_report_20260101_000000.csv"
    _write_csv(csv_path, "g1,KEEP,PN,/music/a.mp3,5.0,120,8A,a.mp3,NO\n"
                          "g1,REVIEW_REMOVE,RAW,/music/b.mp3,5.0,120,8A,b.mp3,NO\n")
    rescue_path = tmp_path / "trash_rescue_report_20260101_000000.txt"
    rescue_path.write_text("Unique tracks found only in a trash folder:\n/music/unique_in_trash.mp3\n")

    with pytest.raises(TrashRescueRequired) as excinfo:
        trash_rescue_preflight(csv_path)
    assert "unique_in_trash.mp3" not in str(excinfo.value)  # message is a summary, not raw paths
    assert any("1 track" in issue for issue in excinfo.value.issues)


def test_preflight_blocks_on_keep_in_trash_row(tmp_path):
    csv_path = tmp_path / "duplicate_report_20260101_000000.csv"
    _write_csv(csv_path, "g1,KEEP,PN,/music/.Trash/a.mp3,5.0,120,8A,a.mp3,YES\n"
                          "g1,REVIEW_REMOVE,RAW,/music/b.mp3,5.0,120,8A,b.mp3,YES\n")

    with pytest.raises(TrashRescueRequired) as excinfo:
        trash_rescue_preflight(csv_path)
    assert any("trash folder" in issue for issue in excinfo.value.issues)


def test_cmd_prune_refuses_when_rescue_pending(tmp_path, monkeypatch):
    """cli.py cmd_prune must call trash_rescue_preflight and stop before prune_files."""
    import argparse

    import pruner as pruner_module
    from pruner import DupeEntry, DupeGroup

    import cli

    keep_file = tmp_path / "keep.mp3"
    remove_file = tmp_path / "remove.mp3"
    keep_file.touch()
    remove_file.touch()

    fake_group = DupeGroup(group_id="g1", entries=[
        DupeEntry(group_id="g1", action="KEEP", rank="PN", file_path=str(keep_file),
                  file_size_mb=5.0, bpm="120", key="8A", filename="keep.mp3"),
        DupeEntry(group_id="g1", action="REVIEW_REMOVE", rank="RAW", file_path=str(remove_file),
                  file_size_mb=5.0, bpm="120", key="8A", filename="remove.mp3"),
    ])

    prune_files_called = {"value": False}

    def _fake_prune_files(*a, **k):
        prune_files_called["value"] = True
        return {}

    def _fake_preflight(csv_path):
        raise pruner_module.TrashRescueRequired("blocked for test", ["fake unresolved rescue item"])

    monkeypatch.setattr(pruner_module, "load_report", lambda csv_path, db=None: [fake_group])
    monkeypatch.setattr(pruner_module, "prune_files", _fake_prune_files)
    monkeypatch.setattr(pruner_module, "trash_rescue_preflight", _fake_preflight)

    from contextlib import contextmanager

    import db_connection

    @contextmanager
    def _fake_read_db(path=None):
        yield object()

    monkeypatch.setattr(db_connection, "read_db", _fake_read_db)
    monkeypatch.setattr(cli, "LOCAL_DB", tmp_path / "fake_master.db")

    csv_path = tmp_path / "duplicate_report_20260101_000000.csv"
    csv_path.write_text("group_id,action,rank,file_path,file_size_mb,bpm,key,filename\n")

    args = argparse.Namespace(csv_path=str(csv_path), dry_run=False, permanent=False)

    with pytest.raises(SystemExit) as excinfo:
        cli.cmd_prune(args)

    assert excinfo.value.code == 1
    assert prune_files_called["value"] is False, (
        "prune_files() ran despite trash_rescue_preflight raising — the gate is not wired in"
    )
