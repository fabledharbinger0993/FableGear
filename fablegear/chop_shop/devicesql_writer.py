# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-10-20
"""
fablegear / chop_shop/devicesql_writer.py

Writer for the DeviceSQL ``export.pdb`` / ``exportExt.pdb`` Device Library that a CDJ-3000 on
firmware 3.2x reads from a USB stick (it ignores OneLibrary's ``exportLibrary.db``; hardware-tested
2026-10-11, see dev/docs/devicesql_export_writer.md). The read side is ``devicesql_reader.py``,
which stays read-only.

Two layers:

1. **Page images** (``parse_image`` / ``serialise_image``): every page of a real file as a model
   (header fields, row slots with presence and transaction flags, row bytes, index-page entries).
   Derived fields -- the packed row counts, ``free_size``, ``used_size``, the row-group bitmasks --
   are recomputed on serialise, not copied. Round-tripping a real Rekordbox file reproduces it
   byte for byte (tests/test_devicesql_writer.py), which is the check that our page layout rules
   are the ones Rekordbox uses.
2. **A fresh writer** (``build_pdb``, the row encoders, ``write_device_library``): lays out tables
   the way the untouched Rekordbox export in tests/fixtures/rekordbox_ground_truth_export does --
   one index page and one first data page per table (pages 2i+1, 2i+2), further pages appended,
   rows packed while the page has room, a zeroed "empty candidate" page linked after each table's
   last data page -- and encodes rows from a ``DeviceLibrary``.

LAYOUT RULES, each DEMONSTRATED on the two committed Rekordbox exports unless marked:
  * Data page header: 0x00 zero, 0x04 page index, 0x08 table type, 0x0C next page, 0x10 sequence,
    0x14 zero, 0x18 three bytes (low 13 bits = row offsets allocated, high 11 bits = rows present),
    0x1B flags (0x24 data, 0x34 data with deleted rows, 0x64 index), 0x1C free_size, 0x1E used_size,
    0x20 transaction row count, 0x22 transaction first row, 0x24 and 0x26 zero.
  * Rows start at 0x28. Row offsets are relative to 0x28 and every row is 4-byte aligned.
  * Row index: 36-byte groups of 16 from the page end; within a group, offsets in reverse order,
    then the presence bitmask, then the last-transaction bitmask.
  * free_size = page_size - 0x28 - used_size - (2 * offsets + 4 * groups): the index occupies only
    the slots in use, and the heap may grow into the unused part of the last group.
  * Index page: header as above with flags 0x64, then at 0x20: u16 0x1FFF, u16 0x1FFF, u16 capacity
    (1004 entries for 4 KiB pages), u16 next entry offset, u32 page index, u32 first data page (or
    0x03FFFFFF when the table is empty), u32 0x03FFFFFF, u32 0, u16 entry count, u16 first empty
    slot (0x1FFF = none), then ``capacity`` u32 entries (empty = 0x1FFFFFF8) and zero padding.
    Rekordbox's own multi-page genre/artist/album/label/artwork tables carry zero entries, so the
    fresh writer writes none.
  * index_shift (bytes 2-3 of track, artist, album, history and My Tag rows) = row slot * 0x20.
  * The file ends after the last non-blank page; ``next_unused_page`` counts the trailing
    empty-candidate page(s) too.
  * Strings: short ASCII ``((len+1) << 1) | 1`` + bytes (<= 126 chars); long ASCII ``0x40``, u16
    total length, 0x00, bytes; anything else ``0x90``, u16 total length, 0x00, UTF-16LE.

UNKNOWN, written as the Rekordbox capture the deck loaded has them (never guessed): track-row u32 at
0x04 (0x000C0700), u16 pair at 0x18 (per export; the capture's values), u16 at 0x56 (0x29), u16 at
0x5C (3), string slots 2/3/4 (small numbers; the capture's "2"/"3"/"" for freshly analysed tracks
with no cues), the history row's "1000", and file header u32 at 0x10.

PROVENANCE: field names follow Deep Symmetry's "DJ Link Ecosystem Analysis" export documentation
(https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html), corrected where the
committed captures disagree (the track string offsets start at 0x5E, not 0x64). No code is copied
from rekordcrate or crate-digger.

HONESTY LIMIT: a file that this module's reader and serialiser agree on is not proof that a deck
loads it. The acceptance test is a stick written only by FableGear listing, playing and showing
Iron's key and BPM on the owner's CDJ-3000 (dev/docs/devicesql_export_writer.md section 4).
"""

