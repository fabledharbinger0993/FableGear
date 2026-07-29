"""
Tests for the loudness-normalisation gain cap in audio_processor.

The defect this guards: gain was previously sized purely from integrated
loudness (`TARGET_LUFS - lufs`), with no regard for how much true-peak
headroom the file actually had. A quiet-but-hot master — low integrated
loudness, true peak already near 0 dBFS, a very normal shape for percussive
or dynamic music — got the full boost anyway and clipped hard, with the
original file deleted once the write verified as non-empty.

_capped_gain_db is the pure fix: gain is capped so the output's true peak can
never cross TRUE_PEAK_CEILING_DBTP. These tests pin the cap's arithmetic
directly, then prove it end-to-end against a real ffmpeg-processed file whose
measured shape (low LUFS, hot peak) is exactly the failure case.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audio_processor as ap
import config


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping audio-fixture test")


def _quiet_but_hot_mp3(tmp_path: Path) -> Path:
    """A track shaped like the failure case: low integrated loudness (mostly
    quiet) but a true peak near 0 dBFS (brief full-scale bursts) — the crest
    factor of ordinary percussive/dynamic music, not a pathological edge case.
    Measured ~-17 LUFS / ~-3.1 dBTP; naive gain to -8 LUFS would want +9 dB,
    which would drive the true peak to roughly +5.9 dBFS.
    """
    _require_ffmpeg()
    p = tmp_path / "quiet_but_hot.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "aevalsrc='if(lt(mod(t,0.5),0.015),"
                "0.99*sin(2*PI*1000*t),0.05*sin(2*PI*1000*t))':s=44100:d=10",
         "-codec:a", "libmp3lame", "-b:a", "320k", str(p)],
        check=True, capture_output=True,
    )
    return p


# ── _capped_gain_db: pure arithmetic ────────────────────────────────────────

def test_boost_within_headroom_is_uncapped():
    # Plenty of room under the ceiling — gain passes through unchanged.
    gain = ap._capped_gain_db(lufs=-9.0, true_peak=-6.0, target=-8.0, ceiling=-1.0)
    assert gain == pytest.approx(1.0)


def test_boost_past_ceiling_is_capped_to_exactly_the_ceiling():
    # This is the failure case: naive gain would be +9 dB and drive true peak
    # from -3.1 to +5.9 dBFS. The cap must land the output exactly on the
    # ceiling, not merely "less than before".
    gain = ap._capped_gain_db(lufs=-17.0, true_peak=-3.1, target=-8.0, ceiling=-1.0)
    assert gain == pytest.approx(2.1)          # -1.0 - (-3.1)
    assert gain < (-8.0 - -17.0)                # strictly less than the naive +9 dB


def test_attenuation_is_never_capped():
    # A wanted gain that's already negative can't push the peak up further,
    # so the true-peak cap must not touch it even if the peak is already hot.
    gain = ap._capped_gain_db(lufs=-3.0, true_peak=-0.05, target=-8.0, ceiling=-1.0)
    assert gain == pytest.approx(-5.0)


def test_unknown_true_peak_falls_back_to_uncapped():
    # Measurement failure shouldn't block normalisation outright — it should
    # behave exactly as the old code did (uncapped) rather than refuse to run.
    gain = ap._capped_gain_db(lufs=-14.0, true_peak=None, target=-8.0, ceiling=-1.0)
    assert gain == pytest.approx(6.0)


def test_boost_landing_exactly_on_ceiling_is_not_further_reduced():
    gain = ap._capped_gain_db(lufs=-9.0, true_peak=-2.0, target=-8.0, ceiling=-1.0)
    assert gain == pytest.approx(1.0)           # want == headroom exactly


# ── end-to-end: the real bug, on real audio ─────────────────────────────────

def test_normalise_never_drives_true_peak_past_ceiling(tmp_path):
    """Runs the actual process_file loudness path against a real quiet-but-hot
    MP3 and re-measures the *output* file's true peak independently. Before
    the fix this reliably clipped (~+5.9 dBFS out of a -1.0 dBTP ceiling)."""
    _require_ffmpeg()
    path = _quiet_but_hot_mp3(tmp_path)

    before = ap._measure_lufs(path)
    assert before is not None
    lufs_before, peak_before = before
    assert lufs_before < config.TARGET_LUFS - config.LUFS_TOLERANCE
    assert peak_before > config.TRUE_PEAK_CEILING_DBTP  # already hotter than the ceiling

    result = ap.process_file(
        path, detect_bpm=False, detect_key=False, normalise=True,
    )
    assert result.ok
    assert result.normalised

    after = ap._measure_lufs(path)
    assert after is not None
    _, peak_after = after
    assert peak_after <= config.TRUE_PEAK_CEILING_DBTP + 0.3  # small ffmpeg measurement tolerance
