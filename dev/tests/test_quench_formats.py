"""
Quench (loudness levelling) must hand back the same format it was given.

Found by running Quench on real audio: AIFF came back as little-endian AIFF-C with its
artist/album/key/BPM tags and cover art gone, 16-bit FLAC was silently widened to 24-bit
(+75% size), and every m4a failed because ffmpeg was told to filter and stream-copy at once.
These tests rebuild each case with synthetic audio and a real ffmpeg.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2] / "fablegear"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audio_processor as ap

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

TAGS = {"artist": "Quench Test", "album": "Format Safety", "title": "Tone"}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, capture_output=True)


def _make(tmp_path: Path, name: str, codec_args: list[str], *, art: bool = True) -> Path:
    """A 3 s quiet stereo tone with tags and (optionally) an attached JPEG cover."""
    out = tmp_path / name
    inputs = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3:sample_rate=44100"]
    maps = ["-map", "0:a"]
    if art:
        cover = tmp_path / "cover.jpg"
        if not cover.exists():
            _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(cover))
        inputs += ["-i", str(cover)]
        maps += ["-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    meta = [x for k, v in TAGS.items() for x in ("-metadata", f"{k}={v}")]
    _ffmpeg(*inputs, "-ac", "2", *maps, *codec_args, *meta, str(out))
    return out


def _probe(path: Path) -> dict:
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
         str(path)], check=True, capture_output=True, text=True).stdout)
    audio = next(s for s in j["streams"] if s["codec_type"] == "audio")
    return {
        "codec": audio["codec_name"],
        "fmt": audio.get("sample_fmt"),
        "art": any(s["codec_type"] == "video" for s in j["streams"]),
        "tags": {k.lower(): v for k, v in (j["format"].get("tags") or {}).items()},
    }


@pytest.fixture(autouse=True)
def _quiet_target(monkeypatch):
    # The synthetic tone is ~ -21 LUFS; a -30 target always needs a cut, which is never
    # limited by true-peak headroom.
    monkeypatch.setattr(ap, "TARGET_LUFS", -30.0)


def _quench(path: Path):
    result = ap.process_file(path, detect_bpm=False, detect_key=False, normalise=True)
    assert result.ok, result.errors
    return result


def test_aiff_stays_big_endian_and_keeps_tags_and_art(tmp_path):
    src = _make(tmp_path, "t.aiff", ["-c:a", "pcm_s16be", "-write_id3v2", "1"])
    assert _probe(src)["codec"] == "pcm_s16be"

    assert _quench(src).normalised

    out = _probe(src)
    assert out["codec"] == "pcm_s16be"        # not AIFF-C little-endian "sowt"
    assert out["art"]
    for key, value in TAGS.items():
        assert out["tags"].get(key) == value


def test_16_bit_flac_is_not_widened_and_keeps_tags_and_art(tmp_path):
    src = _make(tmp_path, "t.flac", ["-c:a", "flac", "-sample_fmt", "s16"])
    assert _probe(src)["fmt"] == "s16"

    assert _quench(src).normalised

    out = _probe(src)
    assert out["fmt"] == "s16"
    assert out["art"]
    for key, value in TAGS.items():
        assert out["tags"].get(key) == value


def test_24_bit_wav_keeps_its_bit_depth(tmp_path):
    src = _make(tmp_path, "t.wav", ["-c:a", "pcm_s24le"], art=False)

    assert _quench(src).normalised

    assert _probe(src)["codec"] == "pcm_s24le"


@pytest.mark.parametrize("codec", ["pcm_f32le", "pcm_f64le"])
def test_floating_point_wav_stays_floating_point(tmp_path, codec):
    src = _make(tmp_path, "t.wav", ["-c:a", codec], art=False)

    assert _quench(src).normalised

    assert _probe(src)["codec"] == codec


def test_m4a_is_left_alone_not_failed(tmp_path):
    """AAC can't be levelled without a lossy re-encode that drops MP4 tags, so Quench
    skips it, and says so, rather than erroring or rewriting it."""
    src = _make(tmp_path, "t.m4a", ["-c:a", "aac", "-b:a", "128k"])
    before = src.read_bytes()

    result = _quench(src)

    assert result.skipped_loudness
    assert not result.normalised
    assert src.read_bytes() == before


def test_unsupported_suffix_has_no_rewrite_recipe(tmp_path):
    assert ap._get_ffmpeg_codec_args(tmp_path / "x.m4a") is None
    assert ap._get_ffmpeg_codec_args(tmp_path / "x.ogg") is None
    assert ap._get_ffmpeg_codec_args(tmp_path / "x.mp3") is not None


def test_refactor_to_aiff_writes_big_endian_with_id3(tmp_path):
    """Re-Factor shares the codec choice, so it must not produce little-endian AIFF-C
    or drop the tags either."""
    src = _make(tmp_path, "t.wav", ["-c:a", "pcm_s16le"], art=False)

    ok, message = ap._convert_file(src, "aiff")

    assert ok, message
    out = _probe(tmp_path / "t.aiff")
    assert out["codec"] == "pcm_s16be"
    for key, value in TAGS.items():
        assert out["tags"].get(key) == value
