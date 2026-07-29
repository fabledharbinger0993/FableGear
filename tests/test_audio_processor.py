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


def _burst_mp3(tmp_path: Path, name: str, burst: float, floor: float) -> Path:
    """A 1 kHz burst train: brief loud transients over a quiet bed. This is the
    crest factor of ordinary percussive music — low integrated loudness with a
    much higher peak — not a pathological edge case."""
    _require_ffmpeg()
    p = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"aevalsrc='if(lt(mod(t,0.5),0.015),"
                f"{burst}*sin(2*PI*1000*t),{floor}*sin(2*PI*1000*t))':s=44100:d=10",
         "-codec:a", "libmp3lame", "-b:a", "320k", str(p)],
        check=True, capture_output=True,
    )
    return p


def _quiet_with_headroom_mp3(tmp_path: Path) -> Path:
    """Quiet, and peaking well below the ceiling: ~-24 LUFS / ~-6.9 dBTP.
    Wants +16 dB to reach target but only ~+5.9 dB of headroom exists, so the
    boost must be capped — the case that proves capping still normalises."""
    return _burst_mp3(tmp_path, "quiet_with_headroom.mp3", 0.45, 0.02)


def _quiet_but_hot_mp3(tmp_path: Path) -> Path:
    """Quiet but already at the ceiling: ~-17 LUFS / ~-0.1 dBTP. Wants +9 dB
    with negative headroom — the case that must be skipped, not attenuated."""
    return _burst_mp3(tmp_path, "quiet_but_hot.mp3", 0.99, 0.05)


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


# ── the cap must not invert a boost into an attenuation ─────────────────────
#
# Most commercial masters already peak ABOVE a -1.0 dBTP ceiling, so
# `ceiling - true_peak` is negative for them. A cap written as a bare
# min(want, headroom) goes negative and silently turns "make this louder"
# into "make this quieter, and re-encode it" — lossy generation loss for no
# benefit. These pin the clamp at zero.

@pytest.mark.parametrize("lufs,tp", [
    (-12.8, -0.17),   # real measured values from a DJ library
    (-12.9, -0.09),
    (-10.1, 0.07),    # peak already over full scale
    (-9.4, -0.51),
])
def test_boost_never_inverts_to_attenuation_when_peak_exceeds_ceiling(lufs, tp):
    want = -8.0 - lufs
    assert want > 0                              # a boost really was requested
    gain = ap._capped_gain_db(lufs, tp, target=-8.0, ceiling=-1.0)
    assert gain == pytest.approx(0.0)            # clamped to "do nothing"
    assert gain >= 0.0                           # and never negative


def test_no_headroom_skips_the_rewrite_entirely(tmp_path):
    """A real file with no headroom must be left byte-for-byte alone — not
    re-encoded to apply a ~0 dB change, which on MP3 costs a generation of
    quality for no audible benefit."""
    _require_ffmpeg()
    path = _quiet_but_hot_mp3(tmp_path)

    lufs, tp = ap._measure_lufs(path)
    assert lufs < config.TARGET_LUFS - config.LUFS_TOLERANCE   # a boost is wanted
    assert tp > config.TRUE_PEAK_CEILING_DBTP                  # but no headroom exists
    before_bytes = path.read_bytes()

    result = ap.process_file(path, detect_bpm=False, detect_key=False, normalise=True)

    assert result.ok
    assert result.skipped_loudness
    assert not result.normalised
    assert path.read_bytes() == before_bytes     # bit-identical


# ── end-to-end: the real bug, on real audio ─────────────────────────────────

def test_normalise_never_drives_true_peak_past_ceiling(tmp_path):
    """Runs the actual process_file loudness path against a real file that
    wants far more gain than it has headroom for, and re-measures the *output*
    file's true peak independently. Before the fix the full +16 dB was applied
    and the output clipped hard."""
    _require_ffmpeg()
    path = _quiet_with_headroom_mp3(tmp_path)

    before = ap._measure_lufs(path)
    assert before is not None
    lufs_before, peak_before = before
    naive_gain = config.TARGET_LUFS - lufs_before
    assert naive_gain > 0
    # The unguarded gain would have driven the peak past full scale.
    assert peak_before + naive_gain > 0.0

    result = ap.process_file(
        path, detect_bpm=False, detect_key=False, normalise=True,
    )
    assert result.ok
    assert result.normalised            # capping still normalises, just less

    after = ap._measure_lufs(path)
    assert after is not None
    lufs_after, peak_after = after
    assert peak_after <= config.TRUE_PEAK_CEILING_DBTP + 0.3  # ffmpeg measurement tolerance
    assert lufs_after > lufs_before                            # it did get louder
