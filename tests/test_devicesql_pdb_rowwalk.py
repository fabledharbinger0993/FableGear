"""
Tests for devicesql_reader.py's tracks-table row walker.

No export.pdb binary is committed to this repo (see module docstring
HONESTY LIMIT) so — same convention as tests/test_devicesql_reader.py and
tests/test_anlz_reader.py — these tests build a SYNTHETIC PDB file
byte-for-byte from Deep Symmetry's published DeviceSQL format
specification (https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html),
not a captured hardware file. They verify the parser round-trips the
documented format correctly; they do not (and cannot, without a real
sample) verify fidelity to actual CDJ-3000 hardware output.

One real data point exists: a genuinely Rekordbox-written export.pdb was
found and read (not committed to the repo — see docs/format_samples/) whose
every table root page was an EMPTY index page (num_entries=0, page_flags
0x64, matching the documented "typical value" exactly). That confirmed the
file header, table pointer array, page header, and index-page-detection
logic against real hardware bytes, and caught a real gap this synthetic
suite had missed: the walker was skipping index pages entirely instead of
following their entries to the actual data pages. Fixed in
_walk_table_pages / _parse_index_entries. The populated-index-page case
(a non-empty index page's entries actually resolving to data) is still
synthetic-only below — no real sample with populated tables was available.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from devicesql_reader import (
    _DATA_PAGE_HEADER_SIZE,
    _FILE_HEADER_SIZE,
    TABLE_TYPE_TRACKS,
    _decode_pdb_string,
    read_pdb,
)

PAGE_SIZE = 4096


def _pack_row_counts(num_row_offsets: int, num_rows: int) -> bytes:
    """Pack the 3-byte row-count bitfield: low 13 bits num_row_offsets,
    high 11 bits num_rows (see devicesql_reader._parse_page_header)."""
    val = (num_row_offsets & 0x1FFF) | ((num_rows & 0x7FF) << 13)
    return val.to_bytes(3, "little")


def _short_string(text: str) -> bytes:
    data = text.encode("ascii")
    total_len = len(data) + 1  # includes the flag byte itself
    assert total_len <= 126
    flag = (total_len << 1) | 1
    return bytes([flag]) + data


def _long_string(text: str, utf16: bool = False) -> bytes:
    data = text.encode("utf-16-le") if utf16 else text.encode("ascii")
    total_len = len(data) + 4
    flag = 0x90 if utf16 else 0x40
    return bytes([flag]) + total_len.to_bytes(2, "little") + b"\x00" + data


def _build_track_page(
    *,
    track_id: int,
    tempo_x100: int,
    artist_id: int,
    album_id: int,
    key_id: int,
    title: bytes,
    file_path: bytes,
    anlz_path: bytes,
    page_index: int = 1,
) -> bytes:
    """
    Build one synthetic data page containing exactly one tracks-table row.

    Layout (all offsets per Deep Symmetry's documented format):
      0x00-0x20  common page header
      0x20-0x28  data-page-specific fields (unused by the parser, zeroed)
      0x28..     the track row itself (ofs[0] = 0, so it starts right here)
      end-36..end  row-group 0 index: ofs[0](2) ... ofs[15](2) tranrf0(2) rowpf0(2)
    """
    buf = bytearray(PAGE_SIZE)

    # Common page header
    buf[0x04:0x08] = page_index.to_bytes(4, "little")
    buf[0x08:0x0C] = TABLE_TYPE_TRACKS.to_bytes(4, "little")  # type
    buf[0x0C:0x10] = page_index.to_bytes(4, "little")     # next_page (== last_page, unused)
    buf[0x18:0x1B] = _pack_row_counts(num_row_offsets=1, num_rows=1)
    buf[0x1B] = 0x24  # data page, not an index page (bit6 clear)

    # The row itself, starting at the data-page-header boundary.
    row_start = _DATA_PAGE_HEADER_SIZE
    buf[row_start:row_start + 2] = (0x0024).to_bytes(2, "little")  # subtype
    buf[row_start + 0x20:row_start + 0x24] = key_id.to_bytes(4, "little")
    buf[row_start + 0x38:row_start + 0x3C] = tempo_x100.to_bytes(4, "little")
    buf[row_start + 0x40:row_start + 0x44] = album_id.to_bytes(4, "little")
    buf[row_start + 0x44:row_start + 0x48] = artist_id.to_bytes(4, "little")
    buf[row_start + 0x48:row_start + 0x4C] = track_id.to_bytes(4, "little")

    # String offsets array: 21 x u16le, relative to row_start. Place the
    # three strings we care about right after the fixed row fields end.
    str_offsets_start = row_start + 0x5E
    string_data_pos = str_offsets_start + 21 * 2  # first free byte after the offsets array
    offsets = [0] * 21

    blobs = []
    for idx, blob in ((14, anlz_path), (17, title), (20, file_path)):
        offsets[idx] = string_data_pos - row_start  # offsets are relative to row start
        blobs.append((string_data_pos, blob))
        string_data_pos += len(blob)

    for i, ofs in enumerate(offsets):
        p = str_offsets_start + i * 2
        buf[p:p + 2] = ofs.to_bytes(2, "little")

    for pos, blob in blobs:
        buf[pos:pos + len(blob)] = blob

    # Row-group 0 index, at the very end of the page.
    group_end = PAGE_SIZE
    group_start = group_end - 36
    buf[group_start:group_start + 2] = (0).to_bytes(2, "little")  # ofs[0] == 0 (row starts at 0x28)
    # ofs[1..15] left as zero — never read, since num_row_offsets == 1.
    buf[group_end - 4:group_end - 2] = (0).to_bytes(2, "little")  # tranrf0 (unused by parser)
    buf[group_end - 2:group_end] = (0b1).to_bytes(2, "little")    # rowpf0: bit0 set -> row 0 present

    return bytes(buf)


def _build_pdb_file(track_page: bytes, *, num_tables: int = 1) -> bytes:
    """Page 0: file header + table pointer array (tracks table -> page 1).
    Page 1: the synthetic track page."""
    page0 = bytearray(PAGE_SIZE)
    page0[0:4] = b"\x00\x00\x00\x00"
    page0[4:8] = PAGE_SIZE.to_bytes(4, "little")
    page0[8:12] = num_tables.to_bytes(4, "little")
    page0[12:16] = (2).to_bytes(4, "little")  # next_unused_page

    # Table pointer array starts at _FILE_HEADER_SIZE (0x1c).
    entry = (
        TABLE_TYPE_TRACKS.to_bytes(4, "little")
        + (0).to_bytes(4, "little")   # empty_candidate, unused
        + (1).to_bytes(4, "little")   # first_page
        + (1).to_bytes(4, "little")   # last_page
    )
    page0[_FILE_HEADER_SIZE:_FILE_HEADER_SIZE + len(entry)] = entry

    return bytes(page0) + track_page


# ── String decoder ────────────────────────────────────────────────────────

def test_decode_short_ascii_string():
    blob = _short_string("Test Track")
    buf = b"\x00" * 10 + blob
    assert _decode_pdb_string(buf, 10) == "Test Track"


def test_decode_long_ascii_string():
    blob = _long_string("Long ASCII Title", utf16=False)
    buf = b"\x00" * 5 + blob
    assert _decode_pdb_string(buf, 5) == "Long ASCII Title"


def test_decode_long_utf16_string():
    blob = _long_string("Ünïcödé Title", utf16=True)
    buf = b"\x00" * 3 + blob
    assert _decode_pdb_string(buf, 3) == "Ünïcödé Title"


def test_decode_out_of_bounds_returns_none():
    assert _decode_pdb_string(b"\x00" * 4, 100) is None


# ── Full row walk, end to end ─────────────────────────────────────────────

def test_read_pdb_recovers_synthetic_track_row(tmp_path):
    page = _build_track_page(
        track_id=555,
        tempo_x100=12800,
        artist_id=0,
        album_id=0,
        key_id=0,
        title=_short_string("Test Track"),
        file_path=_long_string("/Contents/Music/Artist/Test Track.mp3"),
        anlz_path=_short_string("/PIONEER/USBANLZ/P000/00000001"),
    )
    pdb_path = tmp_path / "export.pdb"
    pdb_path.write_bytes(_build_pdb_file(page))

    report = read_pdb(pdb_path)

    assert report.valid_header is True
    assert report.partial is False, report.notes
    assert len(report.tracks) == 1

    track = report.tracks[0]
    assert track.track_id == 555
    assert track.bpm == 128.0
    assert track.title == "Test Track"
    assert track.drive_relative_path == "/Contents/Music/Artist/Test Track.mp3"
    assert track.anlz_folder_path == "/PIONEER/USBANLZ/P000/00000001"
    # Deliberately unresolved this phase — see module docstring.
    assert track.artist is None
    assert track.key is None


def test_read_pdb_skips_row_when_presence_bit_clear(tmp_path):
    page = bytearray(_build_track_page(
        track_id=1, tempo_x100=12000, artist_id=0, album_id=0, key_id=0,
        title=_short_string("X"), file_path=_short_string("Y"), anlz_path=_short_string("Z"),
    ))
    # Clear the presence bit for row 0 — the row must NOT be returned even
    # though its bytes are otherwise perfectly well-formed.
    group_end = PAGE_SIZE
    page[group_end - 2:group_end] = (0).to_bytes(2, "little")
    pdb_path = tmp_path / "export.pdb"
    pdb_path.write_bytes(_build_pdb_file(bytes(page)))

    report = read_pdb(pdb_path)

    assert report.partial is False
    assert report.tracks == []


def test_read_pdb_falls_back_gracefully_on_garbage_tracks_page(tmp_path):
    """A tracks-table page whose page_index/type is set but is otherwise
    garbage (row-count claims rows that don't decode to a valid subtype)
    must not crash read_pdb — it should just contribute zero rows for that
    page rather than raising."""
    page0 = bytearray(PAGE_SIZE)
    page0[0:4] = b"\x00\x00\x00\x00"
    page0[4:8] = PAGE_SIZE.to_bytes(4, "little")
    page0[8:12] = (1).to_bytes(4, "little")
    page0[12:16] = (2).to_bytes(4, "little")
    entry = (
        TABLE_TYPE_TRACKS.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
    )
    page0[_FILE_HEADER_SIZE:_FILE_HEADER_SIZE + len(entry)] = entry

    garbage_page = bytearray(PAGE_SIZE)
    garbage_page[0x08:0x0C] = TABLE_TYPE_TRACKS.to_bytes(4, "little")
    garbage_page[0x18:0x1B] = _pack_row_counts(num_row_offsets=1, num_rows=1)
    garbage_page[0x1B] = 0x24
    garbage_page[PAGE_SIZE - 2:PAGE_SIZE] = (1).to_bytes(2, "little")  # row 0 "present"
    # ofs[0] stays 0 -> row "starts" at 0x28, but its subtype bytes are
    # left as zero, not the expected 0x0024 -> _parse_track_row rejects it.

    pdb_path = tmp_path / "export.pdb"
    pdb_path.write_bytes(bytes(page0) + bytes(garbage_page))

    report = read_pdb(pdb_path)

    assert report.valid_header is True
    assert report.partial is False  # the walk itself succeeded; it just found 0 valid rows
    assert report.tracks == []


# ── Index pages: table roots that point to data pages, not rows directly ────
# Real Rekordbox-written export.pdb files use this for every table (confirmed
# against a genuine sample — see module docstring). The table pointer's
# first_page is often an INDEX page, not a data page.

def _build_index_page(*, page_index: int, table_type: int, entry_page_indices: list) -> bytes:
    """Build a synthetic index page whose entries point at entry_page_indices."""
    buf = bytearray(PAGE_SIZE)
    buf[0x04:0x08] = page_index.to_bytes(4, "little")
    buf[0x08:0x0C] = table_type.to_bytes(4, "little")
    buf[0x0C:0x10] = page_index.to_bytes(4, "little")  # next_page == self (single-page chain)
    buf[0x1B] = 0x64  # index page flag (bit6 set) — matches real hardware value exactly

    num_entries = len(entry_page_indices)
    buf[0x38:0x3A] = num_entries.to_bytes(2, "little")
    for i, target_page in enumerate(entry_page_indices):
        entry_val = (target_page << 3)  # flags = 0
        p = 0x3C + i * 4
        buf[p:p + 4] = entry_val.to_bytes(4, "little")
    return bytes(buf)


def test_read_pdb_skips_index_page_but_reads_data_pages_reachable_via_chain(tmp_path):
    """
    Real-hardware-corrected model: a 13MB real Rekordbox export.pdb proved
    that an index page's entries are a redundant lookup shortcut, not the
    only path to the data — every one of that file's index entries pointed
    at a page ALSO directly reachable via the table's own next_page chain.
    An earlier version of this walker followed index entries in addition to
    the chain and silently double-extracted every such row (991 rows found
    for a table that actually had 581 unique tracks in that sample).

    This fixture reproduces the real shape: first_page is an index page
    (contributing 0 rows), last_page is a data page reachable purely by
    following next_page — no entry-following required or performed.
    """
    index_page = _build_index_page(
        page_index=1, table_type=TABLE_TYPE_TRACKS, entry_page_indices=[2],
    )
    # Index page's next_page must point at the data page for the chain walk
    # to reach it (entries are NOT followed — see module HONESTY NOTE).
    index_page = bytearray(index_page)
    index_page[0x0C:0x10] = (2).to_bytes(4, "little")
    index_page = bytes(index_page)

    data_page = _build_track_page(
        page_index=2,
        track_id=42,
        tempo_x100=13000,
        artist_id=1,
        album_id=1,
        key_id=1,
        title=_short_string("Chained Track"),
        file_path=_short_string("/Contents/Chained Track.mp3"),
        anlz_path=_short_string("/PIONEER/USBANLZ/P000/00000002"),
    )

    page0 = bytearray(PAGE_SIZE)
    page0[0:4] = b"\x00\x00\x00\x00"
    page0[4:8] = PAGE_SIZE.to_bytes(4, "little")
    page0[8:12] = (1).to_bytes(4, "little")
    page0[12:16] = (3).to_bytes(4, "little")
    entry = (
        TABLE_TYPE_TRACKS.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")  # first_page: the index page
        + (2).to_bytes(4, "little")  # last_page: the chain extends to the data page
    )
    page0[_FILE_HEADER_SIZE:_FILE_HEADER_SIZE + len(entry)] = entry

    pdb_path = tmp_path / "export.pdb"
    pdb_path.write_bytes(bytes(page0) + index_page + data_page)

    report = read_pdb(pdb_path)

    assert report.partial is False
    assert len(report.tracks) == 1  # exactly one — no double-count via the index page's entries
    assert report.tracks[0].track_id == 42
    assert report.tracks[0].title == "Chained Track"


def test_read_pdb_handles_empty_index_page_like_real_hardware_sample(tmp_path):
    """Mirrors the actual real-hardware finding: an index page with
    num_entries=0 must resolve to zero tracks, not raise or hang."""
    index_page = _build_index_page(
        page_index=1, table_type=TABLE_TYPE_TRACKS, entry_page_indices=[],
    )
    page0 = bytearray(PAGE_SIZE)
    page0[0:4] = b"\x00\x00\x00\x00"
    page0[4:8] = PAGE_SIZE.to_bytes(4, "little")
    page0[8:12] = (1).to_bytes(4, "little")
    page0[12:16] = (2).to_bytes(4, "little")
    entry = (
        TABLE_TYPE_TRACKS.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
    )
    page0[_FILE_HEADER_SIZE:_FILE_HEADER_SIZE + len(entry)] = entry

    pdb_path = tmp_path / "export.pdb"
    pdb_path.write_bytes(bytes(page0) + index_page)

    report = read_pdb(pdb_path)

    assert report.partial is False
    assert report.tracks == []
