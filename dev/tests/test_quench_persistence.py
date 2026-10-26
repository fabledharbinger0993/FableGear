"""Quench's loudness results reach the FableGear database, not only the audio file.

Forge persists BPM/key through bulk_set_analysis; Quench's measurement (LUFS, true peak, the gain
it applied) lands in fg_loudness through the same _persist_process_results call. An existing
library created before fg_loudness existed gains the table the next time it is opened.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "fablegear"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from audio_processor import ProcessResult
from cli import _persist_process_results
from fablegear_database.database import FableGearDatabase
from fablegear_database.schema import DatabaseConfig


@pytest.fixture
def archive(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))


def _file(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x" * 64)
    return p


def test_levelled_file_stores_its_new_loudness_and_the_gain(archive, tmp_path):
    f = _file(tmp_path, "loud.mp3")
    _persist_process_results([ProcessResult(
        path=f, loudness_before=-14.2, loudness_after=-9.0, true_peak_before=-6.1, true_peak_after=-0.9,
        loudness_gain_db=5.2, loudness_target=-9.0, normalised=True)], archive)
    row = archive.get_loudness(str(f))
    assert row is not None
    assert (row["integrated_lufs"], row["true_peak_dbtp"], row["gain_applied_db"], row["target_lufs"]) == \
           (-9.0, -0.9, 5.2, -9.0)
    assert row["levelled_at"] is not None


def test_measured_but_not_levelled_keeps_the_last_levelling(archive, tmp_path):
    f = _file(tmp_path, "fine.mp3")
    _persist_process_results([ProcessResult(
        path=f, loudness_before=-12.0, loudness_after=-9.0, loudness_gain_db=3.0, loudness_target=-9.0,
        normalised=True)], archive)
    first = archive.get_loudness(str(f))
    # A later run finds it in tolerance: the measurement updates, the record of the rewrite stays.
    _persist_process_results([ProcessResult(
        path=f, loudness_before=-9.1, true_peak_before=-1.2, loudness_target=-9.0, skipped_loudness=True)], archive)
    row = archive.get_loudness(str(f))
    assert row["integrated_lufs"] == -9.1 and row["true_peak_dbtp"] == -1.2
    assert row["gain_applied_db"] == 3.0 and row["levelled_at"] == first["levelled_at"]


def test_never_levelled_file_has_no_gain(archive, tmp_path):
    f = _file(tmp_path, "quiet.flac")
    _persist_process_results([ProcessResult(path=f, loudness_before=-20.0, true_peak_before=-0.1,
                                            loudness_target=-9.0, skipped_loudness=True)], archive)
    row = archive.get_loudness(str(f))
    assert row["integrated_lufs"] == -20.0 and row["gain_applied_db"] is None and row["levelled_at"] is None


def test_loudness_and_bpm_key_land_on_the_same_track_row(archive, tmp_path):
    f = _file(tmp_path, "both.wav")
    _persist_process_results([ProcessResult(path=f, bpm_detected=124.0, key_detected="8A",
                                            loudness_before=-10.0, loudness_target=-9.0)], archive)
    rec = archive.get_content_by_path(str(f))
    assert rec is not None and rec.bpm == 124.0 and rec.key == "8A"
    assert archive.get_loudness(str(f))["content_id"] == rec.id


def test_no_measurement_writes_nothing(archive, tmp_path):
    f = _file(tmp_path, "none.mp3")
    _persist_process_results([ProcessResult(path=f, bpm_detected=120.0)], archive)
    assert archive.get_loudness(str(f)) is None


def test_deleting_the_track_deletes_its_loudness(archive, tmp_path):
    f = _file(tmp_path, "gone.mp3")
    _persist_process_results([ProcessResult(path=f, loudness_before=-11.0)], archive)
    rec = archive.get_content_by_path(str(f))
    assert archive.delete_content(rec.id)
    with archive.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fg_loudness").fetchone()[0] == 0


def test_existing_library_gains_the_table_on_open(tmp_path):
    db = tmp_path / "old.db"
    FableGearDatabase(DatabaseConfig(db_path=db))
    with sqlite3.connect(db) as conn:  # a library from before schema 1.2.0
        conn.execute("DROP TABLE fg_loudness")
    reopened = FableGearDatabase(DatabaseConfig(db_path=db))
    f = _file(tmp_path, "later.mp3")
    reopened.bulk_set_loudness([(str(f), 64, -9.5, -1.0, -9.0, None, False)])
    row = reopened.get_loudness(str(f))
    assert row is not None and row["integrated_lufs"] == -9.5
