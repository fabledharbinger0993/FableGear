"""
Unit tests for chop_shop/anlz_reader.py.

IMPORTANT — fixture honesty (see docs/dual_format_export.md Phase B notes):
no real ANLZ binaries are committed to docs/format_samples/ (only a redacted
markdown inspection report). Every byte buffer built in this file is
SYNTHETIC — hand-assembled from the documented tag layout, not captured from
real hardware. It verifies the tag-chain walker and per-tag decoders parse
their own documented layout correctly; it is not a substitute for testing
against a real export. A real-fixture test should be added the moment a
sanitized ANLZ file is committed to docs/format_samples/.
"""

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from anlz_reader import parse_anlz_file, read_anlz_set  # noqa: E402


def _tag(fourcc: bytes, body: bytes, len_header: int = 12) -> bytes:
    # body is everything after the 12-byte generic prefix (magic+len_header+
    # len_tag), so the tag's total on-disk size is always 12 + len(body) —
    # len_header only marks where the *semantic* body starts within that.
    len_tag = 12 + len(body)
    return fourcc + struct.pack(">II", len_header, len_tag) + body


def _ppth_tag(path: str) -> bytes:
    path_bytes = (path + "\x00").encode("utf-16-be")
    body = struct.pack(">I", len(path_bytes)) + path_bytes
    return _tag(b"PPTH", body)


def _pqtz_tag(beats: list) -> bytes:
    body = struct.pack(">III", 0, 0, len(beats))
    for beat_no, tempo_bpm, time_ms in beats:
        body += struct.pack(">HHI", beat_no, int(round(tempo_bpm * 100)), time_ms)
    return _tag(b"PQTZ", body)


def _pwv6_tag(len_entry_bytes: int, entries: bytes) -> bytes:
    len_entries = len(entries) // len_entry_bytes
    header_extra = struct.pack(">II", len_entry_bytes, len_entries)
    return _tag(b"PWV6", header_extra + entries, len_header=12 + len(header_extra))


def _pcob_tag() -> bytes:
    # Speculative tag — no verified byte layout, body is arbitrary padding.
    return _tag(b"PCOB", b"\x00" * 8)


def _build_anlz(tags: bytes) -> bytes:
    len_header = 28
    len_file = len_header + len(tags)
    header = b"PMAI" + struct.pack(">II", len_header, len_file) + b"\x00" * (len_header - 12)
    return header + tags


def test_parse_anlz_file_decodes_ppth_pqtz_and_waveform(tmp_path):
    beats = [(1, 124.00, 110), (2, 124.00, 594), (3, 124.00, 1078)]
    tags = _ppth_tag("/Contents/Synthetic/track.mp3") + _pqtz_tag(beats) + _pwv6_tag(3, b"\xab" * 12) + _pcob_tag()
    anlz_path = tmp_path / "ANLZ0000.DAT"
    anlz_path.write_bytes(_build_anlz(tags))

    report = parse_anlz_file(anlz_path)

    assert report.exists and report.readable
    assert report.tags_present == ["PPTH", "PQTZ", "PWV6", "PCOB"]
    assert report.ppth_path == "/Contents/Synthetic/track.mp3"
    assert len(report.beat_grid) == 3
    assert report.beat_grid[0].tempo_bpm == 124.00
    assert report.beat_grid[0].time_ms == 110
    assert report.beat_grid[2].beat_no == 3
    assert "PWV6" in report.waveform_tags
    wf = report.waveform_tags["PWV6"]
    assert wf.len_entry_bytes == 3
    assert wf.len_entries == 4
    assert wf.entry_bytes_total == 12
    assert any("PCOB" in note for note in report.notes)


def test_parse_anlz_file_missing_file():
    report = parse_anlz_file("/nonexistent/ANLZ0000.DAT")
    assert not report.exists
    assert "file not found" in report.notes


def test_parse_anlz_file_bad_magic(tmp_path):
    bad = tmp_path / "ANLZ0000.DAT"
    bad.write_bytes(b"NOPE" + b"\x00" * 20)
    report = parse_anlz_file(bad)
    assert report.readable
    assert any("PMAI" in note for note in report.notes)
    assert report.tags_present == []


def test_walk_tags_stops_on_truncated_declaration(tmp_path):
    # A tag that declares more bytes than actually exist in the buffer must
    # stop the walk cleanly instead of raising or reading past the buffer.
    truncated_tag = b"PQTZ" + struct.pack(">II", 12, 9999)
    anlz_path = tmp_path / "ANLZ0000.DAT"
    anlz_path.write_bytes(_build_anlz(truncated_tag))

    report = parse_anlz_file(anlz_path)
    assert report.tags_present == []


def test_read_anlz_set_dat_only(tmp_path):
    tags = _ppth_tag("/Contents/Synthetic/only_dat.mp3")
    anlz_dir = tmp_path / "0001"
    anlz_dir.mkdir()
    (anlz_dir / "ANLZ0000.DAT").write_bytes(_build_anlz(tags))

    set_report = read_anlz_set(anlz_dir)
    assert set_report.dat is not None
    assert set_report.ext is None
    assert set_report.two_ex is None
    assert set_report.track_path == "/Contents/Synthetic/only_dat.mp3"


def test_read_anlz_set_missing_directory():
    set_report = read_anlz_set("/nonexistent/anlz/dir")
    assert set_report.dat is None
    assert "not a directory" in set_report.notes
