"""
Anvil's Ogg support (container family B, second half) -- Vorbis and Opus.

The re-paging in ogg.py is the riskiest code in Anvil: growing or shrinking
the comment packet forces the entire page layout to be recomputed, and a
mistake there produces a file that looks fine to a lenient parser but fails a
real decoder or corrupts audio. These tests check both: that fields round
-trip, and that decodability + audio content survive rewriting untouched.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import anvil
from anvil import TrackFields
from anvil.ogg import _packets_from_pages, _parse_pages


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping Ogg fixture test")


def _decode_pcm(path: Path) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    return out.stdout


@pytest.fixture
def vorbis_file(tmp_path: Path) -> Path:
    _require_ffmpeg()
    path = tmp_path / "test.ogg"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-ac", "2",
         "-c:a", "vorbis", "-q:a", "2", "-strict", "-2", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def opus_file(tmp_path: Path) -> Path:
    _require_ffmpeg()
    path = tmp_path / "test.opus"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "libopus", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.mark.parametrize("fixture_name", ["vorbis_file", "opus_file"])
def test_write_then_read_round_trips(fixture_name, request):
    path = request.getfixturevalue(fixture_name)
    anvil.write_fields(
        path,
        TrackFields(title="Midnight Drive", artist="Test Artist", bpm=128.5, initial_key="Am"),
    )
    fields = anvil.read_fields(path)
    assert fields.title == "Midnight Drive"
    assert fields.artist == "Test Artist"
    assert fields.bpm == pytest.approx(128.5)
    assert fields.initial_key == "Am"


@pytest.mark.parametrize("fixture_name", ["vorbis_file", "opus_file"])
def test_rewrite_preserves_decodable_audio(fixture_name, request):
    """The defect this guards: a page-layout bug that still parses as valid
    Ogg but silently corrupts or truncates the actual audio samples."""
    path = request.getfixturevalue(fixture_name)
    before_pcm = _decode_pcm(path)

    anvil.write_fields(path, TrackFields(bpm=140.0), force=True)

    after_pcm = _decode_pcm(path)
    # The shared prefix must be byte-identical -- no corrupted samples.
    # (Trailing length can differ by a few frames due to decoder-side
    # trim heuristics at end-of-stream; the file's own declared duration,
    # checked separately below, is what actually matters.)
    n = min(len(before_pcm), len(after_pcm))
    assert n > 0
    assert before_pcm[:n] == after_pcm[:n]


@pytest.mark.parametrize("fixture_name", ["vorbis_file", "opus_file"])
def test_rewrite_preserves_declared_duration(fixture_name, request):
    path = request.getfixturevalue(fixture_name)
    before = path.read_bytes()
    pages_before = _parse_pages(before)
    _packets_before, granules_before = _packets_from_pages(pages_before)

    anvil.write_fields(path, TrackFields(bpm=140.0), force=True)

    after = path.read_bytes()
    pages_after = _parse_pages(after)
    _packets_after, granules_after = _packets_from_pages(pages_after)

    assert granules_after[-1] == granules_before[-1]


@pytest.mark.parametrize("fixture_name", ["vorbis_file", "opus_file"])
def test_identification_packet_alone_on_first_page(fixture_name, request):
    """Spec requirement: the first page of a Vorbis/Opus stream must contain
    only the identification packet. Violating this is exactly what an earlier
    version of the greedy page-packer did for any file short enough that
    every packet fit under the 255-segment cap."""
    path = request.getfixturevalue(fixture_name)
    anvil.write_fields(path, TrackFields(bpm=140.0), force=True)

    data = path.read_bytes()
    pages = _parse_pages(data)
    first_page = pages[0]
    # Exactly one segment ending the ident packet -- nothing else on this page.
    assert len(first_page.segments) >= 1
    assert first_page.segments[-1] < 255  # ident packet terminates on this page
    packets, _granules = _packets_from_pages([first_page])
    assert len(packets) == 1


@pytest.mark.parametrize("fixture_name", ["vorbis_file", "opus_file"])
def test_merge_semantics_keep_existing_value(fixture_name, request):
    path = request.getfixturevalue(fixture_name)
    anvil.write_fields(path, TrackFields(bpm=120.0))
    result = anvil.write_fields(path, TrackFields(bpm=175.0))
    assert result.written == {}
    assert result.kept["bpm"] == pytest.approx(120.0)
