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
