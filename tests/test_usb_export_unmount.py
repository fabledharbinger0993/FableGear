"""
Regression test for USB export mid-unmount detection.

Prior to this fix, helpers._run_export() had no explicit "is the target
volume still there" check anywhere -- confirmed by grepping the function
body for anlz|ENOSPC|disk_usage|mounted|OSError|IOError and finding nothing
relevant. A drive that disappeared mid-copy would just cascade into
per-file OSError/FileNotFoundError entries in the errors list, or a
cryptic failure from deep inside a pyrekordbox commit() call.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import helpers


def test_export_target_reachable_true_for_existing_directory(tmp_path):
    assert helpers.export_target_reachable(str(tmp_path)) is True


def test_export_target_reachable_false_for_vanished_path(tmp_path):
    gone = tmp_path / "unplugged_drive"
    # Never created -- simulates the volume having disappeared.
    assert helpers.export_target_reachable(str(gone)) is False


def test_export_target_reachable_false_after_directory_removed(tmp_path):
    drive = tmp_path / "usb"
    drive.mkdir()
    assert helpers.export_target_reachable(str(drive)) is True

    drive.rmdir()
    assert helpers.export_target_reachable(str(drive)) is False


def test_master_db_layout_carries_an_honest_export_note(tmp_path):
    """
    The 'master-db' layout is FableGear's own database format, not a
    standard Rekordbox USB export -- it doesn't include ANLZ (waveform/
    beat-grid/hot-cue) data. Users should see that before exporting, not
    discover it as blank waveforms on a CDJ.
    """
    (tmp_path / "PIONEER" / "Master").mkdir(parents=True)
    (tmp_path / "PIONEER" / "Master" / "master.db").touch()

    info = helpers._detect_pioneer_drive_layout(tmp_path)

    assert info["export_supported"] is True
    assert info["export_note"]
    assert "ANLZ" in info["export_note"]


def test_rekordbox_usb_export_layout_has_no_export_note(tmp_path):
    (tmp_path / "PIONEER" / "rekordbox").mkdir(parents=True)
    (tmp_path / "PIONEER" / "rekordbox" / "exportLibrary.db").touch()

    info = helpers._detect_pioneer_drive_layout(tmp_path)

    assert info["export_supported"] is False
    assert info["export_note"] is None
