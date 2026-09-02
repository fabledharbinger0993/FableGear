"""
Key detection: chroma -> Krumhansl-Schmuckler correlation -> Camelot.

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
