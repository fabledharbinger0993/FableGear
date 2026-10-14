"""
fablegear / quench / schema.py

What Quench measures and what it decided to do about it. Plain dataclasses, no behaviour,
so a caller (Forge, the Normalize preview, a CLI) can log or persist them without knowing
how ffmpeg was driven.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Below this, applying gain isn't worth a full re-encode: the change is inaudible, and on a
# lossy format the rewrite costs a generation of quality.
MIN_GAIN_DB: float = 0.1


@dataclass(frozen=True)
class Loudness:
    """One EBU R128 measurement of a file (or a window of it)."""

    integrated_lufs: float
    true_peak_dbtp: float | None  # None when ffmpeg's loudnorm JSON carried no usable value

    def as_tuple(self) -> tuple[float, float | None]:
        return self.integrated_lufs, self.true_peak_dbtp


@dataclass(frozen=True)
class GainPlan:
    """
    The gain Quench will apply, and why it differs (if it does) from the gain that would
    land exactly on the target.

    requested_db: target - measured, the naive answer.
    applied_db:   what will actually be applied after the true-peak cap. 0.0 means "nothing
                  safe to do" (no headroom), and the caller skips the rewrite.
    capped:       True when the true-peak ceiling reduced the boost.
    """

    requested_db: float
    applied_db: float
    capped: bool

    @property
    def worth_applying(self) -> bool:
        return abs(self.applied_db) >= MIN_GAIN_DB


@dataclass
class QuenchResult:
    """
    Outcome of `quench.level()` on one file.

    `skipped` with a `skip_reason` is the normal, non-error outcome for a file that is
    already in tolerance, has no headroom, or is a container Quench will not rewrite.
    `levelled` is True only when the file on disk was actually replaced.
    """

    path: Path
    before: Loudness | None = None
    after: Loudness | None = None
    plan: GainPlan | None = None
    levelled: bool = False
    skipped: bool = False
    skip_reason: str = ""  # "unsupported_format" | "in_tolerance" | "no_headroom" | ""
    error: str = ""
