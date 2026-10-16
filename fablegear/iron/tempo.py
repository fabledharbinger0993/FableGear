"""
fablegear / iron / tempo.py

Tempo (BPM) detection: several onset functions -> windowed autocorrelation -> harmonic-sum
tempo curve -> tempo-prior octave resolution.

Standard, published MIR technique, independently implemented (no third-party MIR library
underneath): spectral-flux onset detection feeding an autocorrelation periodicity analysis
(Scheirer 1998; Ellis 2007), with a tempo-perception prior to settle octave ambiguity
(Moelants & McKinney 2002; Klapuri et al. 2006), and several independent onset functions
whose evidence is combined (the multi-feature idea of Zapata et al. 2014).

How the pieces earn their place -- each was measured, not assumed, on ~500 real tracks scored
against Rekordbox's own beat-grid BPMs (see docs/ANVIL_IRON_STATUS.md for the numbers):

  Windowed autocorrelation. Averaging the autocorrelation of 8 s windows rewards a
  periodicity present throughout the track instead of letting one loud section (or a drift)
  dominate a single global autocorrelation.

  Several onset functions. A full-band flux curve alone hides the beat on many tracks (a
  hat-driven loop has no bass flux; a kick-driven one little up top). Five views -- full
  band, low, mid, high, high-frequency-content -- are each reduced to a tempo curve and
  summed, so a tempo the views agree on beats one a single view happens to favour.

  Harmonic-sum scoring. A true period T has autocorrelation at 2T, 3T, 4T as well; a
  subharmonic alias doesn't get the same reinforcement from the true, faster period.

  Tempo prior. Harmonic-sum scoring alone is biased toward *slow* candidates (more multiples
  land in range), so without a prior it picks half-tempo on most tracks. A log-normal
  preference around a DJ-library centre breaks that tie and the 3:2 / 2:1 ambiguities. It is
  broad (+/-0.6 octave = one sigma): a clear periodicity still wins from well outside it.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

# Log-normal tempo prior. Centre/width are in BPM / octaves. Chosen on a development split
# and confirmed on a disjoint held-out split of the same library; 120 vs 125 and 0.5 vs 0.7
# octaves moved accuracy by well under a point, whereas no prior at all collapsed it to ~35%.
_PRIOR_CENTRE_BPM = 125.0
_PRIOR_SIGMA_OCTAVES = 0.6

# Weight given to the autocorrelation at 1x, 2x, 3x, 4x the candidate period.
_HARMONIC_WEIGHTS = (1.0, 0.7, 0.5, 0.3)

_BPM_STEP = 0.25
_REFINE_STEP = 0.05
_REFINE_RADIUS_BPM = 1.5

# Shortest clip worth analysing: a handful of beats at slow tempi.
_MIN_SECONDS = 3.0

# A rival peak this close (in octaves) to the winner is the same peak, not a rival.
_RIVAL_EXCLUSION_OCTAVES = 0.06


def _curve(acfs: list[np.ndarray], frame_rate: float, bpms: np.ndarray) -> np.ndarray:
    """Sum, over onset functions, of each one's max-normalised harmonic-sum tempo curve."""
    lags = 60.0 * frame_rate / bpms
    total = np.zeros(bpms.shape[0])
    for acf in acfs:
        score = np.zeros(bpms.shape[0])
        positions = np.arange(acf.shape[0])
        for k, weight in enumerate(_HARMONIC_WEIGHTS, start=1):
            at = lags * k
            valid = at < acf.shape[0] - 1
            score[valid] += weight * np.maximum(np.interp(at[valid], positions, acf), 0.0)
        peak = score.max()
        if peak > 0:
            total += score / peak
    return total


def detect_tempo(
    y: np.ndarray,
    sr: int,
    *,
    bpm_min: float = 30.0,
    bpm_max: float = 300.0,
) -> tuple[float, float] | None:
    """
    Return (bpm, confidence) for a decoded clip, or None if no reliable periodicity is
    found (silence, or a clip too short to establish one).

    `confidence` (0..1) is how far the winning tempo's prior-weighted score stands above the
    best *unrelated* rival tempo -- 0 means a tie with a different tempo, near 1 means no
    credible alternative. It is not on the same scale as essentia's beat-tracker confidence,
    but is usable the same way: a low value means "eyeball this grid before a gig."
    """
    if bpm_max <= bpm_min or y.shape[0] < _MIN_SECONDS * sr:
        return None

    envelopes, frame_rate = dsp.multi_onset_envelopes(y, sr)
    if envelopes[0].shape[0] < 8:
        return None

    acfs = [dsp.windowed_autocorrelation(env, frame_rate) for env in envelopes if np.any(env)]
    if not acfs:
        return None

    bpms = np.arange(bpm_min, bpm_max + 1e-9, _BPM_STEP)
    prior = np.exp(-0.5 * (np.log2(bpms / _PRIOR_CENTRE_BPM) / _PRIOR_SIGMA_OCTAVES) ** 2)
    curve = _curve(acfs, frame_rate, bpms) * prior
    if curve.max() <= 0:
        return None

    best = int(np.argmax(curve))
    best_bpm = float(bpms[best])

    # Fine local refinement on the un-prior'd curve: the prior is smooth, so it only
    # chooses the peak, never nudges where the peak sits.
    fine = np.arange(
        max(bpm_min, best_bpm - _REFINE_RADIUS_BPM),
        min(bpm_max, best_bpm + _REFINE_RADIUS_BPM) + 1e-9,
        _REFINE_STEP,
    )
    refined = float(fine[int(np.argmax(_curve(acfs, frame_rate, fine)))])

    unrelated = np.abs(np.log2(bpms / best_bpm)) > _RIVAL_EXCLUSION_OCTAVES
    rival = float(curve[unrelated].max()) if np.any(unrelated) else 0.0
    confidence = float(np.clip(1.0 - rival / curve[best], 0.0, 1.0))
    return round(refined, 2), round(confidence, 2)
