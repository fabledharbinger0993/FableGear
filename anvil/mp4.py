"""
fablegear / anvil / mp4.py

MP4/M4A atom support -- container family C.

Ground-truthed against a real file (a Logic Pro demo-song M4A, tagged with
mutagen and box-walked byte by byte -- not assumed from memory), the relevant
path is:

    moov
      udta
        meta                4-byte version+flags, THEN children (FullBox)
          hdlr               (untouched, preserved)
          ilst
            <atom code>      e.g. "\\xa9nam", "tmpo" -- one child:
              data            4-byte well-known-type + 4-byte locale (both
                               zero for our purposes) + raw payload
            "----"           freeform: three children in order:
              mean            4-byte version+flags(0) + reverse-DNS string,
                               e.g. "com.apple.iTunes", NOT null-terminated
              name            same shape, e.g. "initialkey"
              data            same shape as above
          free               (untouched, preserved)
      trak / trak / ...       each may hold stco (32-bit) or co64 (64-bit)
                               chunk-offset tables inside mdia/minf/stbl

Anvil never edits `mdat` (or any other top-level box) in place -- only `moov`
is rebuilt, bottom-up: ilst -> meta -> udta -> moov, then the whole file is
reassembled from the ORIGINAL top-level boxes with the new moov spliced in.

The one MP4-specific hazard this creates: `stco`/`co64` chunk-offset tables
store ABSOLUTE file byte offsets into `mdat`. If `moov` sits before `mdat` in
the file (the common case; verified in the ground-truth file above) and its
rebuilt size differs from the original, every stored offset shifts by exactly
that delta -- so this module always measures that delta and patches every
stco/co64 entry it finds, in every trak, rather than assuming a no-op.
"""

from __future__ import annotations

from typing import Any

from anvil.errors import CorruptHeader, UnsupportedFormat
from anvil.schema import TrackFields

MP4_BRAND_OFFSET = 4
_FTYP = b"ftyp"
_MOOV = b"moov"
_MDAT = b"mdat"
_UDTA = b"udta"
_META = b"meta"
_ILST = b"ilst"
_TRAK = b"trak"
_MDIA = b"mdia"
_MINF = b"minf"
_STBL = b"stbl"
_STCO = b"stco"
_CO64 = b"co64"
_MEAN = b"mean"
_NAME = b"name"
_DATA = b"data"
_FREEFORM = b"----"

_DATA_TYPE_UTF8 = 1
_DATA_TYPE_INTEGER = 21


