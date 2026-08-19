"""
fablegear / anvil / schema.py

The normalized field schema, and the database-shaped companion values.

This is the layer that does not exist anywhere today -- the mapping between a
canonical field name and its per-container representation is currently smeared
across audio_processor.py as an is_vorbis / is_mp4 / else branch repeated at
every write site.

Two ideas live here:

  TrackFields  -- what a track says about itself, container-independent.
  db_companion -- the same facts in the shape Rekordbox's master.db wants.

The second exists because a file and a DjmdContent row are two independent
sources of truth that hold the same fact in different units. BPM in a file tag
is a human-scale string ("128"); DjmdContent.BPM is an integer scaled by 100
(12800). Key in a file tag is a notation string ("Am"); DjmdContent.KeyID is a
foreign key into a per-library DjmdKey table. Anvil computes both shapes once,
here, so no caller re-derives `round(bpm * 100)` by hand at the next call site.

Anvil never WRITES the database side. It only says what the value would be.
The actual master.db write stays in db_connection.py / key_mapper.py, behind
the same Rekordbox-must-be-closed gate as everything else.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from typing import Any

# ─── Sync state ───────────────────────────────────────────────────────────────
#
# The structured replacement for routes_mobile.py's hand-built db_note string
# and `status = "complete_partial" if db_note else "complete"`.
#
# Anvil itself only ever returns FILE_ONLY: it wrote the file and computed the
# companion, and that is the whole of its job. A caller that goes on to apply
# the companion to master.db upgrades the state to BOTH_APPLIED; one that finds
# Rekordbox open reports DB_LOCKED. DIVERGENT is for a drift check that found
# the two sources already disagreeing before any write.

SYNC_FILE_ONLY = "file_only"
SYNC_BOTH_APPLIED = "both_applied"
SYNC_DB_LOCKED = "db_locked"
SYNC_DIVERGENT = "divergent"


# ─── The schema ───────────────────────────────────────────────────────────────

@dataclass
class TrackFields:
    """
    Canonical, container-independent track metadata.

    Every field is optional. `None` means "this field was not present / is not
    being written" -- it never means "clear this field". Clearing is a separate,
    explicit operation, so a partially-populated TrackFields passed to a write
    can never silently blank out everything it does not mention.
    """

    # Bibliographic. `title` is inviolate: Anvil never derives, infers, or
    # rewrites it from an external match, because Anvil performs no external
    # matching. Version identity lives in mix_descriptor, never smuggled into
    # or stripped out of the title.
    title: str | None = None
    artist: str | None = None
    album: str | None = None

    # Musical. What a DJ actually mixes on.
    bpm: float | None = None
    initial_key: str | None = None

    # DJ-native. None of these have a home in a bibliographic schema like
    # MusicBrainz/Picard's, which is exactly why they are first-class here.
    mix_descriptor: str | None = None   # "Extended Mix", "VIP", "Radio Edit"
    track_role: str | None = None       # Full / Instrumental / Acapella / ...
    energy_level: int | None = None     # small integer scale, set-planning aid

    # Rhythm. bpm + downbeat_offset is a complete linear grid for the
    # constant-tempo majority of a DJ library, and both are small enough to be
    # real tag fields. A variable-tempo track's full beat map is NOT tag-shaped
    # data in any container -- it belongs in db_companion, headed for the
    # ANLZ/Rekordbox-DB layer. Anvil never invents a tag format for it.
    downbeat_offset: float | None = None   # seconds from file start to beat 1
    time_signature: str | None = None      # "4/4" unless proven otherwise

    def present(self) -> dict[str, Any]:
        """Return only the fields that carry a value."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    def is_empty(self) -> bool:
        return not self.present()

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in dataclass_fields(cls))


# ─── Database companion ───────────────────────────────────────────────────────

def db_companion(fields: TrackFields) -> dict[str, Any]:
    """
    Translate the file-shaped facts in `fields` into master.db-shaped values.

    Returns only the keys the input actually carries. Keys are named for their
    destination so a caller does not have to remember the mapping:

        DjmdContent.BPM      int, BPM * 100     (12800 for 128.0 BPM)
        key_notation         str, ready to hand to key_mapper.resolve_key_id()
                             -- deliberately NOT a KeyID, because resolving one
                             requires a live DjmdKey table and a get-or-create
                             against per-library mutable state. Anvil does not
                             own that; key_mapper.py does.

    This function is the single home of `round(bpm * 100)`. That expression
    currently lives inline at routes_mobile.py:621 and would otherwise be
    reimplemented at every future site that writes a tempo to both surfaces.
    """
    out: dict[str, Any] = {}

    if fields.bpm is not None:
        # Rekordbox stores centi-BPM as an integer. Rounding at the last
        # possible moment keeps 128.5 -> 12850 rather than 12800.
        out["BPM"] = round(fields.bpm * 100)

    if fields.initial_key is not None:
        out["key_notation"] = fields.initial_key

    return out
