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
class TempoCheckpoint:
    """
    One long-baseline confirmation reading: "if the initial BPM guess is right, beat
    number `beat` should fall at `anchor_time` seconds -- what does a fresh, independent
    analysis of a short window there actually say?"

    `agrees` is None if the projected anchor falls beyond the track's duration and was
    never checked, True/False once it was.
    """

    beat: int
    anchor_time: float
    measured_bpm: float | None
    agrees: bool | None


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

    # Populated only when analyze(want=...) includes "downbeat_offset" and/or
    # "time_signature" -- both come from the same beat-grid pass (iron.beats.
    # detect_beat_grid), so requesting either one populates both, plus this shared
    # confidence. Requires bpm to already be found; left None otherwise, same "no value
    # beats a wrong value" stance as everywhere else in Iron.
    downbeat_offset: float | None = None
    time_signature: str | None = None
    beat_grid_confidence: float | None = None

    # Populated only when analyze(..., verify_stability=True) actually ran the check.
    # bpm_stable: None = not checked; True = every reachable checkpoint agreed with the
    # initial reading; False = at least one checkpoint disagreed -- the file likely has
    # more than one tempo section (a DJ mix, live set, or a track with a real tempo
    # change), or the initial guess was an octave/ratio error that a long baseline
    # exposes. checkpoints holds the individual readings either way, for a caller that
    # wants to see the actual per-section data rather than just the verdict.
    bpm_stable: bool | None = None
    checkpoints: list[TempoCheckpoint] = field(default_factory=list)

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

        return TrackFields(
            bpm=self.bpm,
            initial_key=self.initial_key,
            downbeat_offset=self.downbeat_offset,
            time_signature=self.time_signature,
        )
