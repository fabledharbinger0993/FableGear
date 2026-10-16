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


def _record_decodes(monkeypatch, duration, *, fail_key=False):
    """Stub probing/decoding/detectors; return the list of (start, seconds) decode calls."""
    calls = []
    sr = 10

    def fake_decode(p, d=90.0, start=0.0):
        calls.append((start, d))
        if fail_key and start == 0.0 and d != api._BEAT_GRID_SECONDS:
            raise api.DecodeFailed("key decode failed")
        return np.ones(int(d * sr)), sr

    monkeypatch.setattr(api, "_probe_duration", lambda p: duration)
    monkeypatch.setattr(api, "_decode", fake_decode)
    monkeypatch.setattr(api.tempo_detect, "detect_tempo", lambda y, sr_, **kw: (120.0, 0.9))
    monkeypatch.setattr(api.key_detect, "detect_key", lambda y, sr_: ("8A", 0.5))
    return calls


def test_key_only_call_decodes_the_whole_track_once_and_skips_the_body(monkeypatch):
    calls = _record_decodes(monkeypatch, 300.0)
    result = api.analyze(Path("x.wav"), want=("initial_key",))
    assert calls == [(0.0, 300.0)]
    assert result.initial_key == "8A"


def test_key_decode_is_capped_at_ten_minutes(monkeypatch):
    calls = _record_decodes(monkeypatch, 3600.0)
    api.analyze(Path("x.wav"), want=("initial_key",))
    assert calls == [(0.0, api._KEY_MAX_SECONDS)]


def test_key_uses_its_own_decode_while_tempo_keeps_the_body_window(monkeypatch):
    calls = _record_decodes(monkeypatch, 300.0)
    result = api.analyze(Path("x.wav"), want=("bpm", "initial_key"))
    start, span = api._pick_body_window(300.0)
    assert calls == [(start, span), (0.0, 300.0)]
    assert result.bpm == 120.0
    assert result.initial_key == "8A"


def test_a_failed_key_decode_is_an_error_not_a_lost_tempo(monkeypatch):
    _record_decodes(monkeypatch, 300.0, fail_key=True)
    result = api.analyze(Path("x.wav"), want=("bpm", "initial_key"))
    assert result.bpm == 120.0
    assert result.initial_key is None
    assert any("key decode failed" in e for e in result.errors)
