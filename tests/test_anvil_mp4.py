"""
Anvil's MP4/M4A support (container family C).

The riskiest part of mp4.py is chunk-offset patching: when `moov` precedes
`mdat` and rewriting `ilst` changes moov's total size, every stco/co64 entry
in every trak must shift by that exact delta or the audio becomes unreadable
garbage from the wrong file offset. These tests force a real size change
(not a no-op) and then prove the audio still decodes to the same PCM.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import anvil
from anvil import TrackFields
from anvil.mp4 import _find, _split_boxes


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping MP4 fixture test")


def _decode_pcm(path: Path) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    return out.stdout


@pytest.fixture
def m4a_file(tmp_path: Path) -> Path:
    _require_ffmpeg()
    path = tmp_path / "test.m4a"
    # +faststart puts moov before mdat -- the layout that actually requires
    # stco/co64 patching when a metadata write resizes moov. Without it,
    # ffmpeg's default muxer writes mdat first, which would let the risky
    # code path go untested.
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_write_then_read_round_trips(m4a_file):
    anvil.write_fields(
        m4a_file,
        TrackFields(title="Midnight Drive", artist="Test Artist", bpm=128.5, initial_key="Am"),
    )
    fields = anvil.read_fields(m4a_file)
    assert fields.title == "Midnight Drive"
    assert fields.artist == "Test Artist"
    assert fields.bpm == pytest.approx(128.5)
    assert fields.initial_key == "Am"


def test_moov_precedes_mdat_in_fixture(m4a_file):
    """Ground the chunk-offset-patch test: this only exercises the risky path
    (moov before mdat) if the fixture is actually laid out that way."""
    top = _split_boxes(m4a_file.read_bytes())
    types = [t for t, _p in top]
    assert types.index(b"moov") < types.index(b"mdat")


def test_large_metadata_write_forces_moov_resize_and_stays_decodable(m4a_file):
    """
    A long value makes the new ilst bigger than any padding a `free` box
    could plausibly absorb, forcing moov's total size to actually change --
    the case that requires stco patching, not the case that might skip it.
    """
    before_pcm = _decode_pcm(m4a_file)

    long_value = "A" * 4000
    anvil.write_fields(m4a_file, TrackFields(mix_descriptor=long_value), force=True)

    fields = anvil.read_fields(m4a_file)
    assert fields.mix_descriptor == long_value

    after_pcm = _decode_pcm(m4a_file)
    assert after_pcm == before_pcm


def test_shrinking_metadata_also_stays_decodable(m4a_file):
    anvil.write_fields(m4a_file, TrackFields(mix_descriptor="A" * 4000), force=True)
    before_pcm = _decode_pcm(m4a_file)

    anvil.write_fields(m4a_file, TrackFields(mix_descriptor="short"), force=True)

    assert anvil.read_fields(m4a_file).mix_descriptor == "short"
    after_pcm = _decode_pcm(m4a_file)
    assert after_pcm == before_pcm


def test_merge_semantics_keep_existing_value(m4a_file):
    anvil.write_fields(m4a_file, TrackFields(bpm=120.0))
    result = anvil.write_fields(m4a_file, TrackFields(bpm=175.0))
    assert result.written == {}
    assert result.kept["bpm"] == pytest.approx(120.0)


def test_force_overwrites_existing_value(m4a_file):
    anvil.write_fields(m4a_file, TrackFields(bpm=120.0))
    result = anvil.write_fields(m4a_file, TrackFields(bpm=175.0), force=True)
    assert result.written["bpm"] == pytest.approx(175.0)


def test_dj_native_fields_round_trip(m4a_file):
    anvil.write_fields(
        m4a_file,
        TrackFields(mix_descriptor="Extended Mix", track_role="Instrumental", energy_level=7),
    )
    fields = anvil.read_fields(m4a_file)
    assert fields.mix_descriptor == "Extended Mix"
    assert fields.track_role == "Instrumental"
    assert fields.energy_level == 7


def test_clear_fields_removes_only_named_field(m4a_file):
    anvil.write_fields(m4a_file, TrackFields(bpm=128.0, title="Keep Me"))
    anvil.clear_fields(m4a_file, ["bpm"])
    fields = anvil.read_fields(m4a_file)
    assert fields.bpm is None
    assert fields.title == "Keep Me"


def test_existing_udta_siblings_survive(m4a_file):
    """udta can carry more than just meta in the wild; a rewrite must not
    drop siblings it doesn't understand."""
    top = _split_boxes(m4a_file.read_bytes())
    moov = _find(top, b"moov")
    udta = _find(_split_boxes(moov), b"udta")
    sibling_types_before = {t for t, _p in _split_boxes(udta)}

    anvil.write_fields(m4a_file, TrackFields(bpm=128.0))

    top_after = _split_boxes(m4a_file.read_bytes())
    moov_after = _find(top_after, b"moov")
    udta_after = _find(_split_boxes(moov_after), b"udta")
    sibling_types_after = {t for t, _p in _split_boxes(udta_after)}
    assert sibling_types_before <= sibling_types_after
