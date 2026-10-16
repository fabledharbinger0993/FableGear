"""
fablegear / anvil / ogg.py

Ogg container support -- container family B, second half. Covers both Ogg
Vorbis and Ogg Opus, since both wrap the identical comment-list structure
(vorbis_comment.py) in the same page/packet machinery; only the codec
identification packet and comment-packet magic differ.

An Ogg file is a sequence of PAGES (each self-checksummed, "OggS" + a header
+ a segment table + data). One or more pages are concatenated, via a lacing
scheme in the segment table, into logical PACKETS -- and it is packets, not
pages, that carry meaning: packet 0 is the codec identification header,
packet 1 is the comment header Anvil replaces, and (Vorbis only) packet 2 is
the setup header. Every packet after that is audio.

Why this is the harder half of family B: unlike FLAC's simple metadata-block
list, an Ogg page's granule_position records "how much audio has completed as
of this page," and every page has its own CRC32 over its own bytes. Growing
or shrinking the comment packet can shift exactly where later pages'
boundaries fall, so this module re-derives the entire page layout from the
packet sequence rather than patching bytes in place -- and it does so by
walking the ORIGINAL file's packet-to-granule-position mapping, so every
audio packet keeps the granule position it actually had, unchanged.
"""

from __future__ import annotations

from anvil.errors import CorruptHeader, UnsupportedFormat
from anvil.vorbis_comment import (
    decode_opus_tags_packet,
    decode_vorbis_packet,
    encode_opus_tags_packet,
    encode_vorbis_packet,
)

OGGS_MAGIC = b"OggS"

_VORBIS_IDENT_MAGIC = b"\x01vorbis"
_OPUS_IDENT_MAGIC = b"OpusHead"

_FLAG_CONTINUED = 0x01
_FLAG_BOS = 0x02
_FLAG_EOS = 0x04

_NO_GRANULE = (1 << 64) - 1  # -1 as an unsigned 64-bit value: "no packet ends here"


# ─── CRC-32 (Ogg/AAL5 variant: non-reflected, poly 0x04c11db7, init/xor 0) ────
#
# This is NOT the same algorithm as zlib.crc32 / binascii.crc32 (those are the
# reflected CRC-32 used by PNG/Ethernet). Using the wrong one produces pages
# every real Ogg reader rejects as corrupt.

