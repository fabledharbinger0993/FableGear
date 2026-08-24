"""
Integration test for chop_shop/export_auditor.py.

Builds a SYNTHETIC PIONEER/ export tree (ANLZ set, settings file, PDB
header — all hand-assembled from the documented layout, not a hardware
capture; see docs/dual_format_export.md Phase B notes and the sibling
test_anlz_reader.py / test_pioneer_settings.py / test_devicesql_reader.py
fixture-honesty notes) and drives the auditor against a REAL temporary
FableGearDatabase — no mocking the archive away. Verifies both the returned
report and that the archive rows persisted (reopening the DB fresh, per the
"data persists in the archive" requirement).
"""

import json
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from export_auditor import audit_export  # noqa: E402
from usb_inspector import NotAMountError  # noqa: E402
from fablegear_database.database import FableGearDatabase, ContentRecord  # noqa: E402
from fablegear_database.schema import DatabaseConfig  # noqa: E402


def _anlz_tag(fourcc: bytes, body: bytes, len_header: int = 12) -> bytes:
    len_tag = 12 + len(body)
    return fourcc + struct.pack(">II", len_header, len_tag) + body


def _ppth_tag(path: str) -> bytes:
    # len_path lives in the header-extension region, body is exactly the
    # UTF-16BE path bytes — confirmed against real ANLZ0000.2EX/.EXT samples.
    path_bytes = (path + "\x00").encode("utf-16-be")
    header_ext = struct.pack(">I", len(path_bytes))
    return _anlz_tag(b"PPTH", header_ext + path_bytes, len_header=16)


def _pqtz_tag(beats) -> bytes:
    body = struct.pack(">III", 0, 0, len(beats))
    for beat_no, tempo_bpm, time_ms in beats:
        body += struct.pack(">HHI", beat_no, int(round(tempo_bpm * 100)), time_ms)
    return _anlz_tag(b"PQTZ", body)


def _build_anlz_bytes(track_path: str) -> bytes:
    tags = _ppth_tag(track_path) + _pqtz_tag([(1, 124.0, 110), (2, 124.0, 594)])
    len_header = 28
    header = b"PMAI" + struct.pack(">II", len_header, len_header + len(tags)) + b"\x00" * (len_header - 12)
    return header + tags


def _build_pdb_header(page_size=4096, num_tables=20, next_unused_page=47) -> bytes:
    return (
        b"\x00\x00\x00\x00"
        + page_size.to_bytes(4, "little")
        + num_tables.to_bytes(4, "little")
        + next_unused_page.to_bytes(4, "little")
    )


@pytest.fixture
def synthetic_export(tmp_path):
    root = tmp_path / "GIGSTICK"
    anlz_dir = root / "PIONEER" / "USBANLZ" / "0001" / "0001"
    anlz_dir.mkdir(parents=True)
    (anlz_dir / "ANLZ0000.DAT").write_bytes(_build_anlz_bytes("/Contents/Synthetic Artist/track.mp3"))

    settings_dir = root / "PIONEER"
    (settings_dir / "MYSETTING.DAT").write_bytes(b"\x00" * 40)  # garbage -> pyrekordbox parse error, logged as such

    pdb_dir = root / "PIONEER" / "rekordbox"
    pdb_dir.mkdir(parents=True)
    (pdb_dir / "export.pdb").write_bytes(_build_pdb_header() + b"\x00" * 4096)

    # Opaque token blobs — presence/size only, contents never read.
    for name in ("ak.dat", "nn.dat", "gcred.dat"):
        (root / "PIONEER" / name).write_bytes(b"\x01" * 16)

    return root


@pytest.fixture
def archive(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))


def test_audit_export_report_shape(synthetic_export, archive):
    report = audit_export(synthetic_export, archive=archive)

    assert report.anlz_summary.tracks_scanned == 1
    assert report.anlz_summary.with_ppth == 1
    assert report.anlz_summary.with_beat_grid == 1
    assert report.anlz_summary.total_beats == 2
    assert len(report.settings_files) == 1
    assert report.settings_files[0].valid is False  # garbage bytes, honestly reported
    assert report.pdb_report is not None
    assert report.pdb_report.valid_header is True
    # synthetic_export's export.pdb is header-only (no real table pointer
    # data), so the tracks table walk degenerates to an empty-but-successful
    # walk — 0 rows found, not a failure. See tests/test_devicesql_pdb_rowwalk.py
    # for coverage of an actual populated tracks table.
    assert report.pdb_report.tracks == []
    assert report.pdb_report.partial is False
    assert report.archive_logged is True


def test_audit_export_cross_matches_library(synthetic_export, archive):
    archive.insert_content(ContentRecord(
        file_path="/Contents/Synthetic Artist/track.mp3",
        file_name="track.mp3",
        file_size=1234,
    ))

    report = audit_export(synthetic_export, archive=archive)

    assert report.library_cross_match.anlz_tracks_with_path == 1
    assert report.library_cross_match.matched_in_archive == 1


def test_audit_export_persists_rows_visible_after_reopen(synthetic_export, archive, tmp_path):
    audit_export(synthetic_export, archive=archive)

    # Reopen fresh — no reliance on the in-memory report or cached connection.
    reopened = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))

    assert reopened.count_operations("anlz_read") == 1
    assert reopened.count_operations("settings_read") == 1
    assert reopened.count_operations("pdb_read") == 1
    assert reopened.count_operations("export_audit") == 1

    with reopened.connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path, status, metadata FROM fg_processing_log WHERE operation_type = 'export_audit'"
        )
        file_path, status, metadata_json = cur.fetchone()

    assert file_path == str(synthetic_export)
    assert status == "ok"
    metadata = json.loads(metadata_json)
    assert metadata["anlz_tracks_scanned"] == 1
    assert metadata["anlz_with_beat_grid"] == 1
    assert metadata["pdb_present"] is True
    assert metadata["pdb_valid_header"] is True


def test_audit_export_reports_opaque_token_findings(synthetic_export, archive):
    report = audit_export(synthetic_export, archive=archive)

    findings_by_name = {f.name: f for f in report.encryption_findings}
    for token_name in ("ak.dat", "nn.dat", "gcred.dat"):
        finding = findings_by_name[token_name]
        assert finding.present is True
        assert finding.size == 16
        assert finding.path is not None
        assert "NOT read" in finding.note


def test_audit_export_without_archive_still_returns_report(synthetic_export):
    report = audit_export(synthetic_export, archive=None)
    assert report.archive_logged is False
    assert "not persisted" in " ".join(report.notes)


def test_audit_export_not_a_mount(tmp_path):
    with pytest.raises(NotAMountError):
        audit_export(tmp_path / "nonexistent")