def sniff(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == _FTYP


# ─── Generic flat box splitting ────────────────────────────────────────────────

def _split_boxes(payload: bytes) -> list[tuple[bytes, bytes]]:
    """
    Split a contiguous byte range into sibling (type, box_payload) pairs.

    box_payload excludes the box's own header. A size==1 box's 8-byte
    largesize follows the type; a size==0 box (only legal for the last box in
    a range, conventionally only at the top level) extends to the end of
    `payload`.
    """
    boxes: list[tuple[bytes, bytes]] = []
    pos = 0
    total = len(payload)

    while pos + 8 <= total:
        size = int.from_bytes(payload[pos:pos + 4], "big")
        typ = payload[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > total:
                raise CorruptHeader(f"{typ!r} box declares largesize but file is truncated")
            size = int.from_bytes(payload[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = total - pos

        end = pos + size
        if size < header or end > total:
            raise CorruptHeader(f"{typ!r} box declares {size} bytes, only {total - pos} remain")

        boxes.append((typ, payload[pos + header:end]))
        pos = end

    return boxes


def _build_boxes(boxes: list[tuple[bytes, bytes]]) -> bytes:
    """Rebuild a flat sibling sequence with fresh, standard 32-bit-size headers."""
    out = bytearray()
    for typ, box_payload in boxes:
        size = 8 + len(box_payload)
        out += size.to_bytes(4, "big")
        out += typ
        out += box_payload
    return bytes(out)


def _find(boxes: list[tuple[bytes, bytes]], typ: bytes) -> bytes | None:
    for t, p in boxes:
        if t == typ:
            return p
    return None


def _replace(boxes: list[tuple[bytes, bytes]], typ: bytes, new_payload: bytes) -> list[tuple[bytes, bytes]]:
    return [(t, new_payload) if t == typ else (t, p) for t, p in boxes]


# ─── ilst item atoms ────────────────────────────────────────────────────────────

def _decode_data_atom(box_payload: bytes) -> tuple[int, bytes]:
    """Return (well_known_type, raw_value_bytes) from a 'data' atom's payload."""
    if len(box_payload) < 8:
        raise CorruptHeader("'data' atom shorter than its own header")
    data_type = int.from_bytes(box_payload[0:4], "big")
    return data_type, box_payload[8:]


def _encode_data_atom(data_type: int, value: bytes) -> bytes:
    return data_type.to_bytes(4, "big") + b"\x00\x00\x00\x00" + value


def _decode_freeform(item_payload: bytes) -> tuple[str, str, str] | None:
    """From a '----' item's payload, return (mean, name, text_value) or None."""
    children = _split_boxes(item_payload)
    mean = _find(children, _MEAN)
    name = _find(children, _NAME)
    data = _find(children, _DATA)
    if mean is None or name is None or data is None:
        return None
    _dtype, value = _decode_data_atom(data)
    return (
        mean[4:].decode("utf-8", errors="replace"),
        name[4:].decode("utf-8", errors="replace"),
        value.decode("utf-8", errors="replace"),
    )


def _encode_freeform(mean: str, name: str, value: str) -> bytes:
    mean_box = b"\x00\x00\x00\x00" + mean.encode("utf-8")
    name_box = b"\x00\x00\x00\x00" + name.encode("utf-8")
    data_box = _encode_data_atom(_DATA_TYPE_UTF8, value.encode("utf-8"))
    children = [(_MEAN, mean_box), (_NAME, name_box), (_DATA, data_box)]
    return _build_boxes(children)


def read_ilst(data: bytes) -> list[tuple[bytes, bytes]]:
    """Return ilst's raw children as (item_type, item_payload) pairs."""
    if not sniff(data):
        raise UnsupportedFormat("not an MP4/M4A file")

    top = _split_boxes(data)
    moov = _find(top, _MOOV)
    if moov is None:
        raise CorruptHeader("MP4 file has no moov box")
    udta = _find(_split_boxes(moov), _UDTA)
    if udta is None:
        return []
    meta = _find(_split_boxes(udta), _META)
    if meta is None or len(meta) < 4:
        return []
    ilst = _find(_split_boxes(meta[4:]), _ILST)
    if ilst is None:
        return []
    return _split_boxes(ilst)


# ─── stco / co64 patching ──────────────────────────────────────────────────────

def _patch_offsets(payload: bytes, delta: int, *, width: int) -> bytes:
    """Add `delta` to every `width`-byte big-endian offset in a stco/co64 body."""
    if len(payload) < 8:
        return payload
    version_flags = payload[0:4]
    count = int.from_bytes(payload[4:8], "big")
    out = bytearray(version_flags + payload[4:8])
    pos = 8
    for _ in range(count):
        if pos + width > len(payload):
            break
        offset = int.from_bytes(payload[pos:pos + width], "big")
        out += (offset + delta).to_bytes(width, "big")
        pos += width
    out += payload[pos:]  # trailing bytes, if the declared count undershoots
    return bytes(out)


def _patch_moov_chunk_offsets(moov_payload: bytes, delta: int) -> bytes:
    """Walk every trak/mdia/minf/stbl in `moov` and shift stco/co64 offsets by `delta`."""
    if delta == 0:
        return moov_payload

    def rebuild(boxes: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        out: list[tuple[bytes, bytes]] = []
        for typ, payload in boxes:
            if typ == _STCO:
                out.append((typ, _patch_offsets(payload, delta, width=4)))
            elif typ == _CO64:
                out.append((typ, _patch_offsets(payload, delta, width=8)))
            elif typ in (_TRAK, _MDIA, _MINF, _STBL):
                out.append((typ, _build_boxes(rebuild(_split_boxes(payload)))))
            else:
                out.append((typ, payload))
        return out

    return _build_boxes(rebuild(_split_boxes(moov_payload)))


# ─── Public read / write ──────────────────────────────────────────────────────

def write_ilst(data: bytes, new_items: list[tuple[bytes, bytes]]) -> bytes:
    """Return the complete new file bytes with ilst's children replaced."""
    if not sniff(data):
        raise UnsupportedFormat("not an MP4/M4A file")

    top = _split_boxes(data)
    moov_payload = _find(top, _MOOV)
    if moov_payload is None:
        raise CorruptHeader("MP4 file has no moov box")

    moov_boxes = _split_boxes(moov_payload)
    udta_payload = _find(moov_boxes, _UDTA)
    if udta_payload is None:
        # No udta at all: add one, holding a fresh meta/hdlr/ilst.
        meta_payload = _build_new_meta(new_items)
        udta_payload = _build_boxes([(_META, meta_payload)])
        moov_boxes = [*moov_boxes, (_UDTA, udta_payload)]
    else:
        udta_boxes = _split_boxes(udta_payload)
        meta_payload = _find(udta_boxes, _META)
        if meta_payload is None or len(meta_payload) < 4:
            new_meta = _build_new_meta(new_items)
            udta_boxes = [*udta_boxes, (_META, new_meta)]
        else:
            version_flags, meta_children = meta_payload[:4], _split_boxes(meta_payload[4:])
            new_ilst = _build_boxes(new_items)
            if _find(meta_children, _ILST) is None:
                meta_children = [*meta_children, (_ILST, new_ilst)]
            else:
                meta_children = _replace(meta_children, _ILST, new_ilst)
            new_meta = version_flags + _build_boxes(meta_children)
            udta_boxes = _replace(udta_boxes, _META, new_meta)
        udta_payload = _build_boxes(udta_boxes)
        moov_boxes = _replace(moov_boxes, _UDTA, udta_payload)

    new_moov_payload = _build_boxes(moov_boxes)

    # Chunk-offset patching: only matters if moov sits before mdat in the
    # original file, and only by however much moov's size actually changed.
    delta = len(new_moov_payload) - len(moov_payload)
    moov_index = next(i for i, (t, _p) in enumerate(top) if t == _MOOV)
    mdat_index = next((i for i, (t, _p) in enumerate(top) if t == _MDAT), None)
    if delta != 0 and mdat_index is not None and moov_index < mdat_index:
        new_moov_payload = _patch_moov_chunk_offsets(new_moov_payload, delta)

    new_top = _replace(top, _MOOV, new_moov_payload)
    return _build_boxes(new_top)


def _build_new_meta(items: list[tuple[bytes, bytes]]) -> bytes:
    """A minimal fresh meta box: version+flags, a bare hdlr, and ilst."""
    # A minimal "mdir"-handler hdlr, matching what real encoders write for
    # metadata handler type. version+flags(4) + predefined(4) + handler_type(4)
    # + reserved(12) + name (empty, single NUL).
    hdlr_payload = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"mdir"
        + b"appl"
        + b"\x00" * 9
        + b"\x00"
    )
    children = _build_boxes([(b"hdlr", hdlr_payload), (_ILST, _build_boxes(items))])
    return b"\x00\x00\x00\x00" + children


# ─── Field mapping ──────────────────────────────────────────────────────────────

_TEXT_ATOMS: dict[str, bytes] = {
    "title": "\xa9nam".encode("latin-1"),
    "artist": "\xa9ART".encode("latin-1"),
    "album": "\xa9alb".encode("latin-1"),
}

_MEAN_NS = "com.apple.iTunes"
_KEY_NAME = "initialkey"       # matches audio_processor.py's existing on-disk spelling
_BPM_PRECISE_NAME = "BPM_PRECISE"

_CUSTOM_FREEFORM: dict[str, str] = {
    "mix_descriptor": "MIXDESCRIPTOR",
    "track_role": "TRACKROLE",
    "energy_level": "ENERGYLEVEL",
    "downbeat_offset": "DOWNBEATOFFSET",
    "time_signature": "TIMESIGNATURE",
}

_INT_FIELDS = frozenset({"energy_level"})
_FLOAT_FIELDS = frozenset({"downbeat_offset"})


def _coerce(name: str, raw: str) -> Any:
    text = raw.strip()
    if not text:
        return None
    if name in _INT_FIELDS:
        try:
            return int(float(text))
        except ValueError:
            return None
    if name in _FLOAT_FIELDS:
        try:
            return float(text)
        except ValueError:
            return None
    return text


def _format_value(name: str, value: Any) -> str:
    if name in _INT_FIELDS:
        return str(int(value))
    if name in _FLOAT_FIELDS:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    return str(value)


def fields_from_ilst(items: list[tuple[bytes, bytes]]) -> TrackFields:
    fields = TrackFields()

    for name, atom in _TEXT_ATOMS.items():
        for typ, payload in items:
            if typ == atom:
                data = _find(_split_boxes(payload), _DATA)
                if data is not None:
                    _dtype, value = _decode_data_atom(data)
                    setattr(fields, name, _coerce(name, value.decode("utf-8", errors="replace")))
                break

    freeform: dict[tuple[str, str], str] = {}
    tmpo_precise: float | None = None
    for typ, payload in items:
        if typ == b"tmpo":
            data = _find(_split_boxes(payload), _DATA)
            if data is not None:
                _dtype, value = _decode_data_atom(data)
                if len(value) >= 2:
                    fields.bpm = float(int.from_bytes(value[:2], "big"))
        elif typ == _FREEFORM:
            decoded = _decode_freeform(payload)
            if decoded is not None:
                mean, fname, value = decoded
                freeform[(mean, fname.upper())] = value

    key_val = freeform.get((_MEAN_NS, _KEY_NAME.upper()))
    if key_val is not None:
        fields.initial_key = _coerce("initial_key", key_val)

    precise_raw = freeform.get((_MEAN_NS, _BPM_PRECISE_NAME.upper()))
    if precise_raw is not None:
        try:
            tmpo_precise = float(precise_raw)
        except ValueError:
            tmpo_precise = None
    if tmpo_precise is not None and fields.bpm is not None:
        # Trust the precise companion only while it still agrees with tmpo,
        # same rule id3.py applies to TBPM vs. its TXXX companion.
        if abs(round(tmpo_precise) - fields.bpm) <= 0.5:
            fields.bpm = tmpo_precise

    for name, fname in _CUSTOM_FREEFORM.items():
        val = freeform.get((_MEAN_NS, fname.upper()))
        if val is not None:
            setattr(fields, name, _coerce(name, val))

    return fields


def _remove_item(items: list[tuple[bytes, bytes]], atom: bytes) -> list[tuple[bytes, bytes]]:
    return [(t, p) for t, p in items if t != atom]


def _remove_freeform(items: list[tuple[bytes, bytes]], name: str) -> list[tuple[bytes, bytes]]:
    out = []
    for t, p in items:
        if t == _FREEFORM:
            decoded = _decode_freeform(p)
            if decoded is not None and decoded[0] == _MEAN_NS and decoded[1].upper() == name.upper():
                continue
        out.append((t, p))
    return out


def _set_freeform(items: list[tuple[bytes, bytes]], name: str, value: str) -> list[tuple[bytes, bytes]]:
    kept = _remove_freeform(items, name)
    kept.append((_FREEFORM, _encode_freeform(_MEAN_NS, name, value)))
    return kept


def apply_field(items: list[tuple[bytes, bytes]], name: str, value: Any) -> list[tuple[bytes, bytes]]:
    if name == "bpm":
        rounded = round(float(value))
        items = _remove_item(items, b"tmpo")
        items.append((b"tmpo", _build_boxes([(_DATA, _encode_data_atom(_DATA_TYPE_INTEGER, rounded.to_bytes(2, "big")))])))
        precise = f"{float(value):.4f}".rstrip("0").rstrip(".")
        return _set_freeform(items, _BPM_PRECISE_NAME, precise)

    if name == "initial_key":
        return _set_freeform(items, _KEY_NAME, str(value))

    if name in _TEXT_ATOMS:
        atom = _TEXT_ATOMS[name]
        items = _remove_item(items, atom)
        items.append((atom, _build_boxes([(_DATA, _encode_data_atom(_DATA_TYPE_UTF8, str(value).encode("utf-8")))])))
        return items

    if name in _CUSTOM_FREEFORM:
        return _set_freeform(items, _CUSTOM_FREEFORM[name], _format_value(name, value))

    raise ValueError(f"no MP4 atom mapping for field {name!r}")


def remove_schema_field(items: list[tuple[bytes, bytes]], name: str) -> list[tuple[bytes, bytes]]:
    if name == "bpm":
        return _remove_freeform(_remove_item(items, b"tmpo"), _BPM_PRECISE_NAME)
    if name == "initial_key":
        return _remove_freeform(items, _KEY_NAME)
    if name in _TEXT_ATOMS:
        return _remove_item(items, _TEXT_ATOMS[name])
    if name in _CUSTOM_FREEFORM:
        return _remove_freeform(items, _CUSTOM_FREEFORM[name])
    raise ValueError(f"no MP4 atom mapping for field {name!r}")
