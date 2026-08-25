"""
iron.dryrun: the survey runs clean against real files and never modifies anything.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest
import soundfile as sf

from iron import dryrun


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping audio-fixture test")


def _write_wav(path, bpm: float = 128.0, seconds: float = 6.0, sr: int = 44100) -> None:
    t = np.arange(int(sr * seconds)) / sr
    period = 60.0 / bpm
    y = np.zeros_like(t)
    click = np.hanning(400)
    for beat_time in np.arange(0, seconds, period):
        idx = int(beat_time * sr)
        end = min(idx + len(click), len(y))
        y[idx:end] += click[: end - idx]
    tone = 0.15 * np.sin(2 * np.pi * 220 * t)
    sf.write(str(path), (y + tone).astype(np.float32), sr)


def test_survey_reports_files_and_never_writes(tmp_path):
    _require_ffmpeg()
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_wav(a, bpm=128.0)
    _write_wav(b, bpm=90.0)

    mtimes_before = {p: p.stat().st_mtime_ns for p in (a, b)}

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 2
    assert {f.ext for f in result.files} == {".wav"}
    assert all(f.status == "ok" for f in result.files)
    assert all(f.bpm is not None for f in result.files)

    for p, before in mtimes_before.items():
        assert p.stat().st_mtime_ns == before, f"{p} was modified by a read-only survey"


def test_survey_skips_non_audio_and_apple_double(tmp_path):
    _write_wav(tmp_path / "track.wav")
    (tmp_path / "notes.txt").write_text("not audio")
    (tmp_path / "._track.wav").write_bytes(b"\x00" * 16)  # AppleDouble sidecar

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 1
    assert result.files[0].path.endswith("track.wav")


def test_survey_respects_limit(tmp_path):
    for i in range(4):
        _write_wav(tmp_path / f"t{i}.wav", bpm=100 + i)

    result = dryrun.survey(tmp_path, limit=2, progress=False)

    assert result.scanned == 2


def test_survey_reports_unreadable_file_without_raising(tmp_path):
    bad = tmp_path / "corrupt.mp3"
    bad.write_bytes(b"not actually audio data")

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 1
    assert result.files[0].status == "error"
    assert result.files[0].detail