def _build_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def _ogg_crc32(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc


# ─── Page parsing ──────────────────────────────────────────────────────────────

class _Page:
    __slots__ = ("data", "flags", "granule", "segments", "seq", "serial")

    def __init__(self, flags: int, granule: int, serial: int, seq: int,
                 segments: bytes, data: bytes) -> None:
        self.flags = flags
        self.granule = granule
        self.serial = serial
        self.seq = seq
        self.segments = segments
        self.data = data


def sniff(data: bytes) -> bool:
    return data[:4] == OGGS_MAGIC


def _parse_pages(data: bytes) -> list[_Page]:
    pages: list[_Page] = []
    pos = 0
    total = len(data)

    while pos < total:
        if data[pos:pos + 4] != OGGS_MAGIC:
            raise CorruptHeader(f"expected 'OggS' page header at offset {pos}")
        if pos + 27 > total:
            raise CorruptHeader("Ogg page header truncated")

        flags = data[pos + 5]
        granule = int.from_bytes(data[pos + 6:pos + 14], "little")
        serial = int.from_bytes(data[pos + 14:pos + 18], "little")
        seq = int.from_bytes(data[pos + 18:pos + 22], "little")
        n_segments = data[pos + 26]
        seg_start = pos + 27
        segments = data[seg_start:seg_start + n_segments]
        if len(segments) < n_segments:
            raise CorruptHeader("Ogg page segment table truncated")

        body_len = sum(segments)
        body_start = seg_start + n_segments
        body_end = body_start + body_len
        if body_end > total:
            raise CorruptHeader("Ogg page body runs past end of file")

        pages.append(_Page(flags, granule, serial, seq, segments, data[body_start:body_end]))
        pos = body_end

    return pages


def _packets_from_pages(pages: list[_Page]) -> tuple[list[bytes], list[int]]:
    """
    Reassemble logical packets from pages.

    Returns (packets, ending_granule_per_packet) -- the second list records,
    for each packet, the granule_position of the page it finished on (or
    _NO_GRANULE, which cannot actually happen for a packet that terminates,
    since termination is what defines the page it ends on).
    """
    packets: list[bytes] = []
    ending_granule: list[int] = []
    current = bytearray()

    for page in pages:
        seg_pos = 0
        for length in page.segments:
            start = sum(page.segments[:seg_pos])
            current += page.data[start:start + length]
            seg_pos += 1
            if length < 255:
                packets.append(bytes(current))
                ending_granule.append(page.granule)
                current = bytearray()
        # A page ending mid-packet (last segment == 255) leaves `current`
        # non-empty; the next page's leading segments continue it.

    if current:
        raise CorruptHeader("Ogg stream ends mid-packet")

    return packets, ending_granule


def _identify_codec(first_packet: bytes) -> str:
    if first_packet.startswith(_VORBIS_IDENT_MAGIC):
        return "vorbis"
    if first_packet.startswith(_OPUS_IDENT_MAGIC):
        return "opus"
    raise UnsupportedFormat("Ogg stream is neither Vorbis nor Opus")


# ─── Public read ───────────────────────────────────────────────────────────────

def read_comments(data: bytes) -> list[tuple[str, str]]:
    if not sniff(data):
        raise UnsupportedFormat("not an Ogg file")

    pages = _parse_pages(data)
    packets, _granules = _packets_from_pages(pages)
    if len(packets) < 2:
        raise CorruptHeader("Ogg stream has no comment packet")

    codec = _identify_codec(packets[0])
    if codec == "vorbis":
        _vendor, comments = decode_vorbis_packet(packets[1])
    else:
        _vendor, comments = decode_opus_tags_packet(packets[1])
    return comments


# ─── Re-paging ─────────────────────────────────────────────────────────────────

def _segment_table_for(length: int) -> bytes:
    """Lacing values for one packet of `length` bytes, including a terminal
    segment < 255 (a 0-byte terminal segment when length is an exact multiple
    of 255, per spec)."""
    full, rem = divmod(length, 255)
    return bytes([255] * full + [rem])


class _PageRecord:
    __slots__ = ("completes", "continued", "data", "segments")

    def __init__(self) -> None:
        self.segments: list[int] = []
        self.data = bytearray()
        self.completes: list[int] = []
        self.continued = False


def _plan_pages(
    packets: list[bytes],
    ending_granule: list[int],
    forced_breaks: frozenset[int] = frozenset(),
) -> list[_PageRecord]:
    """
    Decide page boundaries without touching bytes yet, so the true last page
    can be found before anything is serialized.

    Greedy packing: accumulate segments into the current page until it holds
    255 (the page_segments field is a single byte), then start a new one. A
    packet whose own lacing needs more than 255 segments spans multiple pages
    on its own. `continued` marks a page whose first segment continues a
    packet that did not finish on the previous page -- true whenever the
    previous page was cut off (by the 255-segment cap) mid-packet, false when
    it was cut off exactly on a packet boundary.

    `forced_breaks` names packet indices after which a NEW page must start
    regardless of how much segment budget remains. This isn't an optimization
    knob -- it's spec compliance: the Vorbis/Opus-in-Ogg encapsulation rules
    require the identification packet to be alone on the stream's first page,
    which greedy packing alone would violate for any file short enough that
    every packet fits in one page's 255-segment budget.
    """
    pages: list[_PageRecord] = [_PageRecord()]
    last_segment_terminal = True  # no packet in progress yet

    for i, packet in enumerate(packets):
        seg_table = _segment_table_for(len(packet))
        pos = 0
        for j, seg_len in enumerate(seg_table):
            if len(pages[-1].segments) >= 255:
                new_page = _PageRecord()
                new_page.continued = not last_segment_terminal
                pages.append(new_page)
            page = pages[-1]
            page.segments.append(seg_len)
            page.data += packet[pos:pos + seg_len]
            pos += seg_len
            last_segment_terminal = j == len(seg_table) - 1
            if last_segment_terminal:
                page.completes.append(ending_granule[i])
                if i in forced_breaks:
                    pages.append(_PageRecord())

    # Drop a trailing page plan that never received any segments (only
    # happens if the very last packet finished exactly on a 255-segment
    # boundary, leaving an empty page queued that was never needed).
    if not pages[-1].segments and len(pages) > 1:
        pages.pop()

    return pages


def _build_pages(
    packets: list[bytes],
    ending_granule: list[int],
    serial: int,
    forced_breaks: frozenset[int] = frozenset(),
) -> bytes:
    """Re-page a packet sequence from scratch. See _plan_pages for the layout logic."""
    pages = _plan_pages(packets, ending_granule, forced_breaks)

    out = bytearray()
    for seq, page in enumerate(pages):
        granule = page.completes[-1] if page.completes else _NO_GRANULE
        flags = 0
        if page.continued:
            flags |= _FLAG_CONTINUED
        if seq == 0:
            flags |= _FLAG_BOS
        if seq == len(pages) - 1:
            flags |= _FLAG_EOS

        header = bytearray()
        header += OGGS_MAGIC
        header.append(0)  # version
        header.append(flags)
        header += granule.to_bytes(8, "little")
        header += serial.to_bytes(4, "little")
        header += seq.to_bytes(4, "little")
        header += b"\x00\x00\x00\x00"  # CRC placeholder
        header.append(len(page.segments))
        header += bytes(page.segments)
        raw = bytes(header) + bytes(page.data)
        crc = _ogg_crc32(raw[:22] + b"\x00\x00\x00\x00" + raw[26:])
        out += raw[:22] + crc.to_bytes(4, "little") + raw[26:]

    return bytes(out)


def write_comments(data: bytes, comments: list[tuple[str, str]]) -> bytes:
    if not sniff(data):
        raise UnsupportedFormat("not an Ogg file")

    pages = _parse_pages(data)
    if not pages:
        raise CorruptHeader("Ogg file has no pages")
    serial = pages[0].serial

    packets, ending_granule = _packets_from_pages(pages)
    if len(packets) < 2:
        raise CorruptHeader("Ogg stream has no comment packet")

    codec = _identify_codec(packets[0])
    if codec == "vorbis":
        new_packet = encode_vorbis_packet(comments)
        header_count = 3  # ident, comment, setup
    else:
        new_packet = encode_opus_tags_packet(comments)
        header_count = 2  # ident (OpusHead), tags (OpusTags)

    packets[1] = new_packet
    # The identification packet must be alone on the stream's first page, and
    # header packets must not share a page with audio -- see _plan_pages.
    forced_breaks = frozenset({0, header_count - 1})
    return _build_pages(packets, ending_granule, serial, forced_breaks)
