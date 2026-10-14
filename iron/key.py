"""
fablegear / iron / key.py

Musical key detection: several spectrally whitened log-frequency views of the clip ->
tuning-corrected, harmonic-weighted 12-bin chroma per view -> a small tonic-equivariant
linear model (one weight vector per mode, shared by all twelve tonics) -> Camelot notation.

All original code over `iron.dsp` (numpy only). The approach -- spectral whitening, a
36-bin/octave pitch representation with tuning correction, harmonic summation, and key
templates learned from a music corpus (rather than classical-listener profiles) -- follows the
published method family for electronic dance music key estimation (Faraldo et al., ECIR 2016,
read only at abstract level); no code or profile values were taken from that work or its repos
(edmkey has no licence; its Essentia fork is AGPL). The model weights are numbers fitted by
this repo's own code on the owner's library (docs/IRON_RESEARCH.md SS27, SS28); no third-party model
or weight file is used.

`detect_key` is the model of SS27, refitted on whole-track chroma in SS28 (the caller, iron.api, passes
it the whole track rather than the tempo body window). `detect_key_profile` is the SS18 detector it replaced
(Pearson correlation against two learned profiles on one whitened view), kept as the baseline
for A/B benchmarks. See SS18 and SS27 for how each step was chosen and measured.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

NOTES: tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# SS18 baseline detector (`detect_key_profile`): key profiles learned from real music: the mean whitened chroma (normalised per track,
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

# Added to every minor key's correlation when choosing the winner (not to the reported
# confidence). DJ libraries lean minor -- 82% of the training tracks -- and the training-CV
# gain plateaus from +0.08 to +0.15; +0.08 is the smallest value on that plateau and still
# calls fewer tracks minor (77%) than the library holds. Cost: true-major tracks right
# 54% -> 42%. Owner's call, 2026-10-10 (docs/IRON_RESEARCH.md SS18.5).
_MINOR_BIAS = 0.08

# Model of docs/IRON_RESEARCH.md SS27/SS28: score(tonic t, mode m) = sum over views g of
# _MODEL_W[m, g] . roll(chroma_g, -t) + _MODEL_B[m] (+ _MINOR_SHIFT for minor), each view's
# chroma first made zero-mean and unit-norm; confidence = softmax probability of the winner.
# Fitted by L2-regularised softmax regression (lam = 0.03) on 5,297 tracks of the owner's
# library labelled by Rekordbox 7's own analysis (Analysed != 0 only), using chroma from the
# WHOLE track (SS28), disjoint from every evaluation set. lam and _MINOR_SHIFT were chosen by
# 5-fold cross-validation on those tracks only. Rebuild: scripts/experiments/key_lab_extract.py
# (--window whole) + key_lab_logit.py.
_VIEW_ENVELOPES = (1.0, 0.5, 2.0)  # octaves of the whitening envelope; one more view is the raw magnitude
_MODEL_W = np.array([
    [  # major
        # whitened, 1-octave envelope
        [ 0.7861,  0.5688,  0.5165, -0.4029,  0.0086, -0.5419, -0.8008,  1.1537, -0.0310, -1.0209,  0.0261, -0.2624],
        # whitened, 0.5-octave envelope
        [ 2.4342, -0.1070, -1.6696, -0.8741,  2.0129,  0.3348,  0.1989, -0.1364, -0.7826, -0.6615, -0.5009, -0.2486],
        # whitened, 2-octave envelope
        [ 1.3483, -0.4446,  0.3259, -1.2415,  1.1059,  0.0929, -0.0720,  0.6917, -0.3298, -0.4995, -0.4934, -0.4839],
        # raw magnitude
        [ 0.8720, -1.2743,  0.7749, -0.8859,  2.0042, -0.3392, -0.4744,  0.9150, -1.4990,  0.8263, -0.5924, -0.3272],
    ],
    [  # minor
        # whitened, 1-octave envelope
        [ 1.3427, -0.5460, -0.7879,  0.3690, -1.0366, -0.7735, -0.4649, -0.6645,  0.3043,  0.6820,  0.4468,  1.1285],
        # whitened, 0.5-octave envelope
        [ 1.4562,  0.0805, -0.7731,  0.0736,  0.3079, -1.1019,  0.6309,  1.7914, -0.7319, -1.0240, -0.2004, -0.5091],
        # whitened, 2-octave envelope
        [ 0.4919,  0.6007,  0.9052,  1.1585,  0.1178,  0.5784, -0.6026,  0.0755, -0.5625, -0.6782, -1.1084, -0.9764],
        # raw magnitude
        [ 1.2847, -1.3054, -0.2147,  0.6637, -0.5789,  0.4825, -0.2590,  0.7324, -0.4017, -0.3595,  0.5338, -0.5781],
    ],
])
_MODEL_B = np.array([-1.6412, 1.6412])  # major, minor
_MINOR_SHIFT = 0.00  # added to the minor logits; CV picked 0 once the model was fitted on whole tracks

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


def _fold(spec: np.ndarray) -> np.ndarray:
    """12-bin chroma (index 0 = C) from one whitened log spectrum: band-limit, harmonic
    summation, fold to 36 bins, correct tuning by up to +-1/3 semitone, then sum each
    semitone's three bins."""
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


