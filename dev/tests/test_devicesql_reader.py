"""
Unit tests for chop_shop/devicesql_reader.py's file-header parsing.

Most of these tests build a SYNTHETIC 16-byte header (a real Rekordbox export is now committed under
tests/fixtures/rekordbox_21track_export/ and is checked by the last test in this file) using the
real numbers documented in docs/format_samples/usb_format_inspection.md (page
size 4096, 20 tables) — not a captured hardware file. Row-level parsing
(tracks table page/row walk, string decoding) is covered separately in
tests/test_devicesql_pdb_rowwalk.py against fixtures built from Deep
Symmetry's published DeviceSQL format spec.
"""

import builtins
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2] / "fablegear"
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from devicesql_reader import read_pdb


def _header(page_size: int = 4096, num_tables: int = 20, next_unused_page: int = 47) -> bytes:
    return (
        b"\x00\x00\x00\x00"
        + page_size.to_bytes(4, "little")
        + num_tables.to_bytes(4, "little")
        + next_unused_page.to_bytes(4, "little")
    )


def test_read_pdb_valid_header_matches_usb_ground_truth(tmp_path):
    path = tmp_path / "export.pdb"
    path.write_bytes(_header() + b"\x00" * 4096)  # pad to a page for realism

    report = read_pdb(path)

    assert report.exists and report.readable
    assert report.valid_header is True
    assert report.page_size == 4096
    assert report.num_tables == 20
    assert report.next_unused_page == 47
    # This fixture's "table pointer array" is zero padding, not real table
    # data — the tracks table pointer degenerates to first_page=last_page=0,
    # which the row walker handles as a harmless empty table (0 rows found,
    # walk completes without error). See tests/test_devicesql_pdb_rowwalk.py
    # for real row-walking coverage against a properly constructed fixture.
    assert report.tracks == []
    assert report.partial is False
    assert any("Extracted 0 track row" in n for n in report.notes)


def test_read_pdb_rejects_nonzero_lead_bytes(tmp_path):
    path = tmp_path / "export.pdb"
    bad = b"\x01\x00\x00\x00" + _header()[4:]
    path.write_bytes(bad)

    report = read_pdb(path)
    assert report.valid_header is False
    assert "not DeviceSQL" in report.detail


def test_read_pdb_rejects_implausible_page_size(tmp_path):
    path = tmp_path / "export.pdb"
    path.write_bytes(_header(page_size=12345))

    report = read_pdb(path)
    assert report.valid_header is False
    assert "implausible page size" in report.detail


def test_read_pdb_rejects_implausible_table_count(tmp_path):
    path = tmp_path / "export.pdb"
    path.write_bytes(_header(num_tables=999))

    report = read_pdb(path)
    assert report.valid_header is False
    assert "implausible table count" in report.detail


def test_read_pdb_too_small(tmp_path):
    path = tmp_path / "export.pdb"
    path.write_bytes(b"\x00" * 8)

    report = read_pdb(path)
    assert report.readable
    assert report.valid_header is False
    assert "too small" in report.detail


def test_read_pdb_missing_file():
    report = read_pdb("/nonexistent/export.pdb")
    assert not report.exists
    assert report.detail == "file not found"


def test_read_pdb_unreadable_file(tmp_path, monkeypatch):
    # Covers the OSError branch on open() — e.g. a permission error — without
    # relying on actual filesystem permissions (unreliable when running as
    # root, as this sandbox does).
    path = tmp_path / "export.pdb"
    path.write_bytes(b"\x00" * 20)

    real_open = builtins.open

    def _raise_for_target(file, *args, **kwargs):
        if str(file) == str(path):
            raise OSError("permission denied (simulated)")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raise_for_target)

    report = read_pdb(path)
    assert report.exists
    assert not report.readable
    assert "unreadable" in report.detail


def test_real_rekordbox_export_fixture_header_parses():
    """A genuine Rekordbox-written export that a CDJ-3000 (firmware 3.2x) loaded correctly.

    Header-level facts only: row-level recovery of this file is not yet complete (see
    docs/devicesql_export_writer.md, milestone 1), so no track count is asserted here.
    """
    from pathlib import Path

    from chop_shop.devicesql_reader import read_pdb

    root = Path(__file__).resolve().parent / "fixtures" / "rekordbox_21track_export" / "PIONEER" / "rekordbox"
    main = read_pdb(root / "export.pdb")
    assert main.valid_header is True
    assert (main.page_size, main.num_tables, main.next_unused_page, main.file_size) == (4096, 20, 60, 241664)
    ext = read_pdb(root / "exportExt.pdb")
    assert ext.valid_header is True
    assert (ext.page_size, ext.num_tables, ext.next_unused_page, ext.file_size) == (4096, 9, 21, 73728)
