"""Tests for ffprobe lookup and the unknown-duration analysis window."""
from pathlib import Path

import numpy as np

from iron import api


def test_find_ffprobe_not_string_replaced(monkeypatch, tmp_path):
    d = tmp_path / "ffmpeg" / "bin"
    d.mkdir(parents=True)
    (d / "ffprobe").write_text("")
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    monkeypatch.setattr(api, "_FFMPEG", str(d / "ffmpeg"))
    assert api._find_ffprobe() == str(d / "ffprobe")


def test_unknown_duration_skips_intro(monkeypatch):
    sr = 10
    seen = {}
    monkeypatch.setattr(api, "_probe_duration", lambda p: None)
    samples = np.arange(240 * sr, dtype=np.float64)
    monkeypatch.setattr(api, "_decode", lambda p, d=90.0, start=0.0: (samples, sr))

    def fake_tempo(y, sr_, **kw):
        seen["n"] = len(y)
        seen["first"] = y[0]
        return None

    monkeypatch.setattr(api.tempo_detect, "detect_tempo", fake_tempo)
    api.analyze(Path("x.wav"), want=("bpm",))
    # body window of a 240 s track is 1/3..0.9 = 136 s, not the 90 s intro
    assert abs(seen["n"] / sr - 136.0) < 1.0
    assert seen["first"] == 80 * sr
