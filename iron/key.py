"""
fablegear / iron / key.py

Musical key detection: chroma profile -> Krumhansl-Schmuckler correlation -> Camelot
notation.

The correlation step is original code, not derived from any dependency -- the only piece
that used to come from librosa (`chroma_cqt`, extracting the 12-bin pitch-class profile) is
now `iron.dsp.chroma_cqt()` (a log-frequency-binned pseudo-CQT chroma -- see its own
docstring for why it resolves bass-register pitch classes that `iron.dsp.chroma()`'s
linear-Hz FFT bins can smear together, and why it isn't a literal per-bin variable-kernel
CQT). `iron.dsp.chroma()` remains available and unchanged for callers that want the plain
linear-frequency version.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

NOTES: tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Schmuckler key profiles (Krumhansl & Kessler 1982) -- empirically measured
# listener judgments of how well each pitch class fits a major/minor tonic. Published
# psychoacoustic data, not sourced from any dependency's code.
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

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


def detect_key(y: np.ndarray, sr: int) -> tuple[str, float] | None:
    """
    Return (camelot_key, confidence) for a decoded clip, or None if it carries no usable
    tonal energy (silence, pure noise).

    `confidence` is the winning profile's Pearson correlation against the chroma vector
    (-1..1 in principle, effectively 0..1 for real audio). A short or quiet clip can
    correlate strongly by coincidence, so a caller enforcing a quality bar should weight
    this alongside clip length/energy, not trust it alone.
    """
    vec = dsp.chroma_cqt(y, sr)
    if not np.any(vec):
        return None

    scores: dict[str, float] = {}
    for i, note in enumerate(NOTES):
        rolled = np.roll(vec, -i)
        scores[note + "maj"] = _pearson(rolled, KS_MAJOR)
        scores[note + "min"] = _pearson(rolled, KS_MINOR)

    best = max(scores, key=lambda note: scores[note])
    camelot = CAMELOT.get(best)
    if camelot is None:
        return None
    return camelot, round(scores[best], 4)
