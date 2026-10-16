"""
fablegear / anvil / vorbis_fields.py

TrackFields <-> Vorbis comment mapping. Shared by flac.py and ogg.py, because
both containers carry the identical comment-list structure (vorbis_comment.py)
-- only the surrounding container differs.

Vorbis comments have no native/non-native split the way ID3 does (TIT2 vs
TXXX): every field is just a "KEY=value" string, case-insensitive by spec.
That makes this module simpler than id3.py's frame mapping, at the cost of
losing ID3's spec-enforced integer BPM -- which is exactly why keeping full
BPM precision here needs no companion field the way TBPM does: a Vorbis "bpm"
comment is not spec-typed to an integer, so writing "128.5" directly is legal
and lossless.

Real-world grounding: `bpm` and `initialkey` (both lowercase, no separator)
are the exact spellings audio_processor.py's `_write_tags()` already uses for
FLAC/OGG today (audio.tags["bpm"] / audio.tags["initialkey"]). Anvil keeps
those spellings so files already tagged by the live app are read correctly,
and so nothing downstream that greps for a lowercase "bpm" comment breaks.
The DJ-native fields have no prior convention to match, so they follow the
same upper-case, no-separator spelling id3.py already uses for TXXX
descriptions -- one compatibility matrix, one set of names, across every
container Anvil speaks.
"""

from __future__ import annotations

import logging
from typing import Any

from anvil.schema import TrackFields

log = logging.getLogger(__name__)

Comments = list[tuple[str, str]]

# Native-ish text fields. Not spec-enforced the way ID3's TIT2/TPE1/TALB are,
# but TITLE/ARTIST/ALBUM are the Vorbis comment spec's own suggested field
# names, and every real tagger honours them.
_TEXT_FIELDS: dict[str, str] = {
    "title": "TITLE",
    "artist": "ARTIST",
    "album": "ALBUM",
}

# Matches the live app's existing on-disk spelling -- see module docstring.
_KEY_FIELD = "initialkey"
_BPM_FIELD = "bpm"

# DJ-native fields: no prior convention anywhere in this codebase to preserve,
# so they take the same spelling id3.py's TXXX descriptions use.
_CUSTOM_FIELDS: dict[str, str] = {
    "mix_descriptor": "MIXDESCRIPTOR",
    "track_role": "TRACKROLE",
    "energy_level": "ENERGYLEVEL",
    "downbeat_offset": "DOWNBEATOFFSET",
    "time_signature": "TIMESIGNATURE",
}

_INT_FIELDS = frozenset({"energy_level"})
_FLOAT_FIELDS = frozenset({"downbeat_offset"})


def _get_first(comments: Comments, key: str) -> str | None:
    key = key.lower()
    for k, v in comments:
        if k.lower() == key:
            return v
    return None


def _remove_all(comments: Comments, key: str) -> Comments:
    key = key.lower()
    return [(k, v) for k, v in comments if k.lower() != key]


def set_single(comments: Comments, key: str, value: str) -> Comments:
    """Replace every comment under `key` (any casing) with exactly one."""
    return [*_remove_all(comments, key), (key, value)]


def remove_field(comments: Comments, key: str) -> Comments:
    return _remove_all(comments, key)


def _coerce(name: str, raw: str) -> Any:
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


def _format_value(name: str, value: Any) -> str:
    if name in _INT_FIELDS:
        return str(int(value))
    if name in _FLOAT_FIELDS:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    return str(value)


def fields_from_comments(comments: Comments) -> TrackFields:
    """Parse a comment list into the normalized schema."""
    fields = TrackFields()

    for name, key in _TEXT_FIELDS.items():
        raw = _get_first(comments, key)
        if raw is not None:
            setattr(fields, name, _coerce(name, raw))

    key_raw = _get_first(comments, _KEY_FIELD)
    if key_raw is not None:
        fields.initial_key = _coerce("initial_key", key_raw)

    bpm_raw = _get_first(comments, _BPM_FIELD)
    if bpm_raw is not None:
        try:
            fields.bpm = float(bpm_raw.strip())
        except ValueError:
            log.debug("bpm comment %r is not a number", bpm_raw)

    for name, key in _CUSTOM_FIELDS.items():
        raw = _get_first(comments, key)
        if raw is not None:
            setattr(fields, name, _coerce(name, raw))

    return fields


def apply_field(comments: Comments, name: str, value: Any) -> Comments:
    """Write one schema field into the comment list, returning the new list."""
    if name == "bpm":
        # No companion field needed: unlike TBPM/tmpo, a Vorbis "bpm" comment
        # is not spec-typed to an integer, so full precision fits directly.
        text = f"{float(value):.4f}".rstrip("0").rstrip(".")
        return set_single(comments, _BPM_FIELD, text)

    if name == "initial_key":
        return set_single(comments, _KEY_FIELD, str(value))

    if name in _TEXT_FIELDS:
        return set_single(comments, _TEXT_FIELDS[name], str(value))

    if name in _CUSTOM_FIELDS:
        return set_single(comments, _CUSTOM_FIELDS[name], _format_value(name, value))

    raise ValueError(f"no vorbis-comment mapping for field {name!r}")


def remove_schema_field(comments: Comments, name: str) -> Comments:
    if name == "bpm":
        return remove_field(comments, _BPM_FIELD)
    if name == "initial_key":
        return remove_field(comments, _KEY_FIELD)
    if name in _TEXT_FIELDS:
        return remove_field(comments, _TEXT_FIELDS[name])
    if name in _CUSTOM_FIELDS:
        return remove_field(comments, _CUSTOM_FIELDS[name])
    raise ValueError(f"no vorbis-comment mapping for field {name!r}")