from __future__ import annotations

import os
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:
    from . import devicesql_canned as canned
    from . import devicesql_reader as reader
except ImportError:  # imported with chop_shop/ itself on sys.path, as some callers do
    import devicesql_canned as canned  # type: ignore[no-redef]
    import devicesql_reader as reader  # type: ignore[no-redef]

PAGE_SIZE = 4096

_DATA_HEADER = 0x28
_GROUP_ROWS = 16
_GROUP_BYTES = _GROUP_ROWS * 2 + 4
_FLAG_DATA = 0x24
_FLAG_DELETED = 0x10
_FLAG_INDEX = 0x64
_NO_PAGE = 0x03FFFFFF
_INDEX_EMPTY = 0x1FFFFFF8
_INDEX_ENTRIES = 0x3C
_INDEX_TAIL = 20  # zero bytes after the entry array


def index_capacity(page_size: int) -> int:
    return (page_size - _INDEX_ENTRIES - _INDEX_TAIL) // 4


# ─── Strings ────────────────────────────────────────────────────────────────

def encode_string(value: str | None) -> bytes:
    """DeviceSQL string bytes for ``value`` (None and "" both give the one-byte empty string)."""
    s = value or ""
    if s.isascii():
        data = s.encode("ascii")
        if len(data) <= 126:
            return bytes([((len(data) + 1) << 1) | 1]) + data
        return bytes([0x40]) + (len(data) + 4).to_bytes(2, "little") + b"\x00" + data
    data = s.encode("utf-16-le")
    return bytes([0x90]) + (len(data) + 4).to_bytes(2, "little") + b"\x00" + data


def encode_isrc(value: str | None) -> bytes:
    """The ISRC slot's own form: 0x90, u16 total length, 0x00, then 0x03, the ASCII code and a NUL
    (Deep Symmetry; matches every ISRC in both captures). Empty is the plain empty string."""
    code = (value or "").strip()
    if not code or not code.isascii():
        return b"\x03"
    body = b"\x03" + code.encode("ascii") + b"\x00"
    return bytes([0x90]) + (len(body) + 4).to_bytes(2, "little") + b"\x00" + body


def _pad4(b: bytes | bytearray) -> bytes:
    return bytes(b) + b"\x00" * (-len(b) % 4)


def _place_string(row: bytearray, value: str | None, *, enc: bytes | None = None) -> int:
    """Append ``value`` (or pre-encoded ``enc``) to ``row`` and return its offset. Long strings
    (0x40/0x90) start on a 4-byte boundary of the row, zero-padded, as in every Rekordbox row
    examined; short strings do not."""
    enc = encode_string(value) if enc is None else enc
    if not enc[0] & 1:
        row += b"\x00" * (-len(row) % 4)
    at = len(row)
    row += enc
    return at


# ─── Page images (round trip) ──────────────────────────────────────────────

@dataclass
class TablePointer:
    table_type: int
    empty_candidate: int
    first_page: int
    last_page: int


@dataclass
class RowSlot:
    offset: int
    present: bool
    touched: bool  # bit in the last-transaction mask


@dataclass
class DataPage:
    index: int
    table_type: int
    next_page: int
    seq: int
    flags: int
    slots: list[RowSlot]
    rows: dict[int, bytes]  # heap offset -> row bytes (to the next row, or to used_size)
    tran_count: int
    tran_index: int
    unknown14: int = 0
    unknown24: int = 0
    unknown26: int = 0


@dataclass
class IndexPage:
    index: int
    table_type: int
    next_page: int
    seq: int
    first_data_page: int
    entries: list[int]  # all `capacity` u32 slots, empties included
    num_entries: int
    first_empty: int
    next_offset: int
    unknown_a: int = 0x1FFF
    unknown_b: int = 0x1FFF
    unknown30: int = _NO_PAGE
    unknown34: int = 0
    unknown14: int = 0


@dataclass
class PdbImage:
    page_size: int
    next_unused_page: int
    header_unknown_10: int
    seqdb: int
    header_unknown_18: int
    tables: list[TablePointer]
    pages: dict[int, DataPage | IndexPage]
    pages_in_file: int
    # Anything the model does not explain is carried verbatim and listed here, so it is never
    # silently dropped: (page index, description).
    raw_pages: dict[int, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _u16(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 2], "little")


def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "little")


