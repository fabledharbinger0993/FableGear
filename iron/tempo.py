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
  Applied on top of the harmonic-sum score to break what ties remain.
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

def _in_genre_band(bpm: float) -> bool:
    return any(lo <= bpm <= hi for lo, hi in _GENRE_BANDS)


# How many multiples/submultiples of the raw winner to consider for octave correction, and
# how much of the raw winner's score a candidate must retain to be considered a real rival
# rather than noise. 0.5 is deliberately permissive: a click train with even, unaccented
# beats (or a hi-hat/kick pair of similar onset magnitude) can leave near-tied strength at
# T and 2T -- see iron/tempo.py's module docstring -- and that near-tie is exactly the case
# genre banding exists to break.
_OCTAVE_RATIOS = (2.0, 3.0, 4.0, 0.5, 1.0 / 3.0, 0.25)
_RIVAL_THRESHOLD = 0.5


_HARMONICS = (1, 2, 3, 4)
_HARMONIC_WEIGHT = (1.0, 0.6, 0.4, 0.3)


def _harmonic_score(acf: np.ndarray, lag: int) -> float:
    """
    Sum positive autocorrelation at `lag` and its first few integer multiples, weighted
    down for higher multiples. Reinforces true fundamentals (whose multiples are real
    periodicities of the same pulse train) over subharmonic aliases (which aren't
    multiples of the true, faster period and so don't collect this same credit).
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

    # Pass 2: octave correction. If a multiple or submultiple of the raw winner both (a)
    # lands inside a genre-typical band the raw winner itself misses, and (b) still carries
    # a meaningful fraction of the raw winner's score -- a real rival, not noise -- prefer
    # it. This is where genre-band knowledge earns its keep: only to resolve a signal that's
    # already ambiguous, never to override a candidate that's clearly winning on its own.
    best_bpm = frame_rate * 60.0 / best_lag
    chosen_lag = best_lag
    if not _in_genre_band(best_bpm):
        for ratio in _OCTAVE_RATIOS:
            ideal = best_lag * ratio
            # Check both integer neighbours of the ideal fractional lag, not a single
            # rounded value -- rounding to the nearer neighbour alone is one coin-flip
            # away from landing on lag N when N-1 (or N+1) is the one that's actually
            # in-band and well-supported (a real case: a rounded lag of 20 misses an
            # in-band, well-scored neighbour at 19 purely because 19.5 rounds to 20).
            neighbours = sorted({int(np.floor(ideal)), int(np.ceil(ideal))},
                                 key=lambda lag: abs(lag - ideal))
            found = False
            for candidate_lag in neighbours:
                if candidate_lag < lag_lo or candidate_lag > lag_hi or candidate_lag == best_lag:
                    continue
                candidate_bpm = frame_rate * 60.0 / candidate_lag
                if not _in_genre_band(candidate_bpm):
                    continue
                candidate_score = _harmonic_score(acf, candidate_lag)
                if candidate_score >= _RIVAL_THRESHOLD * best_score:
                    chosen_lag = candidate_lag
                    best_bpm = candidate_bpm
                    found = True
                    break
            if found:
                break

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
