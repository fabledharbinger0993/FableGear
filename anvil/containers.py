"""
fablegear / anvil / containers.py

Where the ID3 blob lives in each container, and how to replace it without
disturbing anything else in the file.

Family A covers three containers that all carry ID3v2 but store it in three
different places:

  MP3   The tag sits at offset 0 and the audio stream follows it. Replacing
        the tag is a splice at the head of the file.

  WAV   RIFF. The tag is the payload of an "id3 " chunk (lowercase, trailing
        space, by overwhelming convention -- "ID3 " also appears in the wild).
        Chunk sizes are little-endian and every chunk is word-aligned, so an
        odd-length payload is followed by a pad byte that is NOT counted in
        the size field.

  AIFF  FORM/AIFF. Same chunked structure, same word alignment, but sizes are
        big-endian and the chunk id is "ID3 " uppercase.

scanner.py currently treats WAV and AIFF as _TAG_OPTIONAL_EXTS -- tags on
those formats are not trusted to be there, because support for ID3-inside-a-
chunk is inconsistent across libraries. Handling the chunk structure properly
here is what lets them stop being second-class.
"""

from __future__ import annotations

import logging
from typing import Literal

from anvil.errors import CorruptHeader, UnsupportedFormat
from anvil.id3 import ID3_MAGIC, tag_total_size

log = logging.getLogger(__name__)

# int.from_bytes / to_bytes take a Literal, not a bare str.
ByteOrder = Literal["little", "big"]

MP3 = "mp3"
WAV = "wav"
AIFF = "aiff"

# RIFF/AIFF chunk ids that carry an ID3 payload. Compared case-insensitively;
# these are the spellings Anvil writes.
_WAV_ID3_CHUNK = b"id3 "
_AIFF_ID3_CHUNK = b"ID3 "


# ─── Sniffing ─────────────────────────────────────────────────────────────────

def sniff(data: bytes) -> str:
    """
    Identify the container from its leading bytes.

    Extension is not consulted: a .wav that is really an MP3 is a real thing
    that happens, and trusting the name over the bytes is how a writer
    corrupts a file.
    """
    if len(data) < 4:
        raise UnsupportedFormat("file is too short to identify")

    if data[:3] == ID3_MAGIC:
        # An ID3 tag at offset 0 is the MP3 convention. WAV and AIFF wrap
        # theirs in a chunk, so they never start this way.
        return MP3

    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return WAV

    if data[:4] == b"FORM" and data[8:12] in (b"AIFF", b"AIFC"):
        return AIFF

    # A bare MPEG audio frame: 11 sync bits set.
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return MP3

    raise UnsupportedFormat(
        f"unrecognised container (leading bytes {data[:4]!r})"
    )


# ─── Chunked containers (RIFF / AIFF) ─────────────────────────────────────────

def _parse_chunks(data: bytes, endian: ByteOrder) -> list[tuple[bytes, bytes]]:
    """
    Walk a chunked container, returning [(chunk_id, payload), ...].

    The 12-byte container header ("RIFF" + size + "WAVE") is skipped; padding
    bytes after odd-length payloads are consumed and not returned, because
    they are structural filler rather than data. _build_chunks re-adds them.
    """
    chunks: list[tuple[bytes, bytes]] = []
    pos = 12
    total = len(data)

    while pos + 8 <= total:
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], endian)
        start = pos + 8
        end = start + size

        if end > total:
            log.debug(
                "chunk %r declares %d bytes but only %d remain -- truncating",
                chunk_id, size, total - start,
            )
            chunks.append((chunk_id, data[start:total]))
            break

        chunks.append((chunk_id, data[start:end]))
        pos = end + (size & 1)   # skip the word-alignment pad byte

    return chunks


def _build_chunks(
    container_id: bytes,
    form_type: bytes,
    chunks: list[tuple[bytes, bytes]],
    endian: ByteOrder,
) -> bytes:
    """Rebuild a chunked container, restoring word alignment and the size field."""
    body = bytearray()
    for chunk_id, payload in chunks:
        body += chunk_id
        body += len(payload).to_bytes(4, endian)
        body += payload
        if len(payload) & 1:
            body += b"\x00"

    # The container size field counts the form type plus every chunk -- that
    # is, everything after the 8-byte container header.
    size = len(form_type) + len(body)
    return container_id + size.to_bytes(4, endian) + form_type + bytes(body)


def _chunk_config(kind: str) -> tuple[ByteOrder, bytes, bytes]:
    """Return (endianness, container_id, id3_chunk_id) for a chunked kind."""
    if kind == WAV:
        return "little", b"RIFF", _WAV_ID3_CHUNK
    if kind == AIFF:
        return "big", b"FORM", _AIFF_ID3_CHUNK
    raise ValueError(f"{kind} is not a chunked container")


def _find_id3_chunk(chunks: list[tuple[bytes, bytes]]) -> int:
    """Index of the ID3 chunk, or -1. Matched case-insensitively."""
    for i, (chunk_id, _payload) in enumerate(chunks):
        if chunk_id.lower() == b"id3 ":
            return i
    return -1


# ─── Public read / write ──────────────────────────────────────────────────────

def extract_id3(data: bytes, kind: str) -> bytes | None:
    """Return the raw ID3v2 blob from a file's bytes, or None if it has none."""
    if kind == MP3:
        if data[:3] != ID3_MAGIC:
            return None
        size = tag_total_size(data)
        if size <= 0:
            raise CorruptHeader("ID3 header present but declares no body")
        return data[:size]

    endian, _container, _chunk_id = _chunk_config(kind)
    chunks = _parse_chunks(data, endian)
    idx = _find_id3_chunk(chunks)
    if idx < 0:
        return None

    payload = chunks[idx][1]
    return payload if payload[:3] == ID3_MAGIC else None


def install_id3(data: bytes, kind: str, blob: bytes) -> bytes:
    """
    Return the complete new file bytes with `blob` installed as the ID3 tag.

    Everything that is not the tag -- the audio stream, every other chunk, and
    their order -- is preserved exactly. That property is the whole point of
    this module and is what the round-trip tests assert on.
    """
    if kind == MP3:
        old = extract_id3(data, kind)
        audio = data[len(old):] if old else data
        return blob + audio

    endian, container_id, chunk_id = _chunk_config(kind)
    form_type = data[8:12]
    chunks = _parse_chunks(data, endian)

    idx = _find_id3_chunk(chunks)
    if idx >= 0:
        # Keep the spelling already in the file rather than renaming a chunk
        # some other tool wrote and may go looking for again.
        chunks[idx] = (chunks[idx][0], blob)
    else:
        chunks.append((chunk_id, blob))

    return _build_chunks(container_id, form_type, chunks, endian)
