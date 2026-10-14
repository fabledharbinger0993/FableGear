"""
Quench's own surface: measure, plan, apply, level. Forge (audio_processor) is tested
elsewhere through its wrappers; these tests pin the package contract directly, the way
tests/test_iron_api_window.py and tests/test_anvil_*.py do for the other two tools.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import quench

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _tone(tmp_path: Path, name: str, *, volume_db: float = -20.0, seconds: int = 3) -> Path:
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=44100",
         "-af", f"volume={volume_db}dB", "-ac", "2", str(out)],
        check=True, capture_output=True,
    )
    return out


# ── planning: pure arithmetic ───────────────────────────────────────────────

def test_capped_gain_boost_is_limited_to_headroom():
    assert quench.capped_gain_db(lufs=-17.0, true_peak=-3.1, target=-8.0, ceiling=-1.0) == pytest.approx(2.1)


def test_capped_gain_never_inverts_a_boost_into_a_cut():
    assert quench.capped_gain_db(lufs=-3.0, true_peak=-0.05, target=-8.0, ceiling=-1.0) == pytest.approx(-5.0)
    assert quench.capped_gain_db(lufs=-9.0, true_peak=-0.05, target=-8.0, ceiling=-1.0) == 0.0


def test_capped_gain_without_true_peak_is_uncapped():
    assert quench.capped_gain_db(lufs=-14.0, true_peak=None, target=-8.0, ceiling=-1.0) == pytest.approx(6.0)


def test_plan_reports_capping():
    plan = quench.plan_gain(quench.Loudness(-17.0, -3.1), target=-8.0, ceiling=-1.0)
    assert plan.requested_db == pytest.approx(9.0)
    assert plan.applied_db == pytest.approx(2.1)
    assert plan.capped and plan.worth_applying

    flat = quench.plan_gain(quench.Loudness(-9.0, -0.05), target=-8.0, ceiling=-1.0)
    assert flat.applied_db == 0.0 and not flat.worth_applying


# ── codec recipes ───────────────────────────────────────────────────────────

def test_only_rewritable_containers_have_a_recipe(tmp_path):
    assert quench.codec_args(tmp_path / "x.m4a") is None
    assert quench.codec_args(tmp_path / "x.ogg") is None
    assert quench.codec_args(tmp_path / "x.mp3") == ["-codec:a", "libmp3lame", "-b:a", "320k"]
    assert ".m4a" not in quench.NORMALISABLE_SUFFIXES


# ── measure / apply / level: real ffmpeg ────────────────────────────────────

@needs_ffmpeg
def test_measure_reads_lufs_and_true_peak(tmp_path):
    m = quench.measure(_tone(tmp_path, "t.wav"))
    assert -60.0 < m.integrated_lufs < -10.0
    assert m.true_peak_dbtp is not None and m.true_peak_dbtp < 0.0
    assert m.as_tuple() == (m.integrated_lufs, m.true_peak_dbtp)


@needs_ffmpeg
def test_measure_window_is_honoured(tmp_path):
    # Second half is 20 dB louder; a window on it must read louder than the whole file.
    out = tmp_path / "two.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=44100",
         "-af", "volume='if(lt(t,3),0.01,0.5)':eval=frame", "-ac", "2", str(out)],
        check=True, capture_output=True,
    )
    whole = quench.measure(out).integrated_lufs
    loud = quench.measure(out, start=3.0, duration=3.0).integrated_lufs
    assert loud > whole


@needs_ffmpeg
def test_measure_failure_raises_not_returns(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(quench.MeasureFailed):
        quench.measure(bad)


@needs_ffmpeg
def test_level_moves_a_quiet_file_toward_target(tmp_path):
    f = _tone(tmp_path, "quiet.wav", volume_db=-30.0)
    before = quench.measure(f).integrated_lufs
    r = quench.level(f, target=before + 6.0, tolerance=0.5)
    assert r.levelled and not r.skipped and r.error == ""
    assert r.after is not None and r.after.integrated_lufs > before + 4.0
    assert not f.with_suffix(".wav.bak").exists()


@needs_ffmpeg
def test_level_skips_in_tolerance_and_unsupported(tmp_path):
    f = _tone(tmp_path, "ok.wav")
    lufs = quench.measure(f).integrated_lufs
    r = quench.level(f, target=lufs, tolerance=1.0)
    assert r.skipped and r.skip_reason == "in_tolerance" and not r.levelled

    m4a = tmp_path / "x.m4a"
    m4a.write_bytes(b"\x00" * 64)
    r = quench.level(m4a, target=-8.0, tolerance=0.5)
    assert r.skipped and r.skip_reason == "unsupported_format" and r.before is None


@needs_ffmpeg
def test_apply_gain_refuses_unsupported_container(tmp_path):
    m4a = tmp_path / "x.m4a"
    m4a.write_bytes(b"\x00" * 64)
    with pytest.raises(quench.UnsupportedFormat):
        quench.apply_gain(m4a, 3.0)


@needs_ffmpeg
def test_apply_gain_failure_leaves_original_intact(tmp_path, monkeypatch):
    f = _tone(tmp_path, "keep.wav")
    original = f.read_bytes()
    monkeypatch.setattr(quench.api, "_FFMPEG", "/definitely/not/ffmpeg")
    with pytest.raises(quench.RewriteFailed):
        quench.apply_gain(f, 3.0)
    assert f.read_bytes() == original
    assert not f.with_suffix(".wav.bak").exists()
