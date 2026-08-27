"""
fablegear / iron / beats.py

Beat-grid anchor (downbeat_offset) and simple meter (time_signature) detection, built on
top of iron.tempo's already-decided BPM.

The approach is inspired by two published techniques surveyed (not copied from) while
comparing Iron against other tempo/beat tools: BTT's (Krzyzaniak, MIT) cumulative
beat-strength beat-tracking, itself built on Stark's 2011 PhD thesis, for turning a known
tempo into actual beat positions and predictions; and loop-tempo-estimator's (Audacity,
GPL -- read for its published method only, never its code, the same posture Iron already
takes toward essentia) tatum-hypothesis idea of scoring small-integer grouping hypotheses
by how well a signal's accents line up with each one. Both are reimplemented from scratch
here, in numpy, adapted to what Iron already has rather than ported line-for-line.

The DP beat-phase-locking primitive this module calls, `iron.dsp.track_beats`, already
existed before this module and is already validated for exactly this use (see its own
docstring and tests/test_iron_dsp.py::test_track_beats_locks_onto_a_known_period): given a
single, already-decided period, it returns the actual beat POSITIONS, not just an
aggregate periodicity statistic. What was tried and reverted in iron/tempo.py was using
its score to pick BETWEEN candidate periods -- a different question from the one this
module asks, which only ever calls it once, with the one period tempo.py already chose.

Scope, deliberately narrow: this distinguishes 4/4 (the default, matching
anvil.TrackFields.time_signature's own "4/4 unless proven otherwise" comment) from 3/4,
and nothing else. Compound meters (6/8 and similar) are not attempted -- iron/tempo.py's
own module docstring already documents a reverted attempt at compound-meter (3-against-2)
disambiguation that didn't hold up against real tracks, and a bar-level 3-vs-4 check on
already-tracked beats has nowhere near enough signal to also separate simple from compound
meter. Also out of scope, same as everywhere else in Iron v1: variable-tempo tracks --
anvil.TrackFields.downbeat_offset's own comment states a full beat map for those "is NOT
tag-shaped data... belongs in db_companion."

`_detect_downbeat_class`'s premise -- that the true downbeat carries a stronger accent than
the other beats in its bar -- is real (DJ productions do genuinely emphasize "the one"),
but two pitfalls, both found by testing against a fixture with a known amplitude accent,
had to be fixed before that premise showed up reliably in a measurement:

  1. iron.dsp.onset_envelope's log-compressed (log1p) broadband spectral flux -- exactly
     right for FINDING onsets across a whole track, see its own docstring for why --
     compresses a same-instrument amplitude accent almost to nothing. A dedicated,
     non-log-compressed, band-restricted energy feature (iron.dsp.band_energy, passed in
     as `accent_env`) survives it far better: energy scales with amplitude squared, so a
     1.3x accent is a detectable ~1.7x energy difference, not a fraction-of-a-percent flux
     difference.
  2. Sampling that energy feature at the tracked beat FRAME (the one onset_env's flux
     phase-locked to) still failed: a percussive transient whose duration spans several
     analysis hops makes flux peak several frames AFTER the transient's true attack (the
     STFT window keeps gaining more of the still-decaying transient for a few hops before
     it starts losing more than it gains), so the flux-based frame lands well into the
     transient's decay by the time a band-energy feature -- which itself peaks right at
     the true attack -- gets sampled there. `_accent_strength` fixes this by searching
     backward from the tracked frame for the feature's own local peak.

Without both fixes, the same fixture measured no reliable accent at all (~7% spread, wrong
winner); with both, it recovers a clean ~1.6-1.7x separation. Still genuinely unvalidated
against real music -- the same "not yet run through the ground-truth benchmark" position
iron/README.md already states for bpm and initial_key -- so `beat_grid_confidence` exists
for a caller to threshold on rather than trust blindly.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

# Below this many phase-locked beats, a beats-per-bar guess or downbeat class has too few
# repetitions to trust -- 2 full 4-beat bars is the minimum for the bar-level
# autocorrelation lag-4 check (or lag-3, for the 3/4 candidate) to mean anything at all,
# rather than reacting to noise.
_MIN_BEATS_FOR_GRID = 8

# How much of the beat-strength signal's own zero-lag energy the beats-per-bar=3 candidate
# must carry, over and above simply outscoring 4, before overriding the "4/4 unless proven
# otherwise" default. Deliberately conservative -- an informal prior in the same spirit as
# iron.tempo's genre bands, not a rigorously validated threshold: a 3/4 DJ track is real
# but rare, and 4/4 is right far more often than a marginal 3-vs-4 signal is wrong.
_METER_3_MIN_SCORE = 0.2


def _beat_strength(onset_env: np.ndarray, beat_frames: list[int]) -> np.ndarray:
    """Onset-envelope value at each phase-locked beat frame -- how strong an accent, if
    any, landed on that beat. Used both to find the bar-level meter and to pick the
    loudest (downbeat) position among each candidate bar's beats. This is the fallback
    used when no `accent_env` is supplied -- see _accent_strength for the more
    accent-discriminating alternative and this module's own docstring for why sampling
    directly at the tracked frame is the weaker of the two."""
    return np.array([onset_env[b] for b in beat_frames], dtype=np.float64)


def _accent_strength(
    accent_env: np.ndarray, beat_frames: list[int], period_frames: float, *, forward_margin: int = 2
) -> np.ndarray:
    """
    Peak `accent_env` value near each tracked beat, searching BACKWARD from the tracked
    frame by up to half a beat period. See this module's docstring for why the search
    (not a direct sample at the tracked frame) is necessary: onset_env's flux-based phase
    lock lands several frames after a percussive transient's true attack, but a raw
    band-energy feature (iron.dsp.band_energy) peaks AT the attack and decays immediately
    after, so sampling it only at the tracked frame can land well into that decay. Capped
    at half a beat period so the search can't reach back far enough to pick up the
    PREVIOUS beat's own transient instead.
    """
    back = max(1, round(period_frames / 2))
    n = accent_env.shape[0]
    out = np.empty(len(beat_frames), dtype=np.float64)
    for i, b in enumerate(beat_frames):
        lo, hi = max(0, b - back), min(n, b + forward_margin + 1)
        out[i] = float(accent_env[lo:hi].max()) if hi > lo else 0.0
    return out


def _detect_beats_per_bar(strength: np.ndarray) -> tuple[int, float]:
    """
    Return (beats_per_bar, confidence). See this module's docstring for why the only two
    candidates are 3 and 4, and why 4 is the default.
    """
    n = strength.shape[0]
    if n < _MIN_BEATS_FOR_GRID:
        return 4, 0.0

    acf = dsp.autocorrelate(strength - strength.mean())
    if acf.shape[0] <= 4 or acf[0] <= 0:
        return 4, 0.0

    zero_lag = float(acf[0])
    score_3 = float(acf[3])
    score_4 = float(acf[4])

    if score_3 > score_4 and (score_3 / zero_lag) > _METER_3_MIN_SCORE:
        return 3, float(np.clip(score_3 / zero_lag, 0.0, 1.0))
    return 4, float(np.clip(max(score_4, 0.0) / zero_lag, 0.0, 1.0))


def _detect_downbeat_class(strength: np.ndarray, beats_per_bar: int) -> tuple[int, float]:
    """
    Which of `beats_per_bar` beat positions in the bar is the downbeat (beat 1) -- the
    class whose beats carry the strongest average accent. This is the same assumption a
    DJ kick pattern's own emphasis on "the one" makes real, and the same convention
    tests/test_iron_tempo.py's own fixture generator already bakes in
    (`accent = 1.3 if i % 4 == 0`).
    """
    class_means = np.array([strength[c::beats_per_bar].mean() for c in range(beats_per_bar)])
    best = int(np.argmax(class_means))
    total = float(class_means.sum())
    confidence = float(class_means[best] / total) if total > 0 else 0.0
    return best, confidence


def detect_beat_grid(
    onset_env: np.ndarray,
    frame_rate: float,
    bpm: float,
    *,
    window_start_s: float = 0.0,
    accent_env: np.ndarray | None = None,
) -> tuple[float, str, float] | None:
    """
    Return (downbeat_offset, time_signature, confidence), or None if too few beats were
    reliably tracked to trust a grid at all.

    `downbeat_offset` is seconds from the FILE's own t=0 to the earliest downbeat --
    matching anvil.TrackFields.downbeat_offset's contract. It's computed by phase-locking
    iron.dsp.track_beats to the already-decided `bpm` (see this module's docstring for why
    that's the validated use of track_beats, unlike using its score to compare candidate
    periods), picking the loudest beat-class as the downbeat, then folding that position
    back modulo one bar length so it lands at the first such downbeat in the whole file,
    not just the first one inside the analyzed window. `window_start_s` is how far into the
    file the passed-in `onset_env` actually starts (iron.api decodes the track's BODY, not
    always from 0:00 -- see iron/api.py's _pick_body_window). This assumes constant tempo
    for the whole file, the same scope anvil.TrackFields.downbeat_offset's own comment
    states.

    `accent_env` is an optional, more accent-discriminating signal (intended caller:
    iron.dsp.band_energy restricted to a kick drum's band) used for the downbeat/meter
    SCORING step only -- phase-locking always uses `onset_env`, since that's what's
    validated for finding WHERE beats are (see this module's docstring). Falls back to
    sampling onset_env directly at each tracked frame when not given -- weaker (see
    _beat_strength), but keeps this function self-contained for callers/tests that only
    have a plain onset envelope.

    Costs one dynamic-programming pass over the analyzed window (iron.dsp.track_beats,
    O(n * period) in the number of onset-envelope frames) -- cheap on short clips, a real
    but bounded added cost (roughly a second, not tens) on the longest body window
    iron.api.analyze() can decode for a full track.
    """
    if bpm <= 0 or frame_rate <= 0 or onset_env.shape[0] == 0:
        return None

    peak = float(onset_env.max())
    if peak <= 0:
        return None

    # dsp.track_beats' dynamic-programming penalty is additive and independent of the
    # onset envelope's absolute units, so it implicitly assumes a unit-scale signal --
    # true of its own validated test fixture (a 0/1 pulse train), NOT true of
    # dsp.onset_envelope's raw spectral-flux output, whose magnitude depends on the
    # track's loudness and spectral content. Fed raw, the penalty term becomes
    # negligible next to the onset gains and the tracker "double-times": it locks onto
    # both true beats and any comparably strong off-beat content (e.g. a hi-hat exactly
    # halfway between kicks) instead of one beat per period. Found by testing against a
    # kick+hi-hat fixture, the same way iron.tempo's own bugs were found against its BPM
    # sweep -- normalizing to unit max before tracking restores the scale
    # dsp.track_beats was actually validated at, with no change to dsp.track_beats
    # itself or its own default alpha.
    period_frames = frame_rate * 60.0 / bpm
    beat_frames, _score = dsp.track_beats(onset_env / peak, period_frames)
    if len(beat_frames) < _MIN_BEATS_FOR_GRID:
        return None

    if accent_env is not None and accent_env.shape[0] > 0:
        strength = _accent_strength(accent_env, beat_frames, period_frames)
    else:
        strength = _beat_strength(onset_env, beat_frames)
    beats_per_bar, _meter_confidence = _detect_beats_per_bar(strength)
    downbeat_class, downbeat_confidence = _detect_downbeat_class(strength, beats_per_bar)

    class_frames = [f for i, f in enumerate(beat_frames) if i % beats_per_bar == downbeat_class]
    first_downbeat_frame = class_frames[0] if class_frames else beat_frames[0]

    absolute_s = window_start_s + first_downbeat_frame / frame_rate
    bar_period_s = (period_frames / frame_rate) * beats_per_bar
    downbeat_offset = float(absolute_s % bar_period_s) if bar_period_s > 0 else float(absolute_s)

    time_signature = f"{beats_per_bar}/4"
    return downbeat_offset, time_signature, downbeat_confidence


__all__ = ["detect_beat_grid"]
