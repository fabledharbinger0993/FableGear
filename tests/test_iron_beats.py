"""
Beat-grid (downbeat_offset) and simple meter (time_signature) detection.

Fixtures reuse the same kick+hi-hat, bar-accented pattern tests/test_iron_tempo.py uses --
"every Nth beat louder" is both this module's downbeat assumption and the existing tempo
fixture's own convention, so these tests exercise the real assumption rather than a
convenience built just for this file.
"""

from __future__ import annotations

import numpy as np
import pytest

from iron import beats, dsp

SR = 22050
HOP = 512
FRAME_RATE = SR / HOP


def _beat_track(bpm: float, seconds: float = 30.0, seed: int = 0, beats_per_bar: int = 4) -> np.ndarray:
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
        accent = 1.3 if i % beats_per_bar == 0 else 1.0
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


def _onset_env(y: np.ndarray) -> np.ndarray:
    return dsp.onset_envelope(y, SR, hop_length=HOP)


def _accent_env(y: np.ndarray) -> np.ndarray:
    return dsp.band_energy(y, SR, fmin=40.0, fmax=120.0, hop_length=HOP)


@pytest.mark.parametrize("bpm", [90, 120, 128, 174])
def test_detect_beat_grid_reports_4_4_and_a_grid_locked_downbeat(bpm):
    """
    Validates what this pipeline can actually prove end-to-end: the meter comes out 4/4
    (this fixture's real time signature), and the returned downbeat_offset traces back to
    one of the actually-tracked beat positions -- i.e. the DP phase-lock and the
    fold-into-first-bar arithmetic are both correct, and the result isn't a made-up time.

    Deliberately NOT asserted here: that the class picked is specifically the accented
    one. See test_detect_downbeat_class_picks_the_loudest_beat_position, which validates
    that logic directly on a clean synthetic strength signal -- and
    detect_beat_grid's own docstring for why the full kick+hi-hat audio fixture, once run
    through log-compressed spectral flux, doesn't reliably carry enough of a ~1.3x
    amplitude accent to trust an end-to-end assertion on WHICH beat wins here.
    """
    y = _beat_track(bpm, seconds=30.0)
    env = _onset_env(y)
    result = beats.detect_beat_grid(env, FRAME_RATE, bpm)
    assert result is not None
    downbeat_offset, time_signature, confidence = result
    assert time_signature == "4/4"
    assert 0.0 <= confidence <= 1.0

    beat_period = 60.0 / bpm
    bar_period = beat_period * 4
    assert 0.0 <= downbeat_offset < bar_period

    # every one of the 4 candidate downbeat_offsets (one per beat class, independently
    # reconstructed here) that a correct implementation could have returned -- the actual
    # result must match one of them, not an arithmetic-bug value.
    period_frames = FRAME_RATE * 60.0 / bpm
    tracked_frames, _score = dsp.track_beats(env / env.max(), period_frames)
    candidates = {
        (tracked_frames[c] / FRAME_RATE) % bar_period
        for c in range(4)
        if c < len(tracked_frames)
    }
    assert any(abs(downbeat_offset - c) < 1e-6 for c in candidates)


@pytest.mark.parametrize("bpm", [90, 120, 128, 140, 174])
def test_detect_beat_grid_with_accent_env_finds_the_true_downbeat(bpm):
    """
    With a kick-band accent_env supplied, detect_beat_grid should reliably identify the
    ACTUAL accented beat as the downbeat -- unlike the broadband-only fallback path (see
    test_detect_beat_grid_reports_4_4_and_a_grid_locked_downbeat), this is validated
    end-to-end, not just at the unit level, because accent_env is specifically what makes
    the true accent survive to the scoring step (see iron/beats.py's module docstring).
    """
    y = _beat_track(bpm, seconds=30.0)
    onset_env = _onset_env(y)
    accent_env = _accent_env(y)
    result = beats.detect_beat_grid(onset_env, FRAME_RATE, bpm, accent_env=accent_env)
    assert result is not None
    downbeat_offset, time_signature, confidence = result
    assert time_signature == "4/4"

    beat_period = 60.0 / bpm
    bar_period = beat_period * 4
    # the fixture's true downbeats are at 0, bar_period, 2*bar_period, ... -- the folded
    # offset should land close to that boundary, not just close to SOME beat.
    dist_to_zero = min(downbeat_offset, bar_period - downbeat_offset)
    assert dist_to_zero < beat_period * 0.5
    assert confidence > 0.25  # clearly above the 1/4 "no preference" baseline


