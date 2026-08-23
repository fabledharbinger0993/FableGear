"""
iron.dsp: the numpy-only primitives everything else in Iron is built on.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

SR = 44100


def test_hann_window_is_zero_at_edges_and_peaks_at_center():
    w = dsp.hann_window(256)
    assert w[0] == 0.0
    assert np.isclose(w.max(), 1.0, atol=1e-2)
    assert w.argmax() in (127, 128)


def test_frame_signal_shape_and_overlap():
    y = np.arange(1000, dtype=np.float64)
    frames = dsp.frame_signal(y, frame_length=100, hop_length=50)
    assert frames.shape == (19, 100)
    # consecutive frames overlap by hop_length
    assert np.array_equal(frames[0, 50:], frames[1, :50])


def test_frame_signal_too_short_returns_empty():
    y = np.zeros(10)
    frames = dsp.frame_signal(y, frame_length=100, hop_length=50)
    assert frames.shape == (0, 100)


def test_stft_shape():
    y = np.zeros(SR * 2)
    spec = dsp.stft(y, n_fft=2048, hop_length=512)
    assert spec.shape[1] == 2048 // 2 + 1
    assert spec.shape[0] > 0


def test_onset_envelope_silence_is_flat_zero():
    y = np.zeros(SR * 2)
    env = dsp.onset_envelope(y, SR)
    assert np.all(env == 0.0)


def test_onset_envelope_spikes_at_a_transient():
    y = np.zeros(SR * 2)
    # a sudden loud burst partway through
    burst_start = SR // 2
    y[burst_start:burst_start + 2000] = 0.8 * np.sin(2 * np.pi * 1000 * np.arange(2000) / SR)
    env = dsp.onset_envelope(y, SR, hop_length=512)
    assert env.max() > 0
    peak_frame = int(np.argmax(env))
    expected_frame = burst_start / 512
    assert abs(peak_frame - expected_frame) < 3  # within a few frames of the real onset


def test_autocorrelate_zero_lag_is_max_and_positive():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(500)
    acf = dsp.autocorrelate(x)
    assert acf[0] == np.max(np.abs(acf))
    assert acf[0] > 0


def test_autocorrelate_recovers_known_periodicity():
    period = 40
    x = np.tile([1.0] + [0.0] * (period - 1), 20)
    acf = dsp.autocorrelate(x - x.mean())
    # the strongest non-zero lag should be at (or very near) the true period
    peak_lag = 1 + int(np.argmax(acf[1:200]))
    assert abs(peak_lag - period) <= 1


def test_chroma_pure_tone_peaks_at_its_pitch_class():
    # A4 = 440 Hz -> pitch class 9 ("A" in key.NOTES ordering)
    t = np.arange(SR * 2) / SR
    y = 0.5 * np.sin(2 * np.pi * 440 * t)
    vec = dsp.chroma(y, SR)
    assert vec.shape == (12,)
    assert int(np.argmax(vec)) == 9


def test_chroma_silence_is_zero_vector():
    y = np.zeros(SR * 2)
    vec = dsp.chroma(y, SR)
    assert np.all(vec == 0.0)


def test_band_energy_isolates_a_tone_inside_its_band():
    t = np.arange(SR) / SR
    low = 0.8 * np.sin(2 * np.pi * 60 * t)  # inside a 40-120Hz band
    high = 0.8 * np.sin(2 * np.pi * 5000 * t)  # well outside it
    low_energy = dsp.band_energy(low, SR, fmin=40, fmax=120)
    high_energy = dsp.band_energy(high, SR, fmin=40, fmax=120)
    assert low_energy.sum() > 0
    assert low_energy.sum() > high_energy.sum() * 100


def test_band_energy_silence_is_zero():
    y = np.zeros(SR * 2)
    energy = dsp.band_energy(y, SR, fmin=40, fmax=120)
    assert np.all(energy == 0.0)


def test_band_energy_scales_with_amplitude_squared():
    # energy (magnitude^2), not amplitude -- doubling amplitude should ~quadruple energy
    t = np.arange(SR) / SR
    quiet = 0.2 * np.sin(2 * np.pi * 60 * t)
    loud = 0.4 * np.sin(2 * np.pi * 60 * t)
    e_quiet = dsp.band_energy(quiet, SR, fmin=40, fmax=120).sum()
    e_loud = dsp.band_energy(loud, SR, fmin=40, fmax=120).sum()
    assert 3.5 < (e_loud / e_quiet) < 4.5


def _pulse_train(period: int, n_pulses: int, jitter: int = 0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = period * n_pulses + period
    x = np.zeros(n)
    for i in range(n_pulses):
        idx = i * period + (rng.integers(-jitter, jitter + 1) if jitter else 0)
        if 0 <= idx < n:
            x[idx] = 1.0
    return x


def test_track_beats_locks_onto_a_known_period():
    """dsp.track_beats is validated as a phase-locking primitive for an ALREADY-KNOWN
    period -- not for comparing across candidate periods, which iron/tempo.py's docstring
    documents as a real, unsolved bias (see track_beats' own docstring)."""
    period = 20
    env = _pulse_train(period, n_pulses=30, jitter=1, seed=3)
    beats, score = dsp.track_beats(env, period)
    assert len(beats) >= 25  # most of the 30 real pulses should be picked up
    intervals = np.diff(beats)
    # the tracked sequence should be locked near the true period, not drifting
    assert np.median(intervals) == period
    assert score > 0


def test_track_beats_empty_envelope_returns_nothing():
    beats, score = dsp.track_beats(np.zeros(0), period=20.0)
    assert beats == []
    assert score == 0.0


def test_track_beats_single_frame():
    beats, score = dsp.track_beats(np.array([5.0]), period=20.0)
    assert beats == [0]
    assert score == 5.0
