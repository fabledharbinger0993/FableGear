"""
fablegear / iron / schema.py

IronResult: what Iron's analysis produced for one file, and how it hands off to Anvil.

Iron does not listen to a file's tags and does not write anything -- it only ever produces
candidate values. Anvil's own README states the contract explicitly: "Anvil cannot tell
whether a value came from Iron or from a caller typing it in by hand, and treats both the
same way." `to_track_fields()` is the literal hand-off point that promise describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IronResult:
    """
    Candidate analysis values for one file. Every field is optional -- a clip Iron could
    not find a reliable tempo or key in leaves that field None rather than guessing, the
    same "no value beats a wrong value" stance Anvil takes on tag writes.
    """

    bpm: float | None = None
    bpm_confidence: float | None = None
    initial_key: str | None = None
    key_confidence: float | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def to_track_fields(self) -> Any:
        """
        Return an `anvil.TrackFields` populated with whatever this result found, ready to
        hand to `anvil.write_fields()`. Imports Anvil lazily so `iron` never requires it at
        import time -- only a caller that wants the hand-off pays for it.
        """
        from anvil import TrackFields

        return TrackFields(bpm=self.bpm, initial_key=self.initial_key)
