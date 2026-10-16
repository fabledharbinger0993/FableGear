"""
fablegear / anvil / vorbis_comment.py

The Vorbis comment data structure -- shared byte-for-byte between FLAC's
VORBIS_COMMENT metadata block and the comment packet inside an Ogg Vorbis or
Ogg Opus stream. Per the Xiph comment-header spec:

    [framing is a property of the PACKET that wraps this, not this structure]
    vendor_length      : uint32 LE
    vendor_string       : vendor_length bytes, UTF-8
    user_comment_count  : uint32 LE
    for each comment:
      length            : uint32 LE
      "KEY=value"        : length bytes, UTF-8

A comment key is conventionally uppercase and matched case-insensitively; the
spec permits any of [0x20-0x7D] excluding '='. Anvil always writes uppercase
keys and reads case-insensitively, matching every real tagger's behaviour.

This module only knows the comment LIST. What wraps it -- a FLAC metadata
block header, or an Ogg "\\x03vorbis" / "OpusTags" packet -- is the concern of
flac.py and ogg.py respectively.
"""

from __future__ import annotations

from anvil.errors import CorruptHeader

VORBIS_PACKET_MAGIC = b"\x03vorbis"
VORBIS_PACKET_FRAMING = b"\x01"
OPUS_TAGS_MAGIC = b"OpusTags"

_VENDOR = "fablegear/anvil"


def encode_comment_list(comments: list[tuple[str, str]], *, vendor: str = _VENDOR) -> bytes:
    """Encode (key, value) pairs into the raw comment-list bytes."""
    vendor_bytes = vendor.encode("utf-8")
    out = bytearray()
    out += len(vendor_bytes).to_bytes(4, "little")
    out += vendor_bytes
    out += len(comments).to_bytes(4, "little")
    for key, value in comments:
        entry = f"{key}={value}".encode()
        out += len(entry).to_bytes(4, "little")
        out += entry
    return bytes(out)


def decode_comment_list(data: bytes) -> tuple[str, list[tuple[str, str]]]:
    """Decode raw comment-list bytes into (vendor_string, [(key, value), ...])."""
    vendor, comments, _consumed = _decode_comment_list_prefix(data)
    return vendor, comments


def _decode_comment_list_prefix(data: bytes) -> tuple[str, list[tuple[str, str]], int]:
    """
    Decode a comment list from the FRONT of `data`, also returning how many
    bytes it consumed.

    Some real encoders (ffmpeg/lavf observed in the wild) pad the Ogg comment
    HEADER PACKET with trailing zero bytes after the framing bit, all within
    the same logical packet as delimited by the lacing table. The comment
    list itself is fully length-prefixed and self-describing, so it can be
    decoded correctly by reading only as far as it says to -- the caller
    decides what to do with anything left over.
    """
    if len(data) < 8:
        raise CorruptHeader("vorbis comment list shorter than its own header")

    pos = 0
    vendor_len = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    if pos + vendor_len > len(data):
        raise CorruptHeader("vorbis comment vendor string runs past end of block")
    vendor = data[pos:pos + vendor_len].decode("utf-8", errors="replace")
    pos += vendor_len

    if pos + 4 > len(data):
        raise CorruptHeader("vorbis comment block truncated before comment count")
    count = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4

    comments: list[tuple[str, str]] = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise CorruptHeader("vorbis comment block truncated mid-list")
        clen = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        if pos + clen > len(data):
            raise CorruptHeader("vorbis comment entry runs past end of block")
        entry = data[pos:pos + clen].decode("utf-8", errors="replace")
        pos += clen
        key, sep, value = entry.partition("=")
        if sep:
            comments.append((key, value))
        else:
            # A comment with no '=' is spec-illegal; keep it under an empty
            # key rather than dropping it silently.
            comments.append(("", entry))

    return vendor, comments, pos


def encode_vorbis_packet(comments: list[tuple[str, str]], *, vendor: str = _VENDOR) -> bytes:
    """Build a complete Ogg Vorbis comment-header PACKET (with framing bit)."""
    return VORBIS_PACKET_MAGIC + encode_comment_list(comments, vendor=vendor) + VORBIS_PACKET_FRAMING


def decode_vorbis_packet(packet: bytes) -> tuple[str, list[tuple[str, str]]]:
    """
    Decode a complete Ogg Vorbis comment-header packet, verifying framing.

    The framing bit must immediately follow the comment list; anything after
    THAT is tolerated as encoder padding (see _decode_comment_list_prefix) and
    is not re-emitted on write -- Anvil's own writes never pad this packet.
    """
    if not packet.startswith(VORBIS_PACKET_MAGIC):
        raise CorruptHeader("not a vorbis comment header packet")
    body = packet[len(VORBIS_PACKET_MAGIC):]
    vendor, comments, consumed = _decode_comment_list_prefix(body)
    if consumed >= len(body) or body[consumed] != 0x01:
        raise CorruptHeader("vorbis comment header packet missing framing bit")
    return vendor, comments


def encode_opus_tags_packet(comments: list[tuple[str, str]], *, vendor: str = _VENDOR) -> bytes:
    """Build a complete OpusTags packet. No trailing framing bit -- Opus doesn't have one."""
    return OPUS_TAGS_MAGIC + encode_comment_list(comments, vendor=vendor)


def decode_opus_tags_packet(packet: bytes) -> tuple[str, list[tuple[str, str]]]:
    if not packet.startswith(OPUS_TAGS_MAGIC):
        raise CorruptHeader("not an OpusTags packet")
    return decode_comment_list(packet[len(OPUS_TAGS_MAGIC):])
