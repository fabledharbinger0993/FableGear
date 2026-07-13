"""
Unit tests for chop_shop/devicesql_reader.py.

No export.pdb binary is committed to this repo (see module docstring SCOPE
LIMIT), so these tests build a SYNTHETIC 16-byte header using the real
numbers documented in docs/format_samples/DJMTGO_inspection.md (page size
4096, 20 tables) — not a captured hardware file. Row-level parsing is out of
scope for this phase and is asserted to stay an empty stub rather than
guessed at.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from devicesql_reader import read_pdb  # noqa: E402


def _header(page_size: int = 4096, num_tables: int = 20, next_unused_page: int = 47) -> bytes:
    return (
        b"\x00\x00\x00\x00"
        + page_size.to_bytes(4, "little")
        + num_tables.to_bytes(4, "little")
        + next_unused_page.to_bytes(4, "little")
    )


def test_read_pdb_valid_header_matches_djmtgo_ground_truth(tmp_path):
    path = tmp_path / "export.pdb"
    path.write_bytes(_header() + b"\x00" * 4096)  # pad to a page for realism

    report = read_pdb(path)

    assert report.exists and report.readable
    assert report.valid_header is True
    assert report.page_size == 4096
    assert report.num_tables == 20
    assert report.next_unused_page == 47
    assert report.tracks == []
    assert report.partial is True
    assert any("NOT extracted" in n for n in report.notes)


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
