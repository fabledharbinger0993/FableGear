"""
fablegear / anvil / api.py

The public surface: read_fields() and write_fields().

This is the layer that replaces the is_vorbis / is_mp4 / else branch in
audio_processor.py::_write_tags(). A caller names a field; Anvil decides how
that field is spelled in this particular container. Nothing above this line
should ever construct a frame object or know that TBPM exists.

Two rules govern every write, and they are properties of the library rather
than flags a caller has to remember:

  Field-level merge. Writing bpm touches bpm and nothing else. A hand-set
  energy_level or mix_descriptor survives a later tempo re-analysis untouched.

  A candidate is not an overwrite. A value arriving for a field that already
  has one is kept out unless the caller explicitly forces it -- the same
  semantics audio_processor.py's force_bpm / force_key flags already assume,
  now enforced in one place instead of re-checked at each call site. Anvil
  cannot tell whether a candidate came from Iron's analysis or from a caller
  typing a number by hand, and deliberately treats both identically.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anvil import containers, id3
from anvil.errors import NoTagBlock
from anvil.safety import atomic_write, verify_fields
from anvil.schema import SYNC_FILE_ONLY, TrackFields, db_companion

log = logging.getLogger(__name__)

# ─── Field <-> frame mapping ──────────────────────────────────────────────────
#
# Standard text frames. These are native ID3 -- the green column in the
# compatibility matrix.

_TEXT_FRAMES: dict[str, str] = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "initial_key": "TKEY",
}

# DJ-native fields have no standard frame, so they ride TXXX frames keyed by
# description. Non-native, and the matrix says so rather than hiding it behind
# the same green dot as TIT2.
_TXXX_FIELDS: dict[str, str] = {
    "mix_descriptor": "MIXDESCRIPTOR",
    "track_role": "TRACKROLE",
    "energy_level": "ENERGYLEVEL",
    "downbeat_offset": "DOWNBEATOFFSET",
    "time_signature": "TIMESIGNATURE",
}

# BPM is stored twice, on purpose.
#
# TBPM is defined by the spec as an integer, and every other tool in the chain
# reads it -- including Rekordbox. Writing "128.5" there is common in DJ tools
# but is not what the spec says, and some readers reject it.
#
# A DJ library cannot afford to round tempo away: half a BPM drifts a full beat
# inside a few bars. So Anvil writes the spec-compliant rounded integer to
# TBPM for everyone else, and keeps full precision in a TXXX frame for itself.
# Reads prefer the precise value when the two agree to within a rounding step,
# so a third-party tool that rewrites TBPM alone is still believed.
_BPM_PRECISE = "BPM_PRECISE"

_INT_FIELDS = frozenset({"energy_level"})
_FLOAT_FIELDS = frozenset({"downbeat_offset"})


@dataclass
class WriteResult:
    """What a write actually did."""

    path: Path
    written: dict[str, Any] = field(default_factory=dict)
    kept: dict[str, Any] = field(default_factory=dict)
    sync_state: str = SYNC_FILE_ONLY
    db_companion: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    container: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.written)


# ─── Reading ──────────────────────────────────────────────────────────────────

def _coerce(name: str, raw: str) -> Any:
    """Convert a tag's string payload to the schema's type for that field."""
    text = raw.strip()
    if not text:
        return None
    if name in _INT_FIELDS:
        try:
            return int(float(text))
        except ValueError:
            log.debug("field %s: %r is not an integer", name, text)
            return None
    if name in _FLOAT_FIELDS:
        try:
            return float(text)
        except ValueError:
            log.debug("field %s: %r is not a number", name, text)
            return None
    return text


def _read_txxx_map(tag: id3.ID3Tag) -> dict[str, str]:
    """Collect every TXXX frame as {description: value}."""
    out: dict[str, str] = {}
    for frame in tag.get_all("TXXX"):
        description, value = id3.decode_txxx(frame.data)
        if description:
            out[description.upper()] = value
    return out


def _fields_from_tag(tag: id3.ID3Tag) -> TrackFields:
    fields = TrackFields()

    for name, frame_id in _TEXT_FRAMES.items():
        frame = tag.get(frame_id)
        if frame is not None:
            setattr(fields, name, _coerce(name, id3.decode_text(frame.data)))

    txxx = _read_txxx_map(tag)
    for name, description in _TXXX_FIELDS.items():
        if description in txxx:
            setattr(fields, name, _coerce(name, txxx[description]))

    fields.bpm = _read_bpm(tag, txxx)
    return fields


def _read_bpm(tag: id3.ID3Tag, txxx: dict[str, str]) -> float | None:
    """
    Resolve BPM from TBPM and the precise companion frame.

    Tolerates a decimal in TBPM even though the spec says integer -- plenty of
    DJ software writes one, and refusing to read it would lose real data for
    no benefit.
    """
    coarse: float | None = None
    frame = tag.get("TBPM")
    if frame is not None:
        text = id3.decode_text(frame.data).strip()
        if text:
            try:
                coarse = float(text)
            except ValueError:
                log.debug("TBPM %r is not a number", text)

    precise: float | None = None
    if _BPM_PRECISE in txxx:
        try:
            precise = float(txxx[_BPM_PRECISE])
        except ValueError:
            log.debug("precise BPM %r is not a number", txxx[_BPM_PRECISE])

    if precise is None:
        return coarse
    if coarse is None:
        return precise

    # Trust the precise value only while it still agrees with TBPM. If another
    # tool rewrote TBPM alone, our stale companion must not override it.
    if abs(round(precise) - coarse) <= 0.5:
        return precise
    log.debug(
        "TBPM %s disagrees with stored precise BPM %s -- trusting TBPM",
        coarse, precise,
    )
    return coarse


def read_tag(path: Path) -> tuple[str, id3.ID3Tag | None, bytes]:
    """Return (container_kind, parsed_tag_or_None, raw_file_bytes)."""
    path = Path(path)
    data = path.read_bytes()
    kind = containers.sniff(data)
    blob = containers.extract_id3(data, kind)
    tag = id3.parse_tag(blob) if blob else None
    return kind, tag, data


def read_fields(path: Path) -> TrackFields:
    """
    Read a file's tags into the normalized schema.

    Returns an empty TrackFields for a supported container with no tag block --
    "this file says nothing about itself" is an ordinary state for a fresh rip,
    not an error. Use read_tag() when the distinction matters.
    """
    _kind, tag, _data = read_tag(path)
    if tag is None:
        return TrackFields()
    return _fields_from_tag(tag)


def read_cover_art(path: Path) -> tuple[bytes, str] | None:
    """Return (image_bytes, mime_type) from the first APIC frame, or None."""
    _kind, tag, _data = read_tag(path)
    if tag is None:
        return None
    frame = tag.get("APIC")
    if frame is None:
        return None
    return id3.decode_apic(frame.data)


# ─── Writing ──────────────────────────────────────────────────────────────────

def _format_value(name: str, value: Any) -> str:
    if name in _INT_FIELDS:
        return str(int(value))
    if name in _FLOAT_FIELDS:
        # Trim trailing zeros so 0.5 does not become "0.500000".
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    return str(value)


def _forced(force: bool | Iterable[str], name: str) -> bool:
    if isinstance(force, bool):
        return force
    return name in set(force)


def _apply(tag: id3.ID3Tag, name: str, value: Any) -> None:
    """Write one schema field into the tag, choosing its ID3 representation."""
    if name == "bpm":
        tag.set("TBPM", id3.encode_text(str(round(float(value))), tag.version))
        _set_txxx(tag, _BPM_PRECISE, f"{float(value):.4f}".rstrip("0").rstrip("."))
        return

    if name in _TEXT_FRAMES:
        tag.set(_TEXT_FRAMES[name], id3.encode_text(str(value), tag.version))
        return

    if name in _TXXX_FIELDS:
        _set_txxx(tag, _TXXX_FIELDS[name], _format_value(name, value))
        return

    raise ValueError(f"no ID3 mapping for field {name!r}")


def _set_txxx(tag: id3.ID3Tag, description: str, value: str) -> None:
    """
    Set a TXXX frame by description.

    TXXX frames are only unique per description, not per frame id, so a blanket
    remove("TXXX") would delete every user-defined field in the file --
    including ones written by other tools that have nothing to do with us.
    """
    kept = []
    for frame in tag.frames:
        if frame.id != "TXXX":
            kept.append(frame)
            continue
        existing, _value = id3.decode_txxx(frame.data)
        if existing.upper() != description.upper():
            kept.append(frame)
    kept.append(
        id3.Frame("TXXX", id3.encode_txxx(description, value, tag.version))
    )
    tag.frames = kept


def write_fields(
    path: Path,
    fields: TrackFields,
    *,
    force: bool | Iterable[str] = False,
    version: int | None = None,
    checkpoint: Callable[[Path], None] | None = None,
    verify: bool = True,
) -> WriteResult:
    """
    Merge `fields` into the file's tags.

    force=False        never replace a field that already has a value
    force=True         replace everything supplied
    force={"bpm", ...} replace only the named fields

    `version` pins the ID3 major version to write (3 or 4). By default an
    existing tag keeps its own version -- silently upgrading someone's v2.3 tag
    to v2.4 could break whatever they were reading it with -- and a new tag is
    written as v2.4.
    """
    path = Path(path)
    kind, tag, data = read_tag(path)

    if tag is None:
        # A file with no tag block gets one. This is the add_tags() step that
        # audio_processor.py performs by hand today, with a warning comment
        # about formats where it may not work.
        tag = id3.ID3Tag(version=version or 4)
    elif version is not None and version != tag.version:
        tag.version = version

    existing = _fields_from_tag(tag)
    incoming = fields.present()

    written: dict[str, Any] = {}
    kept: dict[str, Any] = {}

    for name, value in incoming.items():
        current = getattr(existing, name, None)
        if current is not None and not _forced(force, name):
            kept[name] = current
            continue
        _apply(tag, name, value)
        written[name] = value

    result = WriteResult(
        path=path,
        written=written,
        kept=kept,
        sync_state=SYNC_FILE_ONLY,
        db_companion=db_companion(TrackFields(**written)) if written else {},
        container=kind,
    )

    if not written:
        # Nothing to do. Rewriting the file to produce identical bytes would be
        # pure risk for no gain.
        return result

    new_blob = id3.serialize_tag(tag)
    new_data = containers.install_id3(data, kind, new_blob)

    verifier: Callable[[], None] | None = None
    if verify:
        def _run_verify() -> None:
            verify_fields(lambda: read_fields(path), written, path)

        verifier = _run_verify

    atomic_write(path, new_data, verify=verifier, checkpoint=checkpoint)
    result.verified = verify
    return result


def clear_fields(path: Path, names: Iterable[str], **kwargs: Any) -> WriteResult:
    """
    Remove specific fields. Separate from write_fields() on purpose: passing
    None in a TrackFields means "not writing this", never "erase this", so
    erasure has to be asked for by name.
    """
    path = Path(path)
    kind, tag, data = read_tag(path)
    if tag is None:
        raise NoTagBlock(f"{path.name} has no tag block to clear")

    removed: dict[str, Any] = {}
    for name in names:
        if name == "bpm":
            tag.remove("TBPM")
            _remove_txxx(tag, _BPM_PRECISE)
            removed[name] = None
        elif name in _TEXT_FRAMES:
            tag.remove(_TEXT_FRAMES[name])
            removed[name] = None
        elif name in _TXXX_FIELDS:
            _remove_txxx(tag, _TXXX_FIELDS[name])
            removed[name] = None
        else:
            raise ValueError(f"no ID3 mapping for field {name!r}")

    if removed:
        new_data = containers.install_id3(data, kind, id3.serialize_tag(tag))
        atomic_write(path, new_data, checkpoint=kwargs.get("checkpoint"))

    return WriteResult(path=path, written={}, kept={}, container=kind)


def _remove_txxx(tag: id3.ID3Tag, description: str) -> None:
    kept = []
    for frame in tag.frames:
        if frame.id == "TXXX":
            existing, _value = id3.decode_txxx(frame.data)
            if existing.upper() == description.upper():
                continue
        kept.append(frame)
    tag.frames = kept


__all__ = [
    "WriteResult",
    "clear_fields",
    "read_cover_art",
    "read_fields",
    "read_tag",
    "write_fields",
]