def _parse_data_page(pi: int, pg: bytes, ps: int) -> tuple[DataPage, list[str]]:
    notes: list[str] = []
    counts = int.from_bytes(pg[0x18:0x1B], "little")
    n_offsets = counts & 0x1FFF
    slots: list[RowSlot] = []
    for g in range((n_offsets + _GROUP_ROWS - 1) // _GROUP_ROWS):
        gs = ps - (g + 1) * _GROUP_BYTES
        pf, tf = _u16(pg, gs + 32), _u16(pg, gs + 34)
        for li in range(_GROUP_ROWS):
            if g * _GROUP_ROWS + li >= n_offsets:
                break
            slots.append(RowSlot(_u16(pg, gs + 2 * (_GROUP_ROWS - 1 - li)), bool(pf >> li & 1), bool(tf >> li & 1)))
    used = _u16(pg, 0x1E)
    offsets = sorted({s.offset for s in slots})
    rows: dict[int, bytes] = {}
    for i, o in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else used
        rows[o] = pg[_DATA_HEADER + o:_DATA_HEADER + end]
    page = DataPage(
        index=_u32(pg, 4), table_type=_u32(pg, 8), next_page=_u32(pg, 0x0C), seq=_u32(pg, 0x10),
        flags=pg[0x1B], slots=slots, rows=rows, tran_count=_u16(pg, 0x20), tran_index=_u16(pg, 0x22),
        unknown14=_u32(pg, 0x14), unknown24=_u16(pg, 0x24), unknown26=_u16(pg, 0x26),
    )
    if page.index != pi:
        notes.append(f"page {pi}: header page_index {page.index}")
    if counts >> 13 != sum(s.present for s in slots):
        notes.append(f"page {pi}: rows-present field {counts >> 13} != presence bits {sum(s.present for s in slots)}")
    return page, notes


def _parse_index_page(pg: bytes, ps: int) -> IndexPage:
    cap = _u16(pg, 0x24)
    return IndexPage(
        index=_u32(pg, 4), table_type=_u32(pg, 8), next_page=_u32(pg, 0x0C), seq=_u32(pg, 0x10),
        first_data_page=_u32(pg, 0x2C), entries=[_u32(pg, _INDEX_ENTRIES + 4 * i) for i in range(cap)],
        num_entries=_u16(pg, 0x38), first_empty=_u16(pg, 0x3A), next_offset=_u16(pg, 0x26),
        unknown_a=_u16(pg, 0x20), unknown_b=_u16(pg, 0x22), unknown30=_u32(pg, 0x30),
        unknown34=_u32(pg, 0x34), unknown14=_u32(pg, 0x14),
    )


def parse_image(data: bytes) -> PdbImage:
    """Model every page of a DeviceSQL file. Raises ValueError on a file that is not DeviceSQL."""
    if len(data) < 0x1C or data[:4] != b"\x00\x00\x00\x00":
        raise ValueError("not a DeviceSQL file")
    ps, n_tables = _u32(data, 4), _u32(data, 8)
    if ps not in (512, 1024, 2048, 4096, 8192, 16384) or not 0 < n_tables <= 64 or len(data) % ps:
        raise ValueError("implausible DeviceSQL header")
    tables = []
    for i in range(n_tables):
        o = 0x1C + 16 * i
        tables.append(TablePointer(_u32(data, o), _u32(data, o + 4), _u32(data, o + 8), _u32(data, o + 12)))
    img = PdbImage(page_size=ps, next_unused_page=_u32(data, 0x0C), header_unknown_10=_u32(data, 0x10),
                   seqdb=_u32(data, 0x14), header_unknown_18=_u32(data, 0x18), tables=tables, pages={},
                   pages_in_file=len(data) // ps)
    if any(data[0x1C + 16 * n_tables:ps]):
        img.raw_pages[0] = data[:ps]
        img.notes.append("page 0: bytes after the table pointers are not zero; carried verbatim")
    for pi in range(1, img.pages_in_file):
        pg = data[pi * ps:(pi + 1) * ps]
        if not any(pg):
            continue  # blank page (an empty candidate, or never used)
        if pg[0x1B] & 0x40:
            img.pages[pi] = _parse_index_page(pg, ps)
        else:
            page, notes = _parse_data_page(pi, pg, ps)
            img.pages[pi] = page
            img.notes += notes
    # Anything a serialise would not reproduce is carried as a raw page and named.
    for pi, page in list(img.pages.items()):
        if _serialise_page(page, ps) != data[pi * ps:(pi + 1) * ps]:
            img.raw_pages[pi] = data[pi * ps:(pi + 1) * ps]
            img.notes.append(f"page {pi}: not reproduced by the page model; carried verbatim")
    return img


def _serialise_data_page(p: DataPage, ps: int) -> bytes:
    buf = bytearray(ps)
    used = max((o + len(r) for o, r in p.rows.items()), default=0)
    for o, r in p.rows.items():
        buf[_DATA_HEADER + o:_DATA_HEADER + o + len(r)] = r
    n = len(p.slots)
    groups = (n + _GROUP_ROWS - 1) // _GROUP_ROWS
    for g in range(groups):
        gs = ps - (g + 1) * _GROUP_BYTES
        pf = tf = 0
        for li in range(_GROUP_ROWS):
            i = g * _GROUP_ROWS + li
            if i >= n:
                break
            s = p.slots[i]
            pos = gs + 2 * (_GROUP_ROWS - 1 - li)
            buf[pos:pos + 2] = s.offset.to_bytes(2, "little")
            pf |= int(s.present) << li
            tf |= int(s.touched) << li
        buf[gs + 32:gs + 34] = pf.to_bytes(2, "little")
        buf[gs + 34:gs + 36] = tf.to_bytes(2, "little")
    free = ps - _DATA_HEADER - used - (2 * n + 4 * groups)
    present = sum(s.present for s in p.slots)
    buf[4:8] = p.index.to_bytes(4, "little")
    buf[8:12] = p.table_type.to_bytes(4, "little")
    buf[0x0C:0x10] = p.next_page.to_bytes(4, "little")
    buf[0x10:0x14] = p.seq.to_bytes(4, "little")
    buf[0x14:0x18] = p.unknown14.to_bytes(4, "little")
    buf[0x18:0x1B] = (n | present << 13).to_bytes(3, "little")
    buf[0x1B] = p.flags
    buf[0x1C:0x1E] = max(free, 0).to_bytes(2, "little")
    buf[0x1E:0x20] = used.to_bytes(2, "little")
    buf[0x20:0x22] = p.tran_count.to_bytes(2, "little")
    buf[0x22:0x24] = p.tran_index.to_bytes(2, "little")
    buf[0x24:0x26] = p.unknown24.to_bytes(2, "little")
    buf[0x26:0x28] = p.unknown26.to_bytes(2, "little")
    return bytes(buf)


def _serialise_index_page(p: IndexPage, ps: int) -> bytes:
    buf = bytearray(ps)
    buf[4:8] = p.index.to_bytes(4, "little")
    buf[8:12] = p.table_type.to_bytes(4, "little")
    buf[0x0C:0x10] = p.next_page.to_bytes(4, "little")
    buf[0x10:0x14] = p.seq.to_bytes(4, "little")
    buf[0x14:0x18] = p.unknown14.to_bytes(4, "little")
    buf[0x1B] = _FLAG_INDEX
    buf[0x20:0x22] = p.unknown_a.to_bytes(2, "little")
    buf[0x22:0x24] = p.unknown_b.to_bytes(2, "little")
    buf[0x24:0x26] = len(p.entries).to_bytes(2, "little")
    buf[0x26:0x28] = p.next_offset.to_bytes(2, "little")
    buf[0x28:0x2C] = p.index.to_bytes(4, "little")
    buf[0x2C:0x30] = p.first_data_page.to_bytes(4, "little")
    buf[0x30:0x34] = p.unknown30.to_bytes(4, "little")
    buf[0x34:0x38] = p.unknown34.to_bytes(4, "little")
    buf[0x38:0x3A] = p.num_entries.to_bytes(2, "little")
    buf[0x3A:0x3C] = p.first_empty.to_bytes(2, "little")
    for i, e in enumerate(p.entries):
        buf[_INDEX_ENTRIES + 4 * i:_INDEX_ENTRIES + 4 * i + 4] = e.to_bytes(4, "little")
    return bytes(buf)


def _serialise_page(p: DataPage | IndexPage, ps: int) -> bytes:
    return _serialise_index_page(p, ps) if isinstance(p, IndexPage) else _serialise_data_page(p, ps)


def serialise_image(img: PdbImage) -> bytes:
    ps = img.page_size
    out = bytearray(img.pages_in_file * ps)
    hdr = bytearray(ps)
    hdr[4:8] = ps.to_bytes(4, "little")
    hdr[8:12] = len(img.tables).to_bytes(4, "little")
    hdr[0x0C:0x10] = img.next_unused_page.to_bytes(4, "little")
    hdr[0x10:0x14] = img.header_unknown_10.to_bytes(4, "little")
    hdr[0x14:0x18] = img.seqdb.to_bytes(4, "little")
    hdr[0x18:0x1C] = img.header_unknown_18.to_bytes(4, "little")
    for i, t in enumerate(img.tables):
        o = 0x1C + 16 * i
        hdr[o:o + 16] = (t.table_type.to_bytes(4, "little") + t.empty_candidate.to_bytes(4, "little")
                         + t.first_page.to_bytes(4, "little") + t.last_page.to_bytes(4, "little"))
    out[0:ps] = img.raw_pages.get(0, bytes(hdr))
    for pi, page in img.pages.items():
        out[pi * ps:(pi + 1) * ps] = img.raw_pages.get(pi) or _serialise_page(page, ps)
    return bytes(out)


# ─── Fresh layout ───────────────────────────────────────────────────────────

# Table types whose rows start with subtype:u16, index_shift:u16 (index_shift = slot * 0x20).
_SHIFTED_EXPORT = frozenset({0x00, 0x02, 0x03, 0x13})
_SHIFTED_EXT = frozenset({0x03})
EXPORT_TABLE_TYPES = tuple(range(0x14))
EXT_TABLE_TYPES = tuple(range(9))
# Order in which tables are filled. Only page numbering depends on it; tracks go last so that, as
# in both Rekordbox captures, the tracks table's empty candidate is the final (unwritten) page.
_FILL_ORDER = (0x13, 0x06, 0x10, 0x11, 0x12, 0x07, 0x01, 0x02, 0x03, 0x04, 0x05, 0x0D, 0x08, 0x00)


def _index_bytes(n_offsets: int) -> int:
    return 2 * n_offsets + 4 * ((n_offsets + _GROUP_ROWS - 1) // _GROUP_ROWS)


def _pack(rows: Sequence[bytes], ps: int) -> list[list[bytes]]:
    """Split rows into pages, in order, filling each page while the row and its index slot fit."""
    pages: list[list[bytes]] = []
    cur: list[bytes] = []
    used = 0
    for r in rows:
        r = _pad4(r)
        if len(r) + _DATA_HEADER + _index_bytes(1) > ps:
            raise ValueError(f"row of {len(r)} bytes does not fit in a {ps}-byte page")
        if cur and _DATA_HEADER + used + len(r) + _index_bytes(len(cur) + 1) > ps:
            pages.append(cur)
            cur, used = [], 0
        cur.append(r)
        used += len(r)
    if cur:
        pages.append(cur)
    return pages


def _with_index_shift(row: bytes, slot: int) -> bytes:
    return row[:2] + ((slot * 0x20) & 0xFFFF).to_bytes(2, "little") + row[4:]


def build_image(
    tables: Mapping[int, Sequence[bytes]],
    *,
    table_types: Sequence[int],
    shifted: frozenset[int],
    header_unknown_10: int,
    page_size: int = PAGE_SIZE,
) -> PdbImage:
    """A fresh page image: ``tables`` maps table type -> rows (encoded, in insertion order)."""
    ps = page_size
    n = len(table_types)
    pointer = {t: TablePointer(t, 0, 2 * i + 1, 2 * i + 1) for i, t in enumerate(table_types)}
    pages: dict[int, DataPage | IndexPage] = {}
    next_free = 2 * n + 1
    seq = 1

    def alloc() -> int:
        nonlocal next_free
        next_free += 1
        return next_free - 1

    order = [t for t in _FILL_ORDER if t in pointer] + [t for t in table_types if t not in _FILL_ORDER]
    first_data: dict[int, int] = {}
    for t in order:
        i = table_types.index(t)
        rows = list(tables.get(t, ()))
        if not rows:
            pointer[t].empty_candidate = 2 * i + 2
            continue
        chunks = _pack(rows, ps)
        page_ids = [2 * i + 2] + [alloc() for _ in chunks[1:]]
        candidate = alloc()
        first_data[t] = page_ids[0]
        for k, (pid, chunk) in enumerate(zip(page_ids, chunks, strict=True)):
            seq += 1
            body: dict[int, bytes] = {}
            slots: list[RowSlot] = []
            off = 0
            for s, r in enumerate(chunk):
                r = _with_index_shift(r, s) if t in shifted else r
                body[off] = r
                slots.append(RowSlot(off, True, True))
                off += len(r)
            nxt = page_ids[k + 1] if k + 1 < len(page_ids) else candidate
            pages[pid] = DataPage(index=pid, table_type=t, next_page=nxt, seq=seq, flags=_FLAG_DATA,
                                  slots=slots, rows=body, tran_count=len(chunk), tran_index=0)
        pointer[t].last_page = page_ids[-1]
        pointer[t].empty_candidate = candidate

    cap = index_capacity(ps)
    for i, t in enumerate(table_types):
        pid = 2 * i + 1
        pages[pid] = IndexPage(
            index=pid, table_type=t, next_page=first_data.get(t, 2 * i + 2), seq=1,
            first_data_page=first_data.get(t, _NO_PAGE), entries=[_INDEX_EMPTY] * cap,
            num_entries=0, first_empty=0x1FFF, next_offset=0,
        )

    last_written = max(pages)
    return PdbImage(page_size=ps, next_unused_page=next_free, header_unknown_10=header_unknown_10,
                    seqdb=seq + 1, header_unknown_18=0, tables=[pointer[t] for t in table_types],
                    pages=pages, pages_in_file=last_written + 1)


# ─── Device library model and row encoders ─────────────────────────────────

# Track colour names -> the fixed colour table's ids (canned.EXPORT_COLORS; 0 = none).
COLOR_IDS = {"pink": 1, "red": 2, "orange": 3, "yellow": 4, "green": 5, "aqua": 6, "blue": 7, "purple": 8}

# Pioneer file-type codes (same values as the OneLibrary writer and pyrekordbox's FileType).
FILE_TYPES = {"mp3": 1, "m4a": 4, "mp4": 4, "flac": 5, "wav": 11, "aiff": 12, "aif": 12}

# Track-row constants copied from the Rekordbox capture the deck loaded (see module docstring).
_TRACK_U04 = 0x000C0700
_TRACK_U18, _TRACK_U1A = 0x6836, 0xCBF4
_TRACK_U56 = 0x29
_TRACK_U5C = 3
_TRACK_STR2, _TRACK_STR3, _TRACK_STR4 = "2", "3", ""
_STRINGS_AT = 0x5E
_N_STRINGS = 21


@dataclass
class DeviceTrack:
    id: int
    title: str
    file_path: str  # drive-relative, "/Contents/..."
    analyze_path: str  # "/PIONEER/USBANLZ/.../ANLZ0000.DAT"
    bpm: float | None = None
    key_id: int = 0
    artist_id: int = 0
    composer_id: int = 0
    remixer_id: int = 0
    original_artist_id: int = 0
    album_id: int = 0
    genre_id: int = 0
    label_id: int = 0
    color_id: int = 0
    artwork_id: int = 0
    rating: int = 0
    file_size: int = 0
    sample_rate: int = 0
    bitrate: int = 0
    bit_depth: int = 0
    duration: int = 0
    track_number: int = 0
    disc_number: int = 0
    year: int = 0
    play_count: int = 0
    file_type: int = 0
    comment: str = ""
    date_added: str = ""
    analyze_date: str = ""
    release_date: str = ""
    mix_name: str = ""
    isrc: str = ""


@dataclass
class DevicePlaylist:
    id: int
    parent_id: int
    name: str
    is_folder: bool
    sort_order: int
    track_ids: list[int] = field(default_factory=list)


@dataclass
class DeviceLibrary:
    """Everything the Device Library needs, with ids already allocated (shared with OneLibrary)."""
    tracks: list[DeviceTrack] = field(default_factory=list)
    artists: dict[int, str] = field(default_factory=dict)
    albums: dict[int, tuple[str, int]] = field(default_factory=dict)  # id -> (name, artist_id)
    genres: dict[int, str] = field(default_factory=dict)
    labels: dict[int, str] = field(default_factory=dict)
    keys: dict[int, str] = field(default_factory=dict)
    playlists: list[DevicePlaylist] = field(default_factory=list)
    export_date: str = ""


def _u32b(v: int) -> bytes:
    return (int(v) & 0xFFFFFFFF).to_bytes(4, "little")


def _u16b(v: int) -> bytes:
    return (max(0, min(int(v), 0xFFFF))).to_bytes(2, "little")


def _track_u14(t: DeviceTrack) -> int:
    """Track-row u32 at 0x14: unknown in every source (varies per track, looks like an id or hash).
    A stable 28-bit hash of the drive path keeps it deterministic and in the observed range."""
    return zlib.crc32(t.file_path.encode("utf-8")) & 0x0FFFFFFF


def encode_track_row(t: DeviceTrack) -> bytes:
    filename = t.file_path.rsplit("/", 1)[-1]
    strings = [
        "", "", _TRACK_STR2, _TRACK_STR3, _TRACK_STR4, "", "ON", "ON", "", "",
        t.date_added, t.release_date, t.mix_name, "", t.analyze_path, t.analyze_date,
        t.comment, t.title, "", filename, t.file_path,
    ]
    head = b"".join([
        _u16b(0x24), _u16b(0), _u32b(_TRACK_U04), _u32b(t.sample_rate), _u32b(t.composer_id), _u32b(t.file_size),
        _u32b(_track_u14(t)), _u16b(_TRACK_U18), _u16b(_TRACK_U1A), _u32b(t.artwork_id), _u32b(t.key_id),
        _u32b(t.original_artist_id), _u32b(t.label_id), _u32b(t.remixer_id), _u32b(t.bitrate), _u32b(t.track_number),
        _u32b(round(t.bpm * 100) if t.bpm else 0), _u32b(t.genre_id), _u32b(t.album_id),
        _u32b(t.artist_id), _u32b(t.id), _u16b(t.disc_number), _u16b(t.play_count), _u16b(t.year),
        _u16b(t.bit_depth), _u16b(t.duration), _u16b(_TRACK_U56), bytes([t.color_id & 0xFF]),
        bytes([max(0, min(t.rating, 5))]), _u16b(t.file_type), _u16b(_TRACK_U5C),
    ])
    assert len(head) == _STRINGS_AT
    row = bytearray(head) + bytes(2 * _N_STRINGS)
    for i, s in enumerate(strings):
        at = _place_string(row, s, enc=encode_isrc(t.isrc) if i == 0 else None)
        if at > 0xFFFF:
            raise ValueError(f"track {t.id}: strings too long for a DeviceSQL row")
        row[_STRINGS_AT + 2 * i:_STRINGS_AT + 2 * i + 2] = _u16b(at)
    return _pad4(row)


def _near_name_row(head: bytes, name: str) -> bytes:
    """Artist/album row: ``head`` then 0x03, a one-byte name offset, and the name. Rekordbox's trailing
    zero padding: after a short name, align(end + 6) -- all 297 artist/album rows with short names in
    the ground-truth export; after a long name, align(end + 4) -- its only 2 such rows, both ending
    4-aligned, so the long rule is thinly evidenced."""
    row = bytearray(head) + b"\x03\x00"
    at = _place_string(row, name)
    row[len(head) + 1] = at
    return _pad4(row + b"\x00" * (6 if row[at] & 1 else 4))


def encode_artist_row(artist_id: int, name: str) -> bytes:
    return _near_name_row(_u16b(0x60) + _u16b(0) + _u32b(artist_id), name)


def encode_album_row(album_id: int, name: str, artist_id: int) -> bytes:
    return _near_name_row(_u16b(0x80) + _u16b(0) + _u32b(0) + _u32b(artist_id) + _u32b(album_id) + _u32b(0), name)


def encode_id_name_row(row_id: int, name: str) -> bytes:
    """Genre and label rows."""
    return _pad4(_u32b(row_id) + encode_string(name))


def encode_key_row(key_id: int, name: str) -> bytes:
    return _pad4(_u32b(key_id) + _u32b(key_id) + encode_string(name))


def encode_playlist_tree_row(p: DevicePlaylist) -> bytes:
    return _pad4(_u32b(p.parent_id) + _u32b(0) + _u32b(p.sort_order) + _u32b(p.id)
                 + _u32b(1 if p.is_folder else 0) + encode_string(p.name))


def encode_playlist_entry_row(entry_index: int, track_id: int, playlist_id: int) -> bytes:
    return _u32b(entry_index) + _u32b(track_id) + _u32b(playlist_id)


def encode_history_row(num_tracks: int, export_date: str) -> bytes:
    """The one history row Rekordbox writes: track count, export date, then "1000" and "" with
    one-byte offsets to them (layout DEMONSTRATED on both captures; "1000" is unknown)."""
    d = encode_string(export_date)
    head = _u16b(0x0280) + _u16b(0) + _u32b(num_tracks) + _u32b(0) + d
    a = len(head) + 2
    b = a + len(encode_string("1000"))
    return _pad4(head + bytes([a, b]) + encode_string("1000") + encode_string("") + b"\x00" * 6)


def build_export_tables(lib: DeviceLibrary) -> dict[int, list[bytes]]:
    tables: dict[int, list[bytes]] = {
        0x00: [encode_track_row(t) for t in lib.tracks],
        0x01: [encode_id_name_row(i, n) for i, n in sorted(lib.genres.items())],
        0x02: [encode_artist_row(i, n) for i, n in sorted(lib.artists.items())],
        0x03: [encode_album_row(i, n, a) for i, (n, a) in sorted(lib.albums.items())],
        0x04: [encode_id_name_row(i, n) for i, n in sorted(lib.labels.items())],
        0x05: [encode_key_row(i, n) for i, n in sorted(lib.keys.items())],
        0x06: list(canned.EXPORT_COLORS),
        0x07: [encode_playlist_tree_row(p) for p in lib.playlists],
        0x08: [encode_playlist_entry_row(k, tid, p.id)
               for p in lib.playlists if not p.is_folder for k, tid in enumerate(p.track_ids, 1)],
        0x10: list(canned.EXPORT_COLUMNS),
        0x11: list(canned.EXPORT_MENU_0x11),
        0x12: list(canned.EXPORT_TABLE_0x12),
        0x13: [encode_history_row(len(lib.tracks), lib.export_date or date.today().isoformat())],
    }
    return tables


def build_export_pdb(lib: DeviceLibrary) -> bytes:
    img = build_image(build_export_tables(lib), table_types=EXPORT_TABLE_TYPES, shifted=_SHIFTED_EXPORT,
                      header_unknown_10=4)
    return serialise_image(img)


def build_export_ext_pdb() -> bytes:
    tables = {0x03: list(canned.EXT_MY_TAGS), 0x07: list(canned.EXT_TABLE_0x07)}
    img = build_image(tables, table_types=EXT_TABLE_TYPES, shifted=_SHIFTED_EXT, header_unknown_10=1)
    return serialise_image(img)


@dataclass
class DeviceLibraryWriteResult:
    export_pdb: str = ""
    export_ext_pdb: str = ""
    tracks_written: int = 0
    playlists_written: int = 0
    errors: list[str] = field(default_factory=list)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".fgtmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_device_library(rekordbox_dir: Path, lib: DeviceLibrary, *, overwrite: bool = False) -> DeviceLibraryWriteResult:
    """Write ``export.pdb`` and ``exportExt.pdb`` into ``rekordbox_dir`` (``<drive>/PIONEER/rekordbox``).

    Refuses to replace existing files unless ``overwrite`` is set, like the OneLibrary writer.
    Every file is built in memory, re-read with ``devicesql_reader`` and checked before it lands.
    """
    rekordbox_dir = Path(rekordbox_dir)
    result = DeviceLibraryWriteResult()
    targets = {"export.pdb": build_export_pdb(lib), "exportExt.pdb": build_export_ext_pdb()}
    for name in targets:
        if (rekordbox_dir / name).exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file: {rekordbox_dir / name}")
    # Verified in a local temp folder, so a failure leaves nothing behind on the stick.
    with tempfile.TemporaryDirectory(prefix="fg_pdb_verify_") as verify_dir:
        for name, data in targets.items():
            probe = Path(verify_dir) / name
            probe.write_bytes(data)
            rep = reader.read_pdb(probe)
            if not rep.valid_header or rep.partial:
                raise ValueError(f"{name}: written bytes do not read back ({rep.detail}; {rep.notes})")
            if name == "export.pdb":
                _check_export_readback(rep, lib)

    rekordbox_dir.mkdir(parents=True, exist_ok=True)
    for name, data in targets.items():
        _atomic_write(rekordbox_dir / name, data)
    result.export_pdb = str(rekordbox_dir / "export.pdb")
    result.export_ext_pdb = str(rekordbox_dir / "exportExt.pdb")
    result.tracks_written = len(lib.tracks)
    result.playlists_written = sum(not p.is_folder for p in lib.playlists)
    return result


def _check_export_readback(rep: reader.PdbReport, lib: DeviceLibrary) -> None:
    got = {t.track_id: t for t in rep.tracks}
    if len(got) != len(lib.tracks):
        raise ValueError(f"export.pdb: {len(got)} track rows read back, {len(lib.tracks)} written")
    for t in lib.tracks:
        r = got.get(t.id)
        if r is None or r.title != t.title or r.drive_relative_path != t.file_path \
                or r.anlz_folder_path != t.analyze_path:
            raise ValueError(f"export.pdb: track {t.id} does not read back as written")
        want_bpm = round(t.bpm * 100) / 100 if t.bpm else None
        if r.bpm != want_bpm or (t.key_id and r.key != lib.keys.get(t.key_id)):
            raise ValueError(f"export.pdb: track {t.id} BPM/key do not read back as written")
