"""
Key detection: whitened chroma -> learned-profile correlation -> Camelot.

Fully synthetic fixtures (pure tones / simple chords generated with numpy), so these tests
need no real music and carry no copyright question.
"""

from __future__ import annotations

import numpy as np
import pytest

from iron import dsp, key

SR = 44100


def _tone(freq: float, seconds: float = 3.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    return 0.5 * np.sin(2 * np.pi * freq * t)


def _chord(freqs: list[float], seconds: float = 3.0, sr: int = SR) -> np.ndarray:
    # np.stack(...).sum(axis=0) rather than the builtin sum(): builtin sum seeds
    # the accumulator with int 0, so its inferred type is `NDArray | float` and
    # pyright rejects the return as not assignable to np.ndarray. Same arithmetic,
    # honest type.
    return np.stack([_tone(f, seconds, sr) for f in freqs]).sum(axis=0) / len(freqs)


# note -> a representative frequency in the octave dsp.chroma()'s default fmin/fmax band
_NOTE_FREQ = {
    "C": 261.63, "C#": 277.18, "D": 293.66, "D#": 311.13, "E": 329.63, "F": 349.23,
    "F#": 369.99, "G": 392.00, "G#": 415.30, "A": 440.00, "A#": 466.16, "B": 493.88,
}


@pytest.mark.parametrize("note", list(_NOTE_FREQ))
def test_chroma_identifies_pitch_class(note):
    vec = dsp.chroma(_tone(_NOTE_FREQ[note]), SR)
    assert key.NOTES[int(np.argmax(vec))] == note


def test_detect_key_major_chord_roots_on_tonic():
    # C major triad: C4, E4, G4 -- should correlate best with a C-rooted profile (major or
    # minor; a pure triad without the full diatonic context can go either way on mode, but
    # the root must be C).
    y = _chord([_NOTE_FREQ["C"], _NOTE_FREQ["E"], _NOTE_FREQ["G"]])
    result = key.detect_key(y, SR)
    assert result is not None
    camelot, confidence = result
    assert camelot in ("8B", "5A")  # Cmaj -> 8B, Cmin -> 5A; root note is what's asserted
    assert confidence > 0


def test_detect_key_a_minor_triad():
    y = _chord([_NOTE_FREQ["A"], _NOTE_FREQ["C"], _NOTE_FREQ["E"]])  # A minor: A-C-E
    result = key.detect_key(y, SR)
    assert result is not None
    camelot, _confidence = result
    assert camelot in ("8A", "11B")  # Amin -> 8A, Amaj -> 11B; root note A either way


def test_detect_key_silence_returns_none():
    y = np.zeros(SR * 2)
    assert key.detect_key(y, SR) is None


def test_detect_key_confidence_is_bounded():
    y = _tone(_NOTE_FREQ["G"])
    result = key.detect_key(y, SR)
    assert result is not None
    _camelot, confidence = result
    assert -1.0 <= confidence <= 1.0


def test_detect_key_survives_detuned_recording():
    # The same A minor triad pitched 30 cents sharp (a vinyl rip running fast): the tuning
    # step must pull it back onto A, not split it between A and A#.
    sharp = 2 ** (30 / 1200)
    y = _chord([_NOTE_FREQ[n] * sharp for n in ("A", "C", "E")])
    result = key.detect_key(y, SR)
    assert result is not None
    assert result[0] in ("8A", "11B")


def test_whitening_keeps_loud_bass_from_outvoting_the_harmony():
    # A G major triad over a much louder low C drone: without whitening, the drone's energy
    # alone would decide the chroma peak; whitened, the triad's three pitch classes still count.
    triad = _chord([_NOTE_FREQ[n] for n in ("G", "B", "D")])
    drone = 8 * _tone(65.41)  # C2
    vec = key._chroma(triad + drone, SR)
    top3 = {key.NOTES[i] for i in np.argsort(vec)[-4:]}
    assert {"G", "B", "D"} <= top3


_ALL_KEYS = [(n, mode) for n in _NOTE_FREQ for mode in ("maj", "min")]


@pytest.mark.parametrize(("note", "mode"), _ALL_KEYS)
def test_detect_key_recovers_every_major_and_minor_triad(note, mode):
    # A triad (two octaves of each tone) in each of the 24 keys must come back as exactly that
    # key. This pins the tonic rotation and the mode of the model: a one-semitone indexing slip,
    # or a major/minor weight swap, fails 12-24 of these at once.
    intervals = (0, 4, 7) if mode == "maj" else (0, 3, 7)
    freqs = [_NOTE_FREQ[note] * 2 ** (i / 12) * octave for i in intervals for octave in (0.5, 1.0)]
    result = key.detect_key(_chord(freqs), SR)
    assert result is not None
    assert result[0] == key.CAMELOT[note + mode]


def test_detect_key_confidence_is_a_probability_and_clearer_audio_scores_higher():
    triad = _chord([_NOTE_FREQ[n] * o for n in ("A", "C", "E") for o in (0.5, 1.0)])
    rng = np.random.default_rng(0)
    noisy = triad + 1.2 * rng.standard_normal(triad.size)
    clear = key.detect_key(triad, SR)
    muddy = key.detect_key(noisy, SR)
    assert clear is not None and muddy is not None
    assert 0.0 < muddy[1] < clear[1] <= 1.0


def test_view_chromas_are_one_zero_mean_unit_norm_row_per_view():
    views = key._view_chromas(_chord([_NOTE_FREQ[n] for n in ("D", "F", "A")]), SR)
    assert views.shape == (1 + len(key._VIEW_ENVELOPES), 12)
    assert np.allclose(views.mean(axis=1), 0.0, atol=1e-9)
    assert np.allclose(np.linalg.norm(views, axis=1), 1.0)


def test_model_weights_have_one_row_per_view():
    assert key._MODEL_W.shape == (2, 1 + len(key._VIEW_ENVELOPES), 12)
    assert key._MODEL_B.shape == (2,)


def test_net_weights_decode_to_the_shapes_the_scorer_expects():
    dim = 12 * (1 + len(key._VIEW_ENVELOPES))
    assert key._net_weights.DIM == dim and len(key._NET_SEEDS) >= 1
    for W, A, c, v, b in key._NET_SEEDS:
        assert W.shape == (2, dim) and A.shape == (key._net_weights.HIDDEN, dim)
        assert c.shape == (key._net_weights.HIDDEN,) and v.shape == (2, key._net_weights.HIDDEN) and b.shape == (2,)
        assert all(np.isfinite(x).all() for x in (W, A, c, v, b))


@pytest.mark.parametrize(("note", "mode"), _ALL_KEYS)
def test_net_logits_are_tonic_equivariant(note, mode):
    # Rolling every view by k semitones must roll the net's logits by k tonics: the twelve tonics
    # share weights. A view-order or roll-direction slip in _net_logits breaks this for most keys.
    intervals = (0, 4, 7) if mode == "maj" else (0, 3, 7)
    freqs = [_NOTE_FREQ[note] * 2 ** (i / 12) for i in intervals]
    views = key._view_chromas(_chord(freqs), SR)
    base = key._net_logits(views)
    for k in (1, 5, 7):
        assert np.allclose(key._net_logits(np.roll(views, k, axis=1)), np.roll(base, k, axis=0), atol=1e-9)


def test_detect_key_linear_baseline_still_finds_every_triad_root():
    # The SS27/SS28 linear model is kept for A/B benchmarks; it must stay callable and sensible.
    y = _chord([_NOTE_FREQ[n] * o for n in ("A", "C", "E") for o in (0.5, 1.0)])
    result = key.detect_key_linear(y, SR)
    assert result is not None and result[0] == "8A"
    assert key.detect_key_linear(np.zeros(SR * 2), SR) is None


def test_detect_key_profile_baseline_still_finds_the_root():
    # The SS18 detector is kept for A/B benchmarks; it must stay callable and sensible.
    y = _chord([_NOTE_FREQ[n] for n in ("A", "C", "E")])
    result = key.detect_key_profile(y, SR)
    assert result is not None
    assert result[0] in ("8A", "11B")
    assert key.detect_key_profile(np.zeros(SR * 2), SR) is None