def test_detect_beat_grid_folds_offset_into_first_bar():
    bpm = 128
    y = _beat_track(bpm, seconds=30.0)
    env = _onset_env(y)
    result = beats.detect_beat_grid(env, FRAME_RATE, bpm)
    assert result is not None
    downbeat_offset, _ts, _conf = result
    bar_period = 60.0 / bpm * 4
    assert 0.0 <= downbeat_offset < bar_period


def test_detect_beat_grid_window_start_shifts_the_folded_offset():
    bpm = 128
    y = _beat_track(bpm, seconds=30.0)
    env = _onset_env(y)
    at_zero = beats.detect_beat_grid(env, FRAME_RATE, bpm, window_start_s=0.0)
    at_offset = beats.detect_beat_grid(env, FRAME_RATE, bpm, window_start_s=5.0)
    assert at_zero is not None and at_offset is not None

    bar_period = 60.0 / bpm * 4
    expected = (at_zero[0] + 5.0) % bar_period
    diff = min(abs(at_offset[0] - expected), bar_period - abs(at_offset[0] - expected))
    assert diff < (60.0 / bpm) * 0.5


def test_detect_beat_grid_too_few_beats_returns_none():
    y = _beat_track(128, seconds=2.0)  # far fewer beats than _MIN_BEATS_FOR_GRID needs
    env = _onset_env(y)
    assert beats.detect_beat_grid(env, FRAME_RATE, 128.0) is None


def test_detect_beat_grid_silence_returns_none():
    env = np.zeros(500)
    assert beats.detect_beat_grid(env, FRAME_RATE, 128.0) is None


def test_detect_beat_grid_invalid_bpm_returns_none():
    env = _onset_env(_beat_track(128, seconds=10.0))
    assert beats.detect_beat_grid(env, FRAME_RATE, 0.0) is None
    assert beats.detect_beat_grid(env, FRAME_RATE, -10.0) is None


def test_detect_beats_per_bar_prefers_3_when_signal_is_clearly_triple():
    n = 60
    strength = np.full(n, 0.05)
    strength[::3] = 1.0  # a clean, strong period-3 accent
    beats_per_bar, confidence = beats._detect_beats_per_bar(strength)
    assert beats_per_bar == 3
    assert confidence > 0


def test_detect_beats_per_bar_defaults_to_4_with_no_clear_signal():
    rng = np.random.default_rng(1)
    strength = rng.random(60) * 0.01  # noise, no real periodicity
    beats_per_bar, _confidence = beats._detect_beats_per_bar(strength)
    assert beats_per_bar == 4


def test_detect_beats_per_bar_too_few_samples_defaults_to_4():
    beats_per_bar, confidence = beats._detect_beats_per_bar(np.array([1.0, 0.5, 0.2]))
    assert beats_per_bar == 4
    assert confidence == 0.0


def test_detect_downbeat_class_picks_the_loudest_beat_position():
    # 4 beats per bar, position 2 (0-indexed) is consistently the loudest
    strength = np.tile([0.1, 0.1, 1.0, 0.1], 10)
    downbeat_class, confidence = beats._detect_downbeat_class(strength, beats_per_bar=4)
    assert downbeat_class == 2
    assert confidence > 0.25  # clearly above the 1/4 "no preference" baseline


def test_accent_strength_finds_a_peak_the_tracked_frame_itself_misses():
    # a sharp spike sits a few frames BEFORE each "tracked" frame -- direct sampling at
    # the tracked frame would see near-zero; the backward search should find the spike.
    period_frames = 20.0
    env = np.zeros(200)
    tracked = [30, 50, 70, 90]
    for t in tracked:
        env[t - 8] = 1.0  # the true accent, 8 frames before where it's "tracked"

    direct = beats._beat_strength(env, tracked)
    searched = beats._accent_strength(env, tracked, period_frames)
    assert np.all(direct < 0.01)  # direct sampling misses it entirely
    assert np.all(searched > 0.9)  # the backward search finds it


def test_accent_strength_does_not_search_past_half_a_period():
    # a spike far outside the search radius (more than half a period back) must not be
    # picked up -- that would risk grabbing the PREVIOUS beat's own transient instead.
    period_frames = 20.0
    env = np.zeros(200)
    env[50 - 15] = 1.0  # 15 frames back, outside the period/2=10 search radius
    searched = beats._accent_strength(env, [50], period_frames)
    assert searched[0] == 0.0
