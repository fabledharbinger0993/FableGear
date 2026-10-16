"""
Anvil's FLAC support (container family B, first half).

Self-consistency for the FLAC metadata-block splicing: STREAMINFO and other
blocks (PICTURE, PADDING) survive a comment rewrite untouched and in place,
and the audio frames after the last metadata block are never touched.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import anvil
from anvil import TrackFields
from anvil.flac import _parse_blocks


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping FLAC fixture test")


@pytest.fixture
def flac_file(tmp_path: Path) -> Path:
    _require_ffmpeg()
    path = tmp_path / "test.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "flac", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_read_empty_flac_has_no_fields(flac_file):
    fields = anvil.read_fields(flac_file)
    assert fields.is_empty()


def test_write_then_read_round_trips(flac_file):
    anvil.write_fields(
        flac_file,
        TrackFields(title="Midnight Drive", artist="Test Artist", bpm=128.5, initial_key="Am"),
    )
    fields = anvil.read_fields(flac_file)
    assert fields.title == "Midnight Drive"
    assert fields.artist == "Test Artist"
    assert fields.bpm == pytest.approx(128.5)
    assert fields.initial_key == "Am"


def test_write_preserves_streaminfo_and_audio(flac_file):
    before = flac_file.read_bytes()
    blocks_before, audio_start_before = _parse_blocks(before)
    streaminfo_before = next(p for t, p in blocks_before if t == 0)
    audio_before = before[audio_start_before:]

    anvil.write_fields(flac_file, TrackFields(bpm=140.0))

    after = flac_file.read_bytes()
    blocks_after, audio_start_after = _parse_blocks(after)
    streaminfo_after = next(p for t, p in blocks_after if t == 0)
    audio_after = after[audio_start_after:]

    assert streaminfo_after == streaminfo_before
    assert audio_after == audio_before


def test_merge_semantics_keep_existing_value(flac_file):
    anvil.write_fields(flac_file, TrackFields(bpm=120.0))
    result = anvil.write_fields(flac_file, TrackFields(bpm=175.0))
    assert result.written == {}
    assert result.kept["bpm"] == pytest.approx(120.0)


def test_force_overwrites_existing_value(flac_file):
    anvil.write_fields(flac_file, TrackFields(bpm=120.0))
    result = anvil.write_fields(flac_file, TrackFields(bpm=175.0), force=True)
    assert result.written["bpm"] == pytest.approx(175.0)
    assert anvil.read_fields(flac_file).bpm == pytest.approx(175.0)


def test_dj_native_fields_round_trip(flac_file):
    anvil.write_fields(
        flac_file,
        TrackFields(mix_descriptor="Extended Mix", track_role="Instrumental", energy_level=7),
    )
    fields = anvil.read_fields(flac_file)
    assert fields.mix_descriptor == "Extended Mix"
    assert fields.track_role == "Instrumental"
    assert fields.energy_level == 7


def test_clear_fields_removes_only_named_field(flac_file):
    anvil.write_fields(flac_file, TrackFields(bpm=128.0, title="Keep Me"))
    anvil.clear_fields(flac_file, ["bpm"])
    fields = anvil.read_fields(flac_file)
    assert fields.bpm is None
    assert fields.title == "Keep Me"
