"""
fablegear / anvil / flac.py

FLAC container support -- container family B, first half.

Structure, per the Xiph FLAC spec:

    "fLaC"                              4-byte magic
    metadata block, metadata block, ...  see below
    <audio frames>                       untouched, everything after the
                                          last metadata block

Each metadata block is a 4-byte header (1 bit "is this the last block" flag,
7 bits block type) + a 3-byte big-endian length, followed by that many bytes
of payload. STREAMINFO (type 0) is mandatory and must be the first block.
VORBIS_COMMENT (type 4) is what Anvil reads and writes here; every other
block (SEEKTABLE, PICTURE, PADDING, APPLICATION, CUESHEET...) is preserved
byte-for-byte and in its original position, because splicing the comment
block is a length-prefixed list operation, not a full-file rewrite.

Unlike Ogg (ogg.py), there is no absolute byte-offset table anywhere in a
FLAC file that a growing/shrinking comment block could invalidate -- the
audio frames simply start wherever the metadata blocks end. That is what
makes this container the safe one to build first.
"""

from __future__ import annotations

from anvil.errors import CorruptHeader, UnsupportedFormat
from anvil.vorbis_comment import decode_comment_list, encode_comment_list

FLAC_MAGIC = b"fLaC"

_STREAMINFO = 0
_VORBIS_COMMENT = 4
_PICTURE = 6

_HEADER_SIZE = 4


def sniff(data: bytes) -> bool:
    return data[:4] == FLAC_MAGIC


def _parse_blocks(data: bytes) -> tuple[list[tuple[int, bytes]], int]:
    """
    Walk the metadata blocks starting after the 4-byte magic.

    Returns ([(block_type, payload), ...], offset_of_first_audio_frame).
    """
    blocks: list[tuple[int, bytes]] = []
    pos = 4
    total = len(data)

    while pos + _HEADER_SIZE <= total:
        header = data[pos]
        is_last = bool(header & 0x80)
        block_type = header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        start = pos + _HEADER_SIZE
        end = start + length

        if end > total:
            raise CorruptHeader(
                f"FLAC metadata block declares {length} bytes, "
                f"only {total - start} remain"
            )

        blocks.append((block_type, data[start:end]))
        pos = end

        if is_last:
            return blocks, pos

    raise CorruptHeader("FLAC file has no metadata block marked 'last'")


def _build_blocks(blocks: list[tuple[int, bytes]]) -> bytes:
    """Rebuild the metadata-block region (magic included) from a block list."""
    out = bytearray(FLAC_MAGIC)
    for i, (block_type, payload) in enumerate(blocks):
        is_last = i == len(blocks) - 1
        header = (0x80 if is_last else 0x00) | (block_type & 0x7F)
        out.append(header)
        out += len(payload).to_bytes(3, "big")
        out += payload
    return bytes(out)


def read_comments(data: bytes) -> list[tuple[str, str]]:
    """Return the file's Vorbis comment list, or [] if it has none."""
    if not sniff(data):
        raise UnsupportedFormat("not a FLAC file")

    blocks, _audio_start = _parse_blocks(data)
    for block_type, payload in blocks:
        if block_type == _VORBIS_COMMENT:
            _vendor, comments = decode_comment_list(payload)
            return comments
    return []


def read_cover_art(data: bytes) -> tuple[bytes, str] | None:
    """Return (image_bytes, mime_type) from the first PICTURE block, or None."""
    if not sniff(data):
        raise UnsupportedFormat("not a FLAC file")

    blocks, _audio_start = _parse_blocks(data)
    for block_type, payload in blocks:
        if block_type == _PICTURE:
            return _decode_picture(payload)
    return None


def _decode_picture(payload: bytes) -> tuple[bytes, str] | None:
    """
    Decode a FLAC PICTURE metadata block.

    Layout: picture_type(4) mime_length(4) mime mime_desc_length(4) desc
    width(4) height(4) depth(4) colors(4) data_length(4) data -- all
    big-endian. Only mime + data are needed here.
    """
    if len(payload) < 32:
        return None
    pos = 4
    mime_len = int.from_bytes(payload[pos:pos + 4], "big")
    pos += 4
    mime = payload[pos:pos + mime_len].decode("latin-1", errors="replace")
    pos += mime_len
    desc_len = int.from_bytes(payload[pos:pos + 4], "big")
    pos += 4 + desc_len
    pos += 16  # width, height, depth, colors
    data_len = int.from_bytes(payload[pos:pos + 4], "big")
    pos += 4
    image = payload[pos:pos + data_len]
    if not image:
        return None
    return image, (mime or "image/jpeg")


def write_comments(data: bytes, comments: list[tuple[str, str]]) -> bytes:
    """Return the complete new file bytes with `comments` installed."""
    if not sniff(data):
        raise UnsupportedFormat("not a FLAC file")

    blocks, audio_start = _parse_blocks(data)
    audio = data[audio_start:]

    new_payload = encode_comment_list(comments)
    new_blocks: list[tuple[int, bytes]] = []
    replaced = False
    for block_type, payload in blocks:
        if block_type == _VORBIS_COMMENT:
            new_blocks.append((_VORBIS_COMMENT, new_payload))
            replaced = True
        else:
            new_blocks.append((block_type, payload))
    if not replaced:
        # STREAMINFO must stay first; the comment block's position among the
        # rest is not spec-significant, so appending is simplest and safe.
        new_blocks.append((_VORBIS_COMMENT, new_payload))

    return _build_blocks(new_blocks) + audio
