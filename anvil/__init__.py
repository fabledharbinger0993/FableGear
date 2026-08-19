"""
Anvil -- audio tag I/O for FableGear.

Anvil reads and writes what a track says about itself. It does not listen to
audio: tempo, beat, and key detection belong to Iron, a separate package, and
arrive here as ordinary candidate values. Anvil cannot tell whether a value
came from Iron or from a caller typing it in by hand, and treats both the
same way.

Container family A only, for now: ID3v2.3 / v2.4 over MP3, WAV, and AIFF.

    from anvil import read_fields, write_fields, TrackFields

    fields = read_fields(path)
    result = write_fields(path, TrackFields(bpm=128.5), force={"bpm"})
    result.db_companion   # {"BPM": 12850} -- ready for master.db, never written here
"""

# Submodules are re-exported explicitly. They are reachable anyway once the
# imports below register them in sys.modules, but relying on that side effect
# leaves `anvil.api` untyped to a checker and unobvious to a reader.
from anvil import api, containers, errors, id3, safety, schema
from anvil.api import (
    WriteResult,
    clear_fields,
    read_cover_art,
    read_fields,
    read_tag,
    write_fields,
)
from anvil.errors import (
    AnvilError,
    CorruptHeader,
    NoTagBlock,
    UnsupportedFormat,
    WriteVerificationFailed,
)
from anvil.schema import (
    SYNC_BOTH_APPLIED,
    SYNC_DB_LOCKED,
    SYNC_DIVERGENT,
    SYNC_FILE_ONLY,
    TrackFields,
    db_companion,
)

__version__ = "0.1.0"

__all__ = [
    "SYNC_BOTH_APPLIED",
    "SYNC_DB_LOCKED",
    "SYNC_DIVERGENT",
    "SYNC_FILE_ONLY",
    "AnvilError",
    "CorruptHeader",
    "NoTagBlock",
    "TrackFields",
    "UnsupportedFormat",
    "WriteResult",
    "WriteVerificationFailed",
    "api",
    "clear_fields",
    "containers",
    "db_companion",
    "errors",
    "id3",
    "read_cover_art",
    "read_fields",
    "read_tag",
    "safety",
    "schema",
    "write_fields",
]
