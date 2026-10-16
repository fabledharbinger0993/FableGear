"""
iron.analyze(..., verify_stability=True): long-baseline confirmation.

A single 90-second window can't tell "one confident tempo" apart from "this file has more
than one tempo section" (a DJ mix, a live recording, a track with a real tempo change).
verify_stability projects the found BPM forward to where beats 32/64/128/256/512 (or a
caller-supplied set) should fall, decodes a short independent window at each reachable one,
and re-derives tempo from scratch there -- confirmed against real DJ-mix files during
development (a 1-hour mix agreed at every checkpoint through beat 256, then correctly
flagged disagreement at beat 512, where the mix had moved to a different track/tempo).

These fixtures write real WAV files (soundfile, not synthetic in-memory arrays) because the
feature under test decodes via ffmpeg with seek -- it needs an actual file on disk, not
pre-decoded PCM. Small custom anchor sets keep fixtures short enough to run fast.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import iron

SR = 22050


def _require_ffmpeg():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping iron.analyze fixture test")


def _click_track(bpm: float, seconds: float, sr: int = SR, seed: int = 0) -> np.ndarray:
    """Kick+hat pattern, same shape as test_iron_tempo.py's fixture generator."""
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    y = np.zeros(n)
    period = 60.0 / bpm

    kick_len = int(sr * 0.15)
    kt = np.arange(kick_len) / sr
    kick = np.sin(2 * np.pi * 80 * kt) * np.exp(-kt * 25)

    for i, beat_time in enumerate(np.arange(0, seconds, period)):
        accent = 1.3 if i % 4 == 0 else 1.0
        jitter = rng.normal(0, period * 0.01)
        idx = int((beat_time + jitter) * sr)
        if 0 <= idx < n:
            end = min(idx + kick_len, n)
            y[idx:end] += kick[: end - idx] * accent

    return y


def _write_wav(path: Path, y: np.ndarray, sr: int = SR) -> None:
    sf.write(str(path), y.astype(np.float32), sr)


def test_stability_confirms_constant_tempo(tmp_path):
    _require_ffmpeg()
    y = _click_track(128.0, seconds=40.0)
    path = tmp_path / "constant.wav"
    _write_wav(path, y)

    result = iron.analyze(path, verify_stability=True, stability_anchors=(8, 16, 32))

    assert result.bpm is not None and abs(result.bpm - 128.0) < 3.0
    assert result.bpm_stable is True
    assert len(result.checkpoints) == 3
    assert all(cp.agrees for cp in result.checkpoints)
    assert all(cp.measured_bpm is not None for cp in result.checkpoints)


def test_stability_detects_a_real_tempo_change(tmp_path):
    _require_ffmpeg()
    # analyze() targets the track's body (1/3 through to 90%, see iron/api.py), so this
    # fixture's proportions matter, not just its total length: section A must be long
    # enough that the body window lands entirely inside it (90% of 90s = 81s, well inside
    # the 0-80s A section below), with section B only reachable via a projected
    # long-baseline anchor, the same way a real mid-track tempo change would be.
    first = _click_track(128.0, seconds=80.0, seed=1)
    second = _click_track(90.0, seconds=10.0, seed=2)
    y = np.concatenate([first, second])
    path = tmp_path / "tempo_change.wav"
    _write_wav(path, y)

    # beat 8/16 (~3.75s/7.5s from wherever the body window starts) stay inside the 128 bpm
    # section; beat 175 (~82s at 128bpm) projects into the 90bpm section starting at 80s.
    result = iron.analyze(
        path, verify_stability=True, stability_anchors=(8, 16, 175), stability_window=5.0
    )

    assert result.bpm is not None and abs(result.bpm - 128.0) < 3.0
    assert result.bpm_stable is False
    by_beat = {cp.beat: cp for cp in result.checkpoints}
    assert by_beat[8].agrees is True
    assert by_beat[16].agrees is True
    assert by_beat[175].agrees is False
    assert by_beat[175].measured_bpm is not None
    assert abs(by_beat[175].measured_bpm - 90.0) < 3.0


def test_stability_skips_anchors_beyond_duration(tmp_path):
    _require_ffmpeg()
    y = _click_track(128.0, seconds=10.0)
    path = tmp_path / "short.wav"
    _write_wav(path, y)

    # beat 8 (~3.75s) is reachable in a 10s clip with a short confirmation window; beat
    # 512 (~240s) is nowhere close regardless of window size.
    result = iron.analyze(
        path, verify_stability=True, stability_anchors=(8, 512), stability_window=3.0
    )

    by_beat = {cp.beat: cp for cp in result.checkpoints}
    assert by_beat[8].measured_bpm is not None
    assert by_beat[8].agrees is True
    assert by_beat[512].measured_bpm is None
    assert by_beat[512].agrees is None
    # Verdict reflects only the reachable checkpoint, not the skipped one.
    assert result.bpm_stable is True


def test_verify_stability_false_by_default(tmp_path):
    _require_ffmpeg()
    y = _click_track(128.0, seconds=10.0)
    path = tmp_path / "default.wav"
    _write_wav(path, y)

    result = iron.analyze(path)

    assert result.bpm_stable is None
    assert result.checkpoints == []
