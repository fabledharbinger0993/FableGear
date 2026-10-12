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

# Half-beat phase check (see _prefer_kick_phase). The shifted phase must carry this much more
# kick-band energy than the tracked one before the grid is moved -- a tie keeps the tracker's
# own answer rather than flipping on noise.
_PHASE_SWITCH_RATIO = 1.1


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


def _phase_strength(accent_env: np.ndarray, frames: list[int], half_width: int) -> float:
    """Mean of the peak accent value within +-half_width frames of each frame. Symmetric and
    narrow on purpose -- unlike _accent_strength's half-period backward search, it must not
    reach from one half-beat phase into the other."""
    n = accent_env.shape[0]
    peaks = [float(accent_env[max(0, f - half_width):min(n, f + half_width + 1)].max())
             for f in frames if 0 <= f < n]
    return float(np.mean(peaks)) if peaks else 0.0


def _prefer_kick_phase(
    accent_env: np.ndarray, beat_frames: list[int], period_frames: float
) -> list[int]:
    """
    Return `beat_frames`, or the same grid shifted by half a beat if the kick band says the
    beat sits there.

    `dsp.track_beats` phase-locks on broadband spectral flux, which in dance music is often
    dominated by the off-beat hi-hat rather than the kick (a noise burst has far more
    broadband flux than an ~80 Hz sine). On 2026-10-09 the tracker sat on the off-beat for
    100% of beats on the kick+hi-hat fixture at every tempo tested, and for 79 of 149 real
    tracks against Rekordbox beat grids. This is a PHASE choice at an already-decided
    period -- not one of the cross-period comparisons docs/IRON_RESEARCH.md section 5 rules out.
    """
    half = period_frames / 2.0
    shifted = [round(f + half) for f in beat_frames]
    width = max(1, round(period_frames / 8.0))
    on = _phase_strength(accent_env, beat_frames, width)
    off = _phase_strength(accent_env, shifted, width)
    return shifted if off > on * _PHASE_SWITCH_RATIO else list(beat_frames)


def _extend_to_start(beat_frames: list[int], period_frames: float, earliest: float) -> list[int]:
    """Prepend whole beat periods until the grid reaches `earliest` (a frame index, may be
    slightly negative to allow for onset latency). dsp.track_beats does not place the first
    beat or two of a window -- on 2026-10-10 its first beat sat ~0.5 s in on real tracks
    whose first kick (Rekordbox's beat 1) was at ~0.06 s."""
    frames = list(beat_frames)
    while frames and frames[0] - period_frames >= earliest:
        frames.insert(0, max(0, round(frames[0] - period_frames)))
    return frames


def _music_start(strength: np.ndarray) -> int:
    """Index of the first beat with a real kick: accent at least half the median beat accent."""
    median = float(np.median(strength)) if strength.size else 0.0
    loud = np.nonzero(strength >= 0.5 * median)[0] if median > 0 else np.array([], dtype=int)
    return int(loud[0]) if loud.size else 0


def _first_kick_class(strength: np.ndarray, beats_per_bar: int) -> tuple[int, float]:
    """
    Downbeat class for a grid that starts at the TOP of the track: the first beat with a
    real kick (accent at least half the track's median beat accent) is beat 1. Club tracks
    start on a 1 -- or bring the kick in on a phrase boundary after a 16/32-beat intro, which
    is still a 1.

    Measured 2026-10-10 against Rekordbox beat grids (rb200, first 60 s, grid counted back
    to 0:00): 54% of downbeat_offsets within 25 ms and 75.5% within 50 ms, vs 13% / -- for
    the loudest-class rule on a mid-track window. Loudest-class and 4/8/16-beat phrase-
    contrast scoring were both worse on the same windows (35% / 37%): in four-on-the-floor
    music every kick is equally loud, and phrase contrast near the intro is weak.
    """
    chosen = _music_start(strength) % beats_per_bar
    class_means = np.array([strength[c::beats_per_bar].mean() for c in range(beats_per_bar)])
    total = float(class_means.sum())
    return chosen, (float(class_means[chosen] / total) if total > 0 else 0.0)


def _detect_beats_per_bar(strength: np.ndarray) -> tuple[int, float]:
    """
    Return (beats_per_bar, confidence). See this module's docstring for why the only two
    candidates are 3 and 4, and why 4 is the default.
    """
    n = strength.shape[0]
    if n < _MIN_BEATS_FOR_GRID:
        return 4, 0.0

    acf = dsp.autocorrelate(strength - strength.mean())
    if acf.shape[0] <= 5 or acf[0] <= 0:
        return 4, 0.0

    r = acf / float(acf[0])
    # Score each bar length by how far its lag STANDS OUT from the neighbouring lags, not
    # by its raw value: a slow drift in accent strength (a build, a filter sweep) lifts every
    # small lag together, and raw lag-3 vs lag-4 then picks 3/4 on plain 4/4 house -- 43 of
    # 200 real tracks on 2026-10-10 (median r3 0.75, r3 - r4 only 0.11). A real triple
    # meter peaks at lag 3 with dips at 2 and 4; drift cancels out of the contrast.
    peak_3 = float(r[3] - (r[2] + r[4]) / 2.0)
    peak_4 = float(r[4] - (r[3] + r[5]) / 2.0)

    if peak_3 > peak_4 and peak_3 > _METER_3_MIN_SCORE:
        return 3, float(np.clip(peak_3, 0.0, 1.0))
    return 4, float(np.clip(max(peak_4, 0.0), 0.0, 1.0))


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
    onset_latency_s: float = 0.0,
    window_is_track_start: bool = False,
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

    `onset_latency_s` is how far `onset_env`'s frame timestamps run ahead of the onsets that
    produced them -- pass `iron.dsp.onset_latency_seconds(sr)` for an envelope from
    `dsp.onset_envelope`/`dsp.energy_flux` (~70 ms at Iron's defaults; without it every beat
    lands that much early). The default 0.0 is for envelopes whose frame index IS the onset
    time, e.g. a synthetic pulse train.

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
        beat_frames = _prefer_kick_phase(accent_env, list(beat_frames), period_frames)
    if window_is_track_start:
        beat_frames = _extend_to_start(list(beat_frames), period_frames, -onset_latency_s * frame_rate)
    if accent_env is not None and accent_env.shape[0] > 0:
        strength = _accent_strength(accent_env, beat_frames, period_frames)
    else:
        strength = _beat_strength(onset_env, beat_frames)
    # Meter is judged from where the music starts: a kickless intro in front of the kicks
    # is a step in `strength`, and a step's autocorrelation falls off with lag, so lag 3
    # outscores lag 4 -- an intro alone made a 4/4 track read as 3/4 at 0.82 confidence.
    beats_per_bar, _meter_confidence = _detect_beats_per_bar(strength[_music_start(strength):])
    if window_is_track_start:
        downbeat_class, downbeat_confidence = _first_kick_class(strength, beats_per_bar)
    else:
        downbeat_class, downbeat_confidence = _detect_downbeat_class(strength, beats_per_bar)

    class_frames = [f for i, f in enumerate(beat_frames) if i % beats_per_bar == downbeat_class]
    first_downbeat_frame = class_frames[0] if class_frames else beat_frames[0]

    absolute_s = window_start_s + first_downbeat_frame / frame_rate + onset_latency_s
    bar_period_s = (period_frames / frame_rate) * beats_per_bar
    downbeat_offset = float(absolute_s % bar_period_s) if bar_period_s > 0 else float(absolute_s)

    time_signature = f"{beats_per_bar}/4"
    return downbeat_offset, time_signature, downbeat_confidence


__all__ = ["detect_beat_grid"]
