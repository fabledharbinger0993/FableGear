"""
fablegear / iron / key.py

Musical key detection: whitened log-frequency spectrum -> tuning-corrected,
harmonic-weighted 12-bin chroma -> correlation against key profiles learned from real
music -> Camelot notation.

All original code over `iron.dsp` (numpy only). The approach -- spectral whitening, a
36-bin/octave pitch representation with tuning correction, harmonic summation, and
corpus-derived (rather than classical-listener) key profiles -- follows the published method
family for electronic dance music key estimation (Faraldo et al., ECIR 2016, read only at
abstract level); no code or profile values were taken from that work or its repos (edmkey
has no licence; its Essentia fork is AGPL). See docs/IRON_RESEARCH.md SS18 for how each
step was chosen and measured.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

NOTES: tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Key profiles learned from real music: the mean whitened chroma (normalised per track,
# rotated so the tonic is index 0) of 798 tracks from the owner's library -- 141 major, 657
# minor -- labelled by Rekordbox 7's own analysis (Analysed != 0 only; file-tag keys were
# excluded), disjoint from both benchmark sets. Scaled so the tonic = 1 (Pearson correlation
# is scale-invariant). Fitted to agree with Rekordbox, the reference DJs compare against.
# Rebuild: ~/FableGearTestbed/harness/key_features.py + key_eval.py.
PROFILE_MAJOR = np.array([1.0, 0.36, 0.6583, 0.3593, 0.7503, 0.6322, 0.382, 0.8513, 0.3293, 0.6317, 0.4265, 0.5258])
PROFILE_MINOR = np.array([1.0, 0.4245, 0.5372, 0.6482, 0.4245, 0.681, 0.3837, 0.7844, 0.4695, 0.4023, 0.6219, 0.4296])

# Chroma settings, chosen by 5-fold cross-validation on the training tracks only.
_BINS_PER_OCTAVE = 36
_SPECTRUM_FMIN = 27.5   # A0: semitone centres land on every third log bin
_BAND_FMIN = 55.0
_BAND_FMAX = 3500.0     # above this, hats/air add energy but little pitch information
_HARMONICS = 4          # a partial at f also votes for f/2, f/3, f/4 ...
_HARMONIC_DECAY = 0.6   # ... with weight 0.6 ** (h - 1)

# Camelot wheel notation, keyed by "<Note>maj"/"<Note>min".
CAMELOT: dict[str, str] = {
    "Amin": "8A", "Emin": "9A", "Bmin": "10A", "F#min": "11A", "C#min": "12A",
    "G#min": "1A", "D#min": "2A", "A#min": "3A", "Fmin": "4A", "Cmin": "5A",
    "Gmin": "6A", "Dmin": "7A",
    "Cmaj": "8B", "Gmaj": "9B", "Dmaj": "10B", "Amaj": "11B", "Emaj": "12B",
    "Bmaj": "1B", "F#maj": "2B", "C#maj": "3B", "G#maj": "4B", "D#maj": "5B",
    "A#maj": "6B", "Fmaj": "7B",
}


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denom == 0.0:
        return 0.0
    return float(np.sum(a * b) / denom)


def _chroma(y: np.ndarray, sr: int) -> np.ndarray:
    """12-bin chroma (index 0 = C) from the whitened log spectrum: band-limit, harmonic
    summation, fold to 36 bins, correct tuning by up to +-1/3 semitone, then sum each
    semitone's three bins."""
    spec = dsp.whitened_log_spectrum(y, sr, fmin=_SPECTRUM_FMIN, bins_per_octave=_BINS_PER_OCTAVE)
    freqs = _SPECTRUM_FMIN * 2.0 ** (np.arange(spec.size) / _BINS_PER_OCTAVE)
    spec = spec * ((freqs >= _BAND_FMIN) & (freqs <= _BAND_FMAX))

    summed = spec.copy()
    for h in range(2, _HARMONICS + 1):
        shift = round(_BINS_PER_OCTAVE * np.log2(h))
        summed[:-shift] += _HARMONIC_DECAY ** (h - 1) * spec[shift:]

    # Log bin 0 is A (pitch class 9) -> 36-bin chroma index 27; C's centre is index 0.
    c36 = np.zeros(_BINS_PER_OCTAVE)
    np.add.at(c36, (np.arange(summed.size) + 27) % _BINS_PER_OCTAVE, summed)

    # Tuning: the sub-semitone phase (on-centre, 1/3 sharp, 1/3 flat) holding the most
    # energy is rotated onto the semitone centres -- vinyl rips and older records are often
    # off A440 by a fraction of a semitone.
    phase = int(np.argmax([c36[j::3].sum() for j in range(3)]))
    c36 = np.roll(c36, -(phase if phase < 2 else -1))

    # Each semitone = its centre bin plus the bins either side.
    return np.roll(c36, 1).reshape(12, 3).sum(axis=1)


def detect_key(y: np.ndarray, sr: int) -> tuple[str, float] | None:
    """
    Return (camelot_key, confidence) for a decoded clip, or None if it carries no usable
    tonal energy (silence, pure noise).

    `confidence` is the winning profile's Pearson correlation against the chroma vector
    (-1..1 in principle, effectively 0..1 for real audio). A short or quiet clip can
    correlate strongly by coincidence, so a caller enforcing a quality bar should weight
    this alongside clip length/energy, not trust it alone.
    """
    vec = _chroma(y, sr)
    if not np.any(vec):
        return None

    scores: dict[str, float] = {}
    for i, note in enumerate(NOTES):
        rolled = np.roll(vec, -i)
        scores[note + "maj"] = _pearson(rolled, PROFILE_MAJOR)
        scores[note + "min"] = _pearson(rolled, PROFILE_MINOR)

    best = max(scores, key=lambda note: scores[note])
    camelot = CAMELOT.get(best)
    if camelot is None:
        return None
    return camelot, round(scores[best], 4)
