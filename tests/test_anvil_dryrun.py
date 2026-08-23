"""
anvil.dryrun: the survey runs clean against real files, correctly reports
coverage across every container family Anvil handles, and never modifies
anything -- the same read-only guarantee iron.dryrun already has a dedicated
test for (test_iron_dryrun.py), which anvil.dryrun was missing until now.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from anvil import dryrun


def _require_ffmpeg():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping dryrun fixture test")


FAKE_MPEG = b"\xff\xfb\x90\x44" + bytes(range(256)) * 4


def _write_mp3(path, *, tagged: bool = False) -> None:
    path.write_bytes(FAKE_MPEG)
    if tagged:
        import anvil as anvil_pkg
        anvil_pkg.write_fields(path, anvil_pkg.TrackFields(title="Tagged", bpm=128.0))


def test_survey_reports_files_and_never_writes(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    _write_mp3(a, tagged=True)
    _write_mp3(b, tagged=False)

    mtimes_before = {p: p.stat().st_mtime_ns for p in (a, b)}

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 2
    assert all(f.status == "ok" for f in result.files)

    for p, before in mtimes_before.items():
        assert p.stat().st_mtime_ns == before, f"{p} was modified by a read-only survey"


def test_survey_skips_non_audio_and_apple_double(tmp_path):
    _write_mp3(tmp_path / "track.mp3")
    (tmp_path / "notes.txt").write_text("not audio")
    (tmp_path / "._track.mp3").write_bytes(b"\x00" * 16)  # AppleDouble sidecar

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 1
    assert result.files[0].path.endswith("track.mp3")


def test_survey_respects_limit(tmp_path):
    for i in range(4):
        _write_mp3(tmp_path / f"t{i}.mp3")

    result = dryrun.survey(tmp_path, limit=2, progress=False)

    assert result.scanned == 2


def test_survey_reports_unreadable_file_without_raising(tmp_path):
    """A file too short/garbled to identify must be reported, not crash the
    whole survey -- whether Anvil classifies it as an unsupported container
    or a corrupt one, the read-only walk over the rest of the folder must
    continue regardless."""
    bad = tmp_path / "corrupt.mp3"
    bad.write_bytes(b"\x00" * 3)  # too short to even identify a container

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 1
    assert result.files[0].status != "ok"
    assert result.files[0].detail


def test_survey_reports_raw_aac_as_not_implemented(tmp_path):
    (tmp_path / "raw.aac").write_bytes(b"\xff\xf1" + bytes(64))

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 1
    assert result.files[0].status == "unsupported_ext"


def test_survey_would_write_vs_would_keep_reflects_merge_rule(tmp_path):
    tagged = tmp_path / "tagged.mp3"
    untagged = tmp_path / "untagged.mp3"
    _write_mp3(tagged, tagged=True)
    _write_mp3(untagged, tagged=False)

    result = dryrun.survey(tmp_path, candidates={"bpm"}, progress=False)

    by_name = {f.path.split("/")[-1]: f for f in result.files}
    assert by_name["tagged.mp3"].would_keep == ["bpm"]
    assert by_name["untagged.mp3"].would_write == ["bpm"]


def test_survey_handles_flac_and_mp4_alongside_id3(tmp_path):
    """Family B/C coverage: dryrun must not report these as 'unsupported_ext'
    now that Anvil handles them -- that classification is reserved for raw
    AAC/WavPack, which genuinely have no writer yet."""
    _require_ffmpeg()

    flac_path = tmp_path / "t.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "flac", str(flac_path)],
        check=True, capture_output=True,
    )
    m4a_path = tmp_path / "t.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(m4a_path)],
        check=True, capture_output=True,
    )

    import anvil as anvil_pkg
    anvil_pkg.write_fields(flac_path, anvil_pkg.TrackFields(bpm=128.0))

    result = dryrun.survey(tmp_path, progress=False)

    assert result.scanned == 2
    statuses = {f.path.split("/")[-1]: f.status for f in result.files}
    assert statuses["t.flac"] == "ok"
    assert statuses["t.m4a"] == "ok"

    flac_report = next(f for f in result.files if f.path.endswith("t.flac"))
    assert flac_report.fields.get("bpm") == pytest.approx(128.0)
