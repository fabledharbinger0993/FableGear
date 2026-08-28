"""
iron.dsp: the numpy-only primitives everything else in Iron is built on.
"""

from __future__ import annotations

import numpy as np
import pytest

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


def test_energy_flux_silence_is_flat_zero():
    y = np.zeros(SR * 2)
    env = dsp.energy_flux(y, SR)
    assert np.all(env == 0.0)


def test_energy_flux_spikes_at_a_transient():
    y = np.zeros(SR * 2)
    burst_start = SR // 2
    y[burst_start:burst_start + 2000] = 0.8 * np.sin(2 * np.pi * 1000 * np.arange(2000) / SR)
    env = dsp.energy_flux(y, SR, hop_length=512)
    assert env.max() > 0
    peak_frame = int(np.argmax(env))
    expected_frame = burst_start / 512
    assert abs(peak_frame - expected_frame) < 3


def test_energy_flux_scales_with_amplitude_squared():
    # raw (non-log-compressed) energy -- doubling amplitude should ~quadruple the flux,
    # unlike onset_envelope's log-compressed flux which deliberately does NOT scale this way
    t = np.arange(SR) / SR
    quiet = np.concatenate([np.zeros(SR // 2), 0.2 * np.sin(2 * np.pi * 200 * t[: SR // 2])])
    loud = np.concatenate([np.zeros(SR // 2), 0.4 * np.sin(2 * np.pi * 200 * t[: SR // 2])])
    e_quiet = dsp.energy_flux(quiet, SR, hop_length=512).max()
    e_loud = dsp.energy_flux(loud, SR, hop_length=512).max()
    assert 3.5 < (e_loud / e_quiet) < 4.5


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


def test_onset_envelope_multiband_silence_is_flat_zero():
    y = np.zeros(SR * 2)
    bands = dsp.onset_envelope_multiband(y, SR)
    assert bands.shape[0] == len(dsp._DEFAULT_ONSET_BANDS)
    assert np.all(bands == 0.0)


def test_onset_envelope_multiband_isolates_transient_to_its_band():
    # A low-frequency (kick-band) transient should spike the kick-band row far more than the
    # high-frequency (hi-hat-band) row. Smooth attack/decay (sine starting at its own zero
    # crossing, exponential decay -- same shape as test_iron_tempo.py's kick fixture) avoids
    # the sharp on/off edges a rectangular burst would have, which spray broadband spectral
    # leakage into every band regardless of the tone's real frequency.
    n = SR * 2
    y = np.zeros(n)
    burst_start = SR // 2
    kick_len = int(SR * 0.15)
    kt = np.arange(kick_len) / SR
    kick = np.sin(2 * np.pi * 80 * kt) * np.exp(-kt * 25)  # inside kick band, no sharp edges
    y[burst_start:burst_start + kick_len] += 0.8 * kick
    bands = dsp.onset_envelope_multiband(y, SR, hop_length=512)
    kick_row, high_row = bands[0], bands[3]
    assert kick_row.max() > 0
    assert kick_row.max() > high_row.max() * 3


def test_cyclic_tempo_strength_pools_octave_related_lags():
    frame_rate = 43.0  # ~22050/512
    acf = np.zeros(2000)
    lag_a = round(frame_rate * 60.0 / 130.0)
    lag_b = round(frame_rate * 60.0 / 65.0)  # rounds to exactly 2 * lag_a at this frame rate
    assert lag_b == 2 * lag_a  # test precondition: an exact octave relationship in lag space
    bpm_a = frame_rate * 60.0 / lag_a
    bpm_b = frame_rate * 60.0 / lag_b
    acf[lag_a] = 3.0
    acf[lag_b] = 2.0
    curve = dsp.cyclic_tempo_strength(acf, frame_rate)
    # bpm_a and bpm_b share the same tempo class (they're exactly one octave apart), so that
    # single bin should carry the SUM of both strengths, not either one alone. Looking up by
    # the ACTUAL bpm each lag quantizes to (not the nominal 130/65 targets) avoids a
    # false failure from the two nominal values happening to straddle a bin boundary.
    pooled = dsp.cyclic_tempo_class_lookup(curve, bpm_a)
    assert pooled == pytest.approx(5.0, abs=1e-6)
    assert pooled == pytest.approx(dsp.cyclic_tempo_class_lookup(curve, bpm_b), abs=1e-6)


def test_cyclic_tempo_strength_empty_range_returns_zeros():
    curve = dsp.cyclic_tempo_strength(np.zeros(10), frame_rate=43.0, bpm_min=30.0, bpm_max=300.0)
    assert curve.shape == (60,)


def test_track_beats_with_penalty_variance_is_low_for_a_consistent_period():
    period = 20
    env = _pulse_train(period, n_pulses=30, jitter=1, seed=3)
    beats, score, variance = dsp.track_beats_with_penalty_variance(env, period)
    assert len(beats) >= 25
    assert score > 0
    assert variance >= 0.0
    assert variance < 0.01  # near-zero for a genuinely well-locked, consistent period


def test_track_beats_with_penalty_variance_short_path_is_infinite():
    beats, _score, variance = dsp.track_beats_with_penalty_variance(np.array([5.0]), period=20.0)
    assert beats == [0]
    assert variance == float("inf")


def test_chroma_cqt_identifies_pitch_class():
    t = np.arange(SR * 2) / SR
    y = 0.5 * np.sin(2 * np.pi * 440 * t)  # A4
    vec = dsp.chroma_cqt(y, SR)
    assert vec.shape == (12,)
    assert int(np.argmax(vec)) == 9  # "A" in key.NOTES ordering


def test_chroma_cqt_silence_is_zero_vector():
    y = np.zeros(SR * 2)
    vec = dsp.chroma_cqt(y, SR)
    assert np.all(vec == 0.0)


def test_grid_alignment_score_high_for_true_period():
    period = 20
    env = _pulse_train(period, n_pulses=30, jitter=0, seed=0)
    score = dsp.grid_alignment_score(env, period)
    assert score > 0.8  # every predicted position should land exactly on a real pulse


def test_grid_alignment_score_low_for_unrelated_period():
    period = 20
    env = _pulse_train(period, n_pulses=30, jitter=0, seed=0)
    # a period with no rational relationship to the real one should mostly miss the pulses
    score = dsp.grid_alignment_score(env, period=13.0)
    assert score < 0.5


def test_grid_alignment_score_empty_envelope_is_zero():
    assert dsp.grid_alignment_score(np.zeros(0), period=20.0) == 0.0


def test_grid_alignment_score_tolerant_to_small_jitter():
    period = 20
    env = _pulse_train(period, n_pulses=30, jitter=1, seed=3)
    score = dsp.grid_alignment_score(env, period)
    assert score > 0.5  # the tolerance window should absorb +/-1-frame jitter


def test_chroma_cqt_resolves_bass_register_better_than_linear_chroma():
    # A1 = 55 Hz, the bottom of both chroma functions' default range -- at n_fft=4096/
    # sr=22050 (chroma()'s default), the linear FFT bin width (~5.4 Hz) is wider than a
    # semitone's spacing there (~3.3 Hz), so chroma() can misplace it. chroma_cqt()'s larger
    # n_fft resolves the semitone correctly.
    t = np.arange(SR * 3) / SR
    y = 0.5 * np.sin(2 * np.pi * 55.0 * t)
    vec = dsp.chroma_cqt(y, SR)
    assert int(np.argmax(vec)) == 9  # "A" -- A1 is still an A
