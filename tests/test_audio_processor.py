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

    measured = ap._measure_lufs(path)
    assert measured is not None
    lufs, tp = measured
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


# ── beat-tracker selection and fallback ─────────────────────────────────────
#
# essentia is an OPTIONAL dependency that is materially more accurate than the
# librosa path (exact-BPM agreement with Rekordbox ground truth 13.4% -> 91.4%
# on a 300-track sample), so it is preferred when importable. But it must never
# become load-bearing: a missing essentia, or a per-file essentia failure, has
# to fall back to librosa rather than silently yielding no BPM. The fallback is
# easy to break by skipping the shared decode too eagerly, which is exactly
# what these pin.

def _tagless_mp3(tmp_path: Path) -> Path:
    _require_ffmpeg()
    p = tmp_path / "plain.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-q:a", "9", str(p)],
        check=True, capture_output=True,
    )
    return p


def test_essentia_is_preferred_when_available(tmp_path, monkeypatch):
    f = _tagless_mp3(tmp_path)
    monkeypatch.setattr(ap, "_essentia_available", lambda: True)
    monkeypatch.setattr(ap, "_detect_bpm_essentia", lambda p: (123.45, 3.2))
    monkeypatch.setattr(ap, "_detect_bpm", lambda *a, **k: pytest.fail(
        "librosa must not run when essentia succeeded"))
    monkeypatch.setattr(ap, "_write_tags", lambda path, bpm=None, key=None: None)

    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False)

    assert r.bpm_detected == 123.45
    assert r.bpm_source == "essentia"
    assert r.bpm_confidence == 3.2


def test_falls_back_to_librosa_when_essentia_missing(tmp_path, monkeypatch):
    f = _tagless_mp3(tmp_path)
    monkeypatch.setattr(ap, "_essentia_available", lambda: False)
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: ("AUDIO", 44100))
    monkeypatch.setattr(ap, "_detect_bpm", lambda *a, **k: 128.0)
    monkeypatch.setattr(ap, "_write_tags", lambda path, bpm=None, key=None: None)

    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False)

    assert r.bpm_detected == 128.0
    assert r.bpm_source == "librosa"
    assert r.bpm_confidence is None


def test_falls_back_to_librosa_when_essentia_fails_on_this_file(tmp_path, monkeypatch):
    """The regression that matters: essentia importable but failing on one file
    (9/300 in the real evaluation, all undecodable) must still produce a BPM.
    Skipping the shared decode because 'essentia is available' silently kills
    this path."""
    f = _tagless_mp3(tmp_path)
    monkeypatch.setattr(ap, "_essentia_available", lambda: True)
    monkeypatch.setattr(ap, "_detect_bpm_essentia", lambda p: None)   # per-file failure
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: ("AUDIO", 44100))
    monkeypatch.setattr(ap, "_detect_bpm", lambda *a, **k: 128.0)
    monkeypatch.setattr(ap, "_write_tags", lambda path, bpm=None, key=None: None)

    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False)

    assert r.bpm_detected == 128.0
    assert r.bpm_source == "librosa"


def test_bpm_only_run_skips_the_shared_decode_when_essentia_succeeds(tmp_path, monkeypatch):
    """essentia loads the file itself, so a BPM-only run must not also pay for
    the 90 s ffmpeg decode that nothing would read."""
    f = _tagless_mp3(tmp_path)
    monkeypatch.setattr(ap, "_essentia_available", lambda: True)
    monkeypatch.setattr(ap, "_detect_bpm_essentia", lambda p: (120.0, 3.0))
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: pytest.fail(
        "shared decode should be skipped when essentia handled BPM and key is off"))
    monkeypatch.setattr(ap, "_write_tags", lambda path, bpm=None, key=None: None)

    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False)
    assert r.bpm_detected == 120.0


def test_key_still_gets_its_decode_even_when_essentia_handles_bpm(tmp_path, monkeypatch):
    f = _tagless_mp3(tmp_path)
    monkeypatch.setattr(ap, "_essentia_available", lambda: True)
    monkeypatch.setattr(ap, "_detect_bpm_essentia", lambda p: (120.0, 3.0))
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: ("AUDIO", 44100))
    monkeypatch.setattr(ap, "_detect_key", lambda *a, **k: "8A")
    monkeypatch.setattr(ap, "_write_tags", lambda path, bpm=None, key=None: None)

    r = ap.process_file(f, detect_bpm=True, detect_key=True, normalise=False)

    assert r.bpm_detected == 120.0
    assert r.key_detected == "8A"


def test_essentia_out_of_range_bpm_is_rejected(tmp_path, monkeypatch):
    """A 1 s silent fixture makes essentia report ~738 BPM. Out-of-range values
    must be discarded so they can't reach a tag or a beat grid."""
    monkeypatch.setattr(ap, "_essentia_available", lambda: True)
    f = _tagless_mp3(tmp_path)
    assert ap._detect_bpm_essentia(f) is None


# ── packaging guard ─────────────────────────────────────────────────────────

def test_health_warns_when_beat_tracker_degraded(monkeypatch):
    """The essentia fallback is deliberately silent so a missing optional
    dependency can't break processing. That makes it invisible — and in a
    packaged build it is the likely failure mode, since essentia is a C++
    extension imported inside a function that PyInstaller can miss. The health
    check is what turns a silent ~91% -> ~13% accuracy regression into a
    visible warning."""
    import health

    monkeypatch.setattr(ap, "_essentia_available", lambda: False)
    finding = health._check_beat_tracker()
    assert finding is not None
    assert finding.id == "beat_tracker_degraded"
    assert finding.severity == "warn"

    monkeypatch.setattr(ap, "_essentia_available", lambda: True)
    assert health._check_beat_tracker() is None


def test_beat_tracker_check_is_registered():
    """A check that exists but is never run is worse than no check."""
    import inspect

    import health
    src = inspect.getsource(health.run_health_checks)
    assert "_check_beat_tracker" in src
