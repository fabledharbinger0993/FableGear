"""
fablegear / anvil / id3.py

ID3v2 tag parsing and serialization -- container family A (MP3, WAV, AIFF).

Scope: ID3v2.3 and ID3v2.4. v2.2 (three-character frame IDs) is detected and
rejected with a clear error rather than mis-parsed as v2.3, which is what a
naive `major >= 2` check would do.

The two version differences that actually bite:

  Frame size encoding. v2.3 stores a frame's size as a plain big-endian
  uint32. v2.4 stores it as a synchsafe integer (7 bits per byte). Reading a
  v2.4 tag with v2.3 rules -- or the reverse -- yields frame sizes that are
  wrong by a factor that grows with size, and the parse walks off into
  garbage. Worse, a well-known family of encoders writes plain uint32 sizes
  into tags they label v2.4. _frame_size() below detects and recovers from
  that rather than failing on files that play fine everywhere else.

  Text encodings. v2.3 permits only ISO-8859-1 (0x00) and UTF-16-with-BOM
  (0x01). v2.4 adds UTF-16BE (0x02) and UTF-8 (0x03). Writing UTF-8 into a
  tag labelled v2.3 produces a file that some readers render as mojibake and
  others reject; encode_text() picks a legal encoding for the target version
  instead of always reaching for UTF-8.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

from anvil.errors import CorruptHeader, UnsupportedFormat

log = logging.getLogger(__name__)

ID3_MAGIC = b"ID3"
HEADER_SIZE = 10

# Text encoding byte values, per spec.
ENC_LATIN1 = 0x00
ENC_UTF16_BOM = 0x01
ENC_UTF16_BE = 0x02
ENC_UTF8 = 0x03

# Tag header flag bits.
_FLAG_UNSYNC = 0x80
_FLAG_EXTENDED = 0x40
_FLAG_FOOTER = 0x10

_VALID_ID_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


# ─── Synchsafe integers ───────────────────────────────────────────────────────
#
# A synchsafe integer stores 7 bits per byte with the high bit always clear, so
# the encoded value can never contain a byte that looks like an MPEG sync word.
# The tag header size is ALWAYS synchsafe, in every version. Frame sizes are
# synchsafe only in v2.4.

def synchsafe_decode(raw: bytes, *, strict: bool = True) -> int:
    """
    Decode a synchsafe integer. With strict=True a set high bit -- which the
    spec forbids -- raises; with strict=False it is masked off, which is how
    a lenient reader recovers a plausible value from a malformed field.
    """
    value = 0
    for byte in raw:
        if byte & 0x80:
            if strict:
                raise CorruptHeader(
                    f"synchsafe integer has high bit set: {raw!r}"
                )
            byte &= 0x7F
        value = (value << 7) | byte
    return value


def synchsafe_encode(value: int, length: int = 4) -> bytes:
    """Encode `value` as a synchsafe integer of `length` bytes."""
    if value < 0:
        raise ValueError(f"cannot encode negative value {value}")
    if value >= (1 << (7 * length)):
        raise ValueError(
            f"value {value} does not fit in {length} synchsafe bytes"
        )
    out = bytearray(length)
    for i in range(length - 1, -1, -1):
        out[i] = value & 0x7F
        value >>= 7
    return bytes(out)


def _is_synchsafe(raw: bytes) -> bool:
    return not any(b & 0x80 for b in raw)


# ─── Unsynchronisation ────────────────────────────────────────────────────────
#
# Unsynchronisation inserts a 0x00 after any 0xFF that would otherwise look
# like the start of an MPEG frame sync, so a decoder skipping the tag cannot
# false-sync inside it. Reversing it is a straight substitution.

def deunsynchronise(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


# ─── Frames ───────────────────────────────────────────────────────────────────

@dataclass
class Frame:
    """One ID3v2 frame: a four-character ID and its raw payload."""
    id: str
    data: bytes
    flags: int = 0


@dataclass
class ID3Tag:
    """A parsed ID3v2 tag. `version` is the major version: 3 or 4."""
    version: int = 4
    frames: list[Frame] = field(default_factory=list)

    def get(self, frame_id: str) -> Frame | None:
        for f in self.frames:
            if f.id == frame_id:
                return f
        return None

    def get_all(self, frame_id: str) -> list[Frame]:
        return [f for f in self.frames if f.id == frame_id]

    def remove(self, frame_id: str) -> None:
        """
        Drop every frame with this ID.

        Called before every set, which is why audio_processor.py's manual
        `audio.tags.delall("TBPM")` dance exists today: without it a second
        write appends a second TBPM at a possibly different encoding, and
        which one a reader honours is anyone's guess.
        """
        self.frames = [f for f in self.frames if f.id != frame_id]

    def set(self, frame_id: str, data: bytes) -> None:
        self.remove(frame_id)
        self.frames.append(Frame(frame_id, data))


def _valid_frame_id(raw: bytes) -> bool:
    return len(raw) == 4 and all(b in _VALID_ID_BYTES for b in raw)


def _plausible_boundary(body: bytes, pos: int) -> bool:
    """
    Would `pos` be a sane place for the next frame to start?

    True at exact end-of-body, at padding, or at something that looks like a
    frame header. Used to arbitrate between the two possible readings of a
    v2.4 frame size.
    """
    if pos == len(body):
        return True
    if pos > len(body) or pos < 0:
        return False
    nxt = body[pos:pos + 4]
    if len(nxt) < 4:
        return False
    if nxt == b"\x00\x00\x00\x00":
        return True
    return _valid_frame_id(nxt)


def _frame_size(body: bytes, pos: int, version: int) -> int:
    """
    Read the frame size at `pos`, recovering from the v2.4 size-encoding bug.

    v2.3 is unambiguous: plain big-endian uint32.

    v2.4 says synchsafe, but a well-known family of encoders writes plain
    uint32 anyway. The two readings agree only for sizes under 128, so for any
    frame bigger than that we have to choose. We prefer the spec-compliant
    synchsafe reading and fall back to plain uint32 only when synchsafe leads
    somewhere implausible AND plain lands exactly on a frame boundary --
    evidence, not a guess.
    """
    raw = body[pos + 4:pos + 8]
    plain = struct.unpack(">I", raw)[0]

    if version == 3:
        return plain

    if not _is_synchsafe(raw):
        # Definitionally not a synchsafe integer, so the encoder wrote a plain
        # one. No ambiguity to arbitrate.
        return plain

    ss = synchsafe_decode(raw)
    if ss == plain:
        return ss

    if not _plausible_boundary(body, pos + HEADER_SIZE + ss) and _plausible_boundary(
        body, pos + HEADER_SIZE + plain
    ):
        log.debug(
            "frame %r: synchsafe size %d implausible, using plain uint32 %d",
            body[pos:pos + 4], ss, plain,
        )
        return plain

    return ss


def parse_frames(body: bytes, version: int) -> list[Frame]:
    """Walk a tag body and return its frames. Stops cleanly at padding."""
    frames: list[Frame] = []
    pos = 0
    total = len(body)

    while pos + HEADER_SIZE <= total:
        raw_id = body[pos:pos + 4]

        # All-zero ID means we have reached the padding that follows the last
        # frame. Everything from here to the end of the tag is filler.
        if raw_id == b"\x00\x00\x00\x00":
            break

        if not _valid_frame_id(raw_id):
            # A single malformed frame should not cost us the frames we already
            # read. Stop here and keep what parsed.
            log.debug("stopping frame walk at invalid frame id %r", raw_id)
            break

        size = _frame_size(body, pos, version)
        flags = struct.unpack(">H", body[pos + 8:pos + 10])[0]
        start = pos + HEADER_SIZE
        end = start + size

        if size < 0 or end > total:
            log.debug(
                "frame %r claims %d bytes, only %d remain -- truncating walk",
                raw_id, size, total - start,
            )
            break

        frames.append(Frame(raw_id.decode("ascii"), body[start:end], flags))
        pos = end

    return frames


def serialize_frame(frame: Frame, version: int) -> bytes:
    """Render one frame back to bytes for the given major version."""
    fid = frame.id.encode("ascii")
    if not _valid_frame_id(fid):
        raise ValueError(f"invalid frame id {frame.id!r}")

    if version == 3:
        size = struct.pack(">I", len(frame.data))
    else:
        size = synchsafe_encode(len(frame.data))

    # Frame flags are deliberately written as zero. Anvil never emits
    # compressed, encrypted, or grouped frames, so there is no flag state worth
    # round-tripping -- and preserving a compression flag we did not honour
    # would describe the payload incorrectly.
    return fid + size + b"\x00\x00" + frame.data


# ─── Text payloads ────────────────────────────────────────────────────────────

def _split_terminated(payload: bytes, encoding: int) -> list[bytes]:
    """
    Split on the null terminator appropriate to `encoding`.

    UTF-16 terminators are two bytes and must be found on an even boundary --
    splitting UTF-16 on single 0x00 bytes would cut every ASCII character in
    half, since those encode as 0x41 0x00.
    """
    if encoding in (ENC_UTF16_BOM, ENC_UTF16_BE):
        parts: list[bytes] = []
        current = bytearray()
        i = 0
        while i + 1 < len(payload):
            pair = payload[i:i + 2]
            if pair == b"\x00\x00":
                parts.append(bytes(current))
                current = bytearray()
            else:
                current += pair
            i += 2
        if i < len(payload):
            current += payload[i:]
        parts.append(bytes(current))
        return parts
    return payload.split(b"\x00")


def _decode_one(raw: bytes, encoding: int) -> str:
    if encoding == ENC_LATIN1:
        return raw.decode("latin-1", errors="replace")
    if encoding == ENC_UTF16_BOM:
        return raw.decode("utf-16", errors="replace") if raw else ""
    if encoding == ENC_UTF16_BE:
        return raw.decode("utf-16-be", errors="replace")
    if encoding == ENC_UTF8:
        return raw.decode("utf-8", errors="replace")
    raise CorruptHeader(f"unknown text encoding byte {encoding:#04x}")


def decode_text(data: bytes) -> str:
    """
    Decode a text frame payload, returning its first value.

    v2.4 permits multiple null-separated values in one text frame; every field
    Anvil maps is single-valued, so extra values are dropped rather than
    joined into something no reader would expect.
    """
    if not data:
        return ""
    encoding = data[0]
    parts = _split_terminated(data[1:], encoding)
    for part in parts:
        text = _decode_one(part, encoding).strip("\x00")
        if text:
            return text
    return ""


def encode_text(value: str, version: int) -> bytes:
    """
    Encode a text frame payload using an encoding legal for `version`.

    Prefers ISO-8859-1 when the value fits, because it is the most widely
    understood and the most compact. Falls back to UTF-8 on v2.4 and
    UTF-16-with-BOM on v2.3, which does not have UTF-8 available to it.
    """
    try:
        return bytes([ENC_LATIN1]) + value.encode("latin-1")
    except UnicodeEncodeError:
        pass

    if version == 3:
        # v2.3 has no UTF-8. UTF-16 with BOM is the only Unicode option.
        return bytes([ENC_UTF16_BOM]) + value.encode("utf-16")
    return bytes([ENC_UTF8]) + value.encode("utf-8")


# ─── TXXX (user-defined text) ─────────────────────────────────────────────────
#
# The DJ-native fields -- mix_descriptor, track_role, energy_level,
# downbeat_offset -- have no standard ID3 frame, so they ride TXXX frames
# keyed by description. This is the ID3 equivalent of the MP4 freeform atom,
# and the compatibility matrix flags both as non-native for the same reason.

def decode_txxx(data: bytes) -> tuple[str, str]:
    """Return (description, value) from a TXXX payload."""
    if not data:
        return "", ""
    encoding = data[0]
    parts = _split_terminated(data[1:], encoding)
    if not parts:
        return "", ""
    description = _decode_one(parts[0], encoding).strip("\x00")
    value = ""
    for part in parts[1:]:
        text = _decode_one(part, encoding).strip("\x00")
        if text:
            value = text
            break
    return description, value


def encode_txxx(description: str, value: str, version: int) -> bytes:
    """Build a TXXX payload: encoding byte, description, NUL, value."""
    combined = f"{description}\x00{value}"
    try:
        return bytes([ENC_LATIN1]) + combined.encode("latin-1")
    except UnicodeEncodeError:
        pass

    if version == 3:
        blob = bytes([ENC_UTF16_BOM])
        blob += description.encode("utf-16") + b"\x00\x00"
        blob += value.encode("utf-16")
        return blob
    return bytes([ENC_UTF8]) + combined.encode("utf-8")


# ─── APIC (attached picture) ──────────────────────────────────────────────────

def decode_apic(data: bytes) -> tuple[bytes, str] | None:
    """Return (image_bytes, mime_type) from an APIC payload, or None."""
    if len(data) < 4:
        return None
    encoding = data[0]
    rest = data[1:]

    mime_end = rest.find(b"\x00")
    if mime_end < 0:
        return None
    mime = rest[:mime_end].decode("latin-1", errors="replace") or "image/jpeg"
    rest = rest[mime_end + 1:]

    if not rest:
        return None
    rest = rest[1:]  # picture type byte

    # Description, terminated per the frame's text encoding.
    if encoding in (ENC_UTF16_BOM, ENC_UTF16_BE):
        idx = 0
        while idx + 1 < len(rest):
            if rest[idx:idx + 2] == b"\x00\x00":
                rest = rest[idx + 2:]
                break
            idx += 2
        else:
            return None
    else:
        idx = rest.find(b"\x00")
        if idx < 0:
            return None
        rest = rest[idx + 1:]

    return (rest, mime) if rest else None


# ─── Tag-level parse / serialize ──────────────────────────────────────────────

def tag_total_size(blob: bytes) -> int:
    """
    Total on-disk byte length of the ID3 tag at the start of `blob`.

    This is what a container needs in order to splice the tag out without
    parsing it: header + declared body (+ footer, if v2.4 declared one).
    """
    if len(blob) < HEADER_SIZE or blob[:3] != ID3_MAGIC:
        return 0
    size = synchsafe_decode(blob[6:10], strict=False)
    total = HEADER_SIZE + size
    if blob[5] & _FLAG_FOOTER:
        total += HEADER_SIZE
    return total


def parse_tag(blob: bytes) -> ID3Tag:
    """
    Parse the ID3v2 tag at the start of `blob`.

    Raises UnsupportedFormat if there is no ID3v2 tag or it is a version Anvil
    does not handle; CorruptHeader if the header is structurally broken.
    """
    if len(blob) < HEADER_SIZE:
        raise UnsupportedFormat("too short to contain an ID3v2 header")
    if blob[:3] != ID3_MAGIC:
        raise UnsupportedFormat("no ID3v2 tag at offset 0")

    major, _revision, flags = blob[3], blob[4], blob[5]

    if major == 2:
        # v2.2 uses three-character frame IDs and a three-byte size. Parsing it
        # with v2.3 rules silently misreads every frame, so refuse explicitly.
        raise UnsupportedFormat(
            "ID3v2.2 is not supported (three-character frame IDs)"
        )
    if major not in (3, 4):
        raise UnsupportedFormat(f"unsupported ID3v2 major version {major}")

    size = synchsafe_decode(blob[6:10], strict=False)
    body = blob[HEADER_SIZE:HEADER_SIZE + size]
    if len(body) < size:
        log.debug(
            "ID3 header declares %d body bytes, only %d present", size, len(body)
        )

    if flags & _FLAG_UNSYNC:
        body = deunsynchronise(body)

    if flags & _FLAG_EXTENDED:
        body = _skip_extended_header(body, major)

    return ID3Tag(version=major, frames=parse_frames(body, major))


def _skip_extended_header(body: bytes, major: int) -> bytes:
    """
    Step over an extended header.

    The size field means different things per version: in v2.3 it is a plain
    uint32 counting the bytes that FOLLOW it; in v2.4 it is synchsafe and
    counts itself. Getting this wrong shifts every subsequent frame.
    """
    if len(body) < 4:
        return body
    if major == 3:
        ext_size = struct.unpack(">I", body[:4])[0]
        return body[4 + ext_size:]
    ext_size = synchsafe_decode(body[:4], strict=False)
    return body[ext_size:]


def serialize_tag(tag: ID3Tag, *, padding: int = 1024) -> bytes:
    """
    Render a complete ID3v2 tag.

    Written without unsynchronisation and without an extended header: both are
    optional, both complicate every reader, and neither buys anything for tags
    Anvil produces.

    Trailing padding is conventional and harmless -- it gives other tools room
    to grow the tag in place instead of rewriting the whole file.
    """
    if tag.version not in (3, 4):
        raise ValueError(f"cannot serialize ID3v2.{tag.version}")

    body = b"".join(serialize_frame(f, tag.version) for f in tag.frames)
    body += b"\x00" * max(0, padding)

    header = (
        ID3_MAGIC
        + bytes([tag.version, 0])
        + b"\x00"
        + synchsafe_encode(len(body))
    )
    return header + body