def _chroma(y: np.ndarray, sr: int) -> np.ndarray:
    """12-bin chroma of the one-octave-envelope whitened view (the SS18 detector's input)."""
    return _fold(dsp.whitened_log_spectrum(y, sr, fmin=_SPECTRUM_FMIN, bins_per_octave=_BINS_PER_OCTAVE))


def _view_chromas(y: np.ndarray, sr: int) -> np.ndarray:
    """(views, 12) zero-mean, unit-norm chroma, one row per model view (see _MODEL_W)."""
    spectra = dsp.whitened_log_spectra(
        y, sr, envelope_octaves=_VIEW_ENVELOPES, include_raw=True,
        fmin=_SPECTRUM_FMIN, bins_per_octave=_BINS_PER_OCTAVE,
    )
    chroma = np.stack([_fold(spec) for spec in spectra])
    centred = chroma - chroma.mean(axis=1, keepdims=True)
    return centred / (np.linalg.norm(centred, axis=1, keepdims=True) + 1e-12)


def detect_key(y: np.ndarray, sr: int) -> tuple[str, float] | None:
    """
    Return (camelot_key, confidence) for a decoded clip, or None if it carries no usable
    tonal energy (silence, pure noise).

    `confidence` is the model's softmax probability for the winning key (0..1). On held-out
    Rekordbox-analysed tracks the winner is right about 22% of the time below 0.3, about 67%
    at 0.5-0.7 and about 86% at 0.7-0.9 (SS28), so it is a usable "is this key trustworthy" signal;
    a short or quiet clip can still score high by coincidence, so a caller enforcing a quality
    bar should weight this alongside clip length/energy, not trust it alone.
    """
    views = _view_chromas(y, sr)
    if not np.any(views[0]):
        return None

    logits = np.empty((12, 2))
    for tonic in range(12):
        rolled = np.roll(views, -tonic, axis=1)
        logits[tonic] = np.einsum("mgk,gk->m", _MODEL_W, rolled) + _MODEL_B
    logits[:, 1] += _MINOR_SHIFT
    prob = np.exp(logits - logits.max())
    prob /= prob.sum()

    tonic, mode = np.unravel_index(int(np.argmax(prob)), prob.shape)
    camelot = CAMELOT.get(NOTES[tonic] + ("min" if mode else "maj"))
    if camelot is None:
        return None
    return camelot, round(float(prob[tonic, mode]), 4)


def detect_key_profile(y: np.ndarray, sr: int) -> tuple[str, float] | None:
    """
    The SS18 detector: Pearson correlation of one whitened chroma against two learned profiles
    (PROFILE_MAJOR / PROFILE_MINOR), with a small minor-key bias (_MINOR_BIAS) on the choice.
    `confidence` is the winning profile's plain correlation. Superseded by `detect_key` (SS27);
    kept so benchmarks can run the previous detector side by side.
    """
    vec = _chroma(y, sr)
    if not np.any(vec):
        return None

    scores: dict[str, float] = {}
    for i, note in enumerate(NOTES):
        rolled = np.roll(vec, -i)
        scores[note + "maj"] = _pearson(rolled, PROFILE_MAJOR)
        scores[note + "min"] = _pearson(rolled, PROFILE_MINOR)

    best = max(scores, key=lambda note: scores[note] + (_MINOR_BIAS if note.endswith("min") else 0.0))
    camelot = CAMELOT.get(best)
    if camelot is None:
        return None
    return camelot, round(scores[best], 4)
