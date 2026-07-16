"""
Unit tests for chop_shop/pioneer_settings.py.

Fixture honesty note (see docs/dual_format_export.md Phase B notes): no real
Pioneer settings binaries are committed to this repo. The hand-parsed-header
tests below build SYNTHETIC bytes from the documented layout (not a hardware
capture). The pyrekordbox-path tests exercise real pyrekordbox 0.4.4 against
deliberately garbage bytes to verify FableGear's wiring degrades honestly
(valid=False) rather than crashing or claiming success — they do not claim
pyrekordbox itself is being fixture-tested, since a byte-correct MYSETTING.DAT
is not available in this repo.
"""

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from pioneer_settings import read_settings_file, read_settings_tree  # noqa: E402

_LEN_STRINGS = 0x60
_SENTINEL = b"\x78\x56\x34\x12"


def _build_settings_bytes(brand: bytes = b"PIONEER DJ", entry_count: int = 5) -> bytes:
    strings_block = brand + b"\x00rekordbox\x00 6.0.0\x00"
    strings_block = strings_block.ljust(_LEN_STRINGS, b"\x00")[:_LEN_STRINGS]
    header = struct.pack("<I", _LEN_STRINGS) + strings_block
    data_len = struct.pack("<I", 16)
    body = _SENTINEL + struct.pack("<I", entry_count) + b"\x00" * 8
    checksum = struct.pack("<H", 0)
    return header + data_len + body + checksum


def test_hand_parse_unrecognized_filename(tmp_path):
    path = tmp_path / "CUSTOMSETTING.DAT"
    path.write_bytes(_build_settings_bytes())

    report = read_settings_file(path)

    assert report.present
    assert report.parsed_via == "hand-parsed header"
    assert report.valid is True
    assert report.brand == "PIONEER DJ"
    assert report.entry_count == 5
    assert any("not decoded" in n for n in report.notes)


def test_hand_parse_rejects_bad_len_strings(tmp_path):
    path = tmp_path / "CUSTOMSETTING.DAT"
    bad = struct.pack("<I", 0x10) + b"\x00" * 200
    path.write_bytes(bad)

    report = read_settings_file(path)
    assert report.valid is False
    assert "len_strings" in report.detail


def test_hand_parse_rejects_missing_markers(tmp_path):
    path = tmp_path / "CUSTOMSETTING.DAT"
    header = struct.pack("<I", _LEN_STRINGS) + (b"\x00" * _LEN_STRINGS)
    rest = struct.pack("<I", 16) + b"\x00" * 20
    path.write_bytes(header + rest)

    report = read_settings_file(path)
    assert report.valid is False
    assert "missing expected markers" in report.detail


def test_pyrekordbox_path_reports_parse_failure_honestly(tmp_path):
    # A recognized filename with garbage content must not crash — pyrekordbox
    # raises, and read_settings_file must report valid=False rather than
    # silently falling back to the hand-parsed path or claiming success.
    path = tmp_path / "MYSETTING.DAT"
    path.write_bytes(b"\x00" * 40)

    report = read_settings_file(path)
    assert report.present
    assert report.parsed_via == "pyrekordbox"
    assert report.valid is False
    assert "pyrekordbox failed to parse" in report.detail


def test_pyrekordbox_unavailable_reports_explicitly_not_fallback(tmp_path, monkeypatch):
    # A recognized filename when pyrekordbox itself can't be imported must
    # report that explicitly (valid=None) rather than silently falling
    # through to the hand-parsed header path — that fallback is reserved for
    # filenames pyrekordbox doesn't recognize at all. Forcing the import to
    # fail via sys.modules avoids needing to actually uninstall pyrekordbox.
    monkeypatch.setitem(sys.modules, "pyrekordbox.mysettings", None)
    path = tmp_path / "MYSETTING.DAT"
    path.write_bytes(b"\x00" * 40)

    report = read_settings_file(path)

    assert report.present
    assert report.parsed_via == "pyrekordbox"
    assert report.valid is None
    assert "pyrekordbox not available" in report.detail


def test_read_settings_file_missing():
    report = read_settings_file("/nonexistent/MYSETTING.DAT")
    assert not report.present
    assert report.detail == "file not found"


def test_read_settings_tree_only_present_files(tmp_path):
    pioneer = tmp_path / "PIONEER"
    pioneer.mkdir()
    (pioneer / "MYSETTING.DAT").write_bytes(b"\x00" * 40)
    # MYSETTING2.DAT, DEVSETTING.DAT, DJMMYSETTING.DAT intentionally absent.

    reports = read_settings_tree(tmp_path)
    assert len(reports) == 1
    assert reports[0].filename == "MYSETTING.DAT"
