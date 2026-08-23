"""
fablegear / iron / tempo.py

Tempo (BPM) detection: onset envelope -> autocorrelation -> DJ-genre-aware tempo pick.

Standard MIR technique -- spectral-flux onset detection feeding an autocorrelation
periodicity analysis, the approach underlying published beat trackers going back to Scheirer
1998 and refined through e.g. Ellis 2007 ("Beat Tracking by Dynamic Programming"). This is an
independent implementation of a published, non-proprietary method, not a port of any
dependency's internals.

Octave disambiguation is the hard part. Picking the single strongest autocorrelation peak
with no other information is what produces the ~13% exact-BPM accuracy documented for the
librosa fallback path in requirements_optional.txt -- a plain peak can't reliably tell a
tempo from its double or half, and worse: a short, sharp click can leave a *stronger* raw
autocorrelation peak at 2x its true period than at the true period itself, an artifact of
onset energy landing unevenly across the analysis-frame grid. Two corrections handle it:

  Harmonic-sum scoring. A true fundamental period T has strong autocorrelation not just at
  T but reliably at 2T, 3T, ... A spurious detection at 2T doesn't get that same
  reinforcement from T itself (T is shorter than 2T, not a multiple of it), so scoring each
  candidate by its own lag plus a few of its multiples -- not the raw lag value alone --
  systematically favours the true, faster fundamental over its subharmonic aliases.

  Genre-band bonus. A working DJ library clusters tightly by genre (house ~118-130,
  techno ~125-145, ...) -- real prior information a generic MIR tool doesn't get to assume.
  Applied on top of the harmonic-sum score to break what ties remain. Bands carry a small
  pad at their edges (_BAND_PAD_BPM) so a true tempo whose autocorrelation energy straddles
  two adjacent integer lags right at a boundary -- a real quantization effect, not a design
  choice -- doesn't get excluded on a technicality. The correction itself scans every lag in
  range for the best in-band rival rather than only checking clean multiples/submultiples of
  the raw winner, because on real (non-percussion-uniform) music the true tempo is not
  always a tidy ratio of the wrong one.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

# Typical genre tempo centers a working DJ library clusters around. Not exhaustive and not
# genre-labelled -- just density priors for octave disambiguation.
_GENRE_BANDS: tuple[tuple[float, float], ...] = (
    (85.0, 100.0),    # hip-hop / trap
    (95.0, 115.0),    # downtempo / halftime
    (118.0, 130.0),   # house
    (125.0, 145.0),   # techno / trance
    (140.0, 155.0),   # dubstep / half-time DnB
    (160.0, 180.0),   # drum & bass / jungle
    (180.0, 220.0),   # hardcore / gabber
)


# Real music quantizes its true period across a discrete lag grid, and the
# true tempo's harmonic energy can straddle two adjacent integer lags right at
# a band's edge -- found on a real 117.45 BPM house track whose autocorrelation
# peak split across lag 21 (123.05 BPM, in-band but under-scoring) and lag 22
# (117.45 BPM, well-scored but 0.55 BPM outside the band as originally drawn).
# A fixed pad absorbs that without having to hand-tune every band boundary.
_BAND_PAD_BPM = 5.0


def _in_genre_band(bpm: float) -> bool:
    return any(lo - _BAND_PAD_BPM <= bpm <= hi + _BAND_PAD_BPM for lo, hi in _GENRE_BANDS)


# How much of the raw winner's score a rival candidate must retain to be considered real
# rather than noise. 0.5 is deliberately permissive: a click train with even, unaccented
# beats (or a hi-hat/kick pair of similar onset magnitude) can leave near-tied strength at
# T and 2T -- see iron/tempo.py's module docstring -- and that near-tie is exactly the case
# genre banding exists to break.
_RIVAL_THRESHOLD = 0.5

# Conventional breakdown/bridge lengths, in bars (4/4 assumed). Not exhaustive -- arrangement
# convention, same informal-prior spirit as _GENRE_BANDS, not a hard rule.
_CONVENTIONAL_BAR_COUNTS = (8, 12, 16, 24, 32)

# How close (in bars) a breakdown's implied length must land to a conventional count to be
# treated as a genuinely tight structural fit -- see the Pass 4 comment in detect_tempo for
# why this must be a strict, one-sided gate rather than a plain "closer of two" comparison.
_BAR_FIT_TIGHT_BARS = 0.5


def _bar_fit_distance(duration_s: float, bpm: float) -> float:
    """Distance, in bars, from `duration_s` at `bpm` to the nearest conventional bar count."""
    bars = duration_s * bpm / 60.0 / 4.0
    nearest = min(_CONVENTIONAL_BAR_COUNTS, key=lambda r: abs(bars - r))
    return abs(bars - nearest)


_HARMONICS = (1, 2, 3, 4)
_HARMONIC_WEIGHT = (1.0, 0.6, 0.4, 0.3)


def _harmonic_score(acf: np.ndarray, lag: int) -> float:
    """
    Sum positive autocorrelation at `lag` and its first few integer multiples, weighted
    down for higher multiples. Reinforces true fundamentals (whose multiples are real
    periodicities of the same pulse train) over subharmonic aliases (which aren't
    multiples of the true, faster period and so don't collect this same credit).

    A compound-meter (1.5x / 3-against-2) credit term was tried here and reverted: on a
    real 75-track library test it failed to flip the one case it targeted (both the true
    fundamental and its wrong, higher-scoring rival share enough overlapping harmonic
    content that a modest asymmetric weight boosted both roughly proportionally), and at a
    strong enough weight to matter, it broke an existing synthetic regression test
    (test_detect_tempo_within_tolerance[165], a clean 2x error). This is flagged as a real,
    unsolved gap -- see docs/ANVIL_IRON_STATUS.md -- not something to patch again without a
    fundamentally different approach (e.g. a genuinely independent second onset-detection
    feature, closer to what essentia's multifeature ensemble does, rather than a second term
    on the same single autocorrelation signal).
    """
    total = 0.0
    for k, weight in zip(_HARMONICS, _HARMONIC_WEIGHT, strict=True):
        idx = lag * k
        if idx >= acf.shape[0]:
            break
        value = acf[idx]
        if value > 0:
            total += weight * value
    return total


def detect_tempo(
    y: np.ndarray,
    sr: int,
    *,
    bpm_min: float = 30.0,
    bpm_max: float = 300.0,
    hop_length: int = 512,
) -> tuple[float, float] | None:
    """
    Return (bpm, confidence) for a decoded clip, or None if no reliable periodicity is
    found (silence, or a clip too short to establish one).

    `confidence` is the winning lag's autocorrelation strength relative to the strongest
    lag anywhere in the signal (0..1) -- not on the same scale as essentia's beat-tracker
    confidence, but usable the same way: a low value means "eyeball this grid before a gig."
    """
    env = dsp.onset_envelope(y, sr, hop_length=hop_length)
    if env.shape[0] < 8 or not np.any(env):
        return None

    acf = dsp.autocorrelate(env - env.mean())
    frame_rate = sr / hop_length

    lag_lo = max(1, int(frame_rate * 60.0 / bpm_max))
    lag_hi = min(acf.shape[0] - 1, int(frame_rate * 60.0 / bpm_min))
    if lag_hi <= lag_lo:
        return None

    peak_strength = float(np.max(np.abs(acf[1:]))) if acf.shape[0] > 1 else 0.0
    if peak_strength <= 0:
        return None

    # Pass 1: find the strongest candidate by harmonic-sum score alone, no genre bias.
    # This is the honest "what does the signal say" answer before any DJ-domain prior gets
    # applied to it.
    best_lag: int | None = None
    best_score = -np.inf
    for lag in range(lag_lo, lag_hi + 1):
        strength = acf[lag]
        if strength <= 0:
            continue
        score = _harmonic_score(acf, lag)
        if score > best_score:
            best_score = score
            best_lag = lag

    if best_lag is None:
        return None

    # Pass 2: octave correction. If some OTHER lag in the valid range both (a) lands inside
    # a genre-typical band the raw winner itself misses, and (b) still carries a meaningful
    # fraction of the raw winner's score -- a real rival, not noise -- prefer whichever such
    # candidate scores highest. This is where genre-band knowledge earns its keep: only to
    # resolve a signal that's already ambiguous, never to override a candidate that's
    # clearly winning on its own.
    #
    # Scanning every lag directly, rather than only checking clean integer/simple-fraction
    # multiples of the raw winner, is deliberate: real music's competing periodicity is not
    # always a tidy ratio of the wrong one. Found on a real track where the raw winner and
    # the true tempo were related by a ratio of ~2.52 -- not 2, 3, 4, 0.5, 1/3, or 1/4, so a
    # ratio-restricted search would never have found the true candidate at all, no matter how
    # generous its score.
    best_bpm = frame_rate * 60.0 / best_lag
    chosen_lag = best_lag
    if not _in_genre_band(best_bpm):
        rival_lag: int | None = None
        rival_score = -np.inf
        for lag in range(lag_lo, lag_hi + 1):
            if lag == best_lag:
                continue
            bpm = frame_rate * 60.0 / lag
            if not _in_genre_band(bpm):
                continue
            score = _harmonic_score(acf, lag)
            if score >= _RIVAL_THRESHOLD * best_score and score > rival_score:
                rival_score = score
                rival_lag = lag
        if rival_lag is not None:
            chosen_lag = rival_lag
            best_bpm = frame_rate * 60.0 / rival_lag

    # A third pass using dsp.track_beats() (DP beat tracking, Ellis 2007) to disambiguate
    # against compound-meter rivals was tried here and reverted -- see dsp.track_beats's
    # docstring for what it does and does not currently prove. Comparing its score across
    # candidate periods of different lengths carries a systematic bias toward faster
    # candidates (more beats in a path gives the optimizer more chances at favorable
    # alignment, independent of real periodicity) that neither raw nor baseline-subtracted
    # normalization removed, confirmed by breaking several already-correct synthetic cases
    # (70, 90, 100, 174, 210 BPM) in a full sweep -- not a margin-tuning problem, a real
    # unsolved comparison-fairness problem. Flagged for whoever picks this up next: a fair
    # comparison likely needs to control for path length directly (e.g. resampling to a
    # fixed beat count) rather than normalizing the raw DP score by frames or by beats.

    # Pass 4: breakdown-duration structural fit. A DJ mix or club edit's bridge/breakdown is
    # almost always a "round" length in bars (8/16/32, arrangement convention, not a signal
    # property) -- so the duration of that section, divided by each candidate BPM's beat
    # length, should land close to a round bar count for the TRUE tempo and not necessarily
    # for a compound-meter alias. This needs the caller to have decoded enough of the track
    # to contain a real mid-song breakdown, not just the first 90 seconds -- see iron/api.py.
    #
    # exclude_frac is small here, NOT find_breakdown_duration's own 0.15 default: the caller
    # (iron/api.py) already decodes only the track's body, having excluded the real intro
    # and outro at the file level. Applying another 15% exclusion on top of that double-trims
    # an already-trimmed window -- confirmed on a real track where it clipped the true
    # breakdown from 31.4s down to 24.7s, throwing away exactly the portion that made the
    # bar-count fit tight. A small edge margin still guards against pure window-boundary
    # artifacts without re-excluding real structure.
    #
    # Deliberately conservative otherwise: only overrides the current pick when the fit is
    # GENUINELY tight for exactly one candidate. A naive "whichever is closer" comparison was
    # tested and rejected -- on an already-correct track, the wrong half-tempo candidate had
    # a *closer* (but still loose, ~0.8 bars off) fit than the correct one (~1.6 bars off),
    # which would have flipped a working answer. Requiring a tight fit on one side and a
    # loose fit on the other keeps the two ambiguous-but-not-clean real cases this was tested
    # against silent (correctly -- neither had a fit worth trusting) while still catching the
    # one real case where the true tempo's breakdown length landed within a third of a bar of
    # 16 bars exactly, against the wrong candidate's 1.5+ bar miss.
    breakdown_s = dsp.find_breakdown_duration(env, frame_rate, exclude_frac=0.03)
    if breakdown_s is not None:
        chosen_bpm = frame_rate * 60.0 / chosen_lag
        chosen_dist = _bar_fit_distance(breakdown_s, chosen_bpm)
        best_rival_lag: int | None = None
        best_rival_dist = np.inf
        for ratio in (2.0, 0.5, 1.5, 2.0 / 3.0):
            lag = round(chosen_lag * ratio)
            if lag < lag_lo or lag > lag_hi or lag == chosen_lag:
                continue
            rival_bpm = frame_rate * 60.0 / lag
            dist = _bar_fit_distance(breakdown_s, rival_bpm)
            if dist < best_rival_dist:
                best_rival_dist = dist
                best_rival_lag = lag
        if (
            best_rival_lag is not None
            and best_rival_dist < _BAR_FIT_TIGHT_BARS
            and chosen_dist >= _BAR_FIT_TIGHT_BARS
        ):
            chosen_lag = best_rival_lag
            best_bpm = frame_rate * 60.0 / best_rival_lag

    strength = float(acf[chosen_lag])
    refined_lag = _parabolic_peak(acf, chosen_lag)
    if refined_lag > 0:
        best_bpm = frame_rate * 60.0 / refined_lag

    confidence = float(np.clip(strength / peak_strength, 0.0, 1.0))
    return round(best_bpm, 2), round(confidence, 2)


def _parabolic_peak(acf: np.ndarray, lag: int) -> float:
    """
    Sub-frame refinement of an integer-lag peak by fitting a parabola through it and its
    two neighbours. Autocorrelation only has one value per frame (here, ~23ms at 22050 Hz /
    512 hop), so the raw integer-lag estimate alone carries a quantization error large
    enough to matter -- at 128 BPM it's the difference between 128.0 and 129.2, a gap a DJ
    tool can't shrug off. Standard technique for periodicity/pitch estimation refinement.
    """
    if lag <= 0 or lag >= acf.shape[0] - 1:
        return float(lag)
    left, center, right = acf[lag - 1], acf[lag], acf[lag + 1]
    denom = left - 2.0 * center + right
    if denom == 0:
        return float(lag)
    shift = 0.5 * (left - right) / denom
    # A well-formed peak shifts by less than half a frame; a larger shift means the
    # neighbours aren't really describing this peak (e.g. it's at the edge of the search
    # window) and the unrefined integer lag is more trustworthy than the fit.
    if abs(shift) >= 1.0:
        return float(lag)
    return lag + shift
