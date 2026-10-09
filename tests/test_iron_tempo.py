"""
Tempo detection: multiple onset functions -> windowed autocorrelation -> harmonic-sum tempo
curve -> tempo-prior octave resolution.

Fixtures are synthetic kick+hi-hat patterns with light timing humanization and a bar-level
accent (every 4th beat louder) -- not a bare metronome click. A perfectly regular,
zero-variation pulse train is mathematically ambiguous between its true period and every one
of its own integer divisors (autocorrelation has no way to prefer one over the other when
every beat is identical); real recorded music is never that regular, and this fixture is
built to have the same kind of asymmetric information a real track does, deliberately, so
these tests exercise the same disambiguation problem the algorithm is actually built for.
"""

from __future__ import annotations

import numpy as np
import pytest

from iron import tempo

SR = 22050  # iron.api decodes at this rate; test directly against it to skip ffmpeg


def _beat_track(bpm: float, seconds: float = 15.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    y = np.zeros(n)
    period = 60.0 / bpm

    kick_len = int(SR * 0.15)
    kt = np.arange(kick_len) / SR
    kick = np.sin(2 * np.pi * 80 * kt) * np.exp(-kt * 25)

    hat_len = int(SR * 0.05)
    ht = np.arange(hat_len) / SR
    hat = rng.standard_normal(hat_len) * np.exp(-ht * 60) * 0.4

    for i, beat_time in enumerate(np.arange(0, seconds, period)):
        accent = 1.3 if i % 4 == 0 else 1.0
        jitter = rng.normal(0, period * 0.01)
        idx = int((beat_time + jitter) * SR)
        if 0 <= idx < n:
            end = min(idx + kick_len, n)
            y[idx:end] += kick[: end - idx] * accent

        off_jitter = rng.normal(0, period * 0.01)
        off_idx = int((beat_time + period / 2 + off_jitter) * SR)
        if 0 <= off_idx < n:
            oend = min(off_idx + hat_len, n)
            y[off_idx:oend] += hat[: oend - off_idx]

    return y


# The DJ tempo range the detector's prior favours. Real-music accuracy is measured by
# scripts/benchmark_iron_tempo.py; these only guard against the algorithm being plainly broken.
@pytest.mark.parametrize("bpm", [90, 100, 118, 124, 128, 133, 140, 150, 165, 174])
def test_detect_tempo_within_tolerance(bpm):
    y = _beat_track(bpm)
    result = tempo.detect_tempo(y, SR)
    assert result is not None
    detected, confidence = result
    # 2%: loose enough to absorb the frame-quantization + humanization in the fixture
    # itself, tight enough that an octave error (50%+ off) still fails loudly.
    assert abs(detected - bpm) / bpm < 0.02, f"true={bpm} detected={detected}"
    assert 0.0 <= confidence <= 1.0


# At the far ends of the range the kick+off-beat-hat fixture is genuinely octave-ambiguous
# (at 70 BPM it IS a valid 140 BPM pattern), and the tempo prior deliberately resolves such
# ties toward the DJ range. Requiring the true tempo or its half/double -- not the exact
# octave -- is the honest assertion here; the 3:2 and other non-octave errors that used to
# fail on real music are what the real-library benchmark tracks.
@pytest.mark.parametrize("bpm", [70, 190, 210])
def test_detect_tempo_extremes_resolve_to_an_octave_of_truth(bpm):
    y = _beat_track(bpm)
    result = tempo.detect_tempo(y, SR)
    assert result is not None
    detected, _confidence = result
    assert any(abs(detected - bpm * k) / (bpm * k) < 0.02 for k in (0.5, 1.0, 2.0)), (
        f"true={bpm} detected={detected}"
    )


def test_detect_tempo_silence_returns_none():
    y = np.zeros(SR * 10)
    assert tempo.detect_tempo(y, SR) is None


def test_detect_tempo_too_short_returns_none():
    y = _beat_track(128, seconds=0.3)
    assert tempo.detect_tempo(y, SR) is None


def test_detect_tempo_respects_bpm_bounds():
    # A real 128 BPM track, searched only in a range that excludes 128 -- forces the
    # detector to either report nothing usable or a value inside the requested bounds,
    # never a value it was told is out of range.
    y = _beat_track(128)
    result = tempo.detect_tempo(y, SR, bpm_min=140.0, bpm_max=300.0)
    if result is not None:
        detected, _confidence = result
        assert 140.0 <= detected <= 300.0
