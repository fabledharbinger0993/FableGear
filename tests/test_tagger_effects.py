import shutil
import subprocess, sys
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TBPM, TKEY

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audio_processor as ap


def _require_ffmpeg() -> None:
    """Audio fixtures are synthesized with ffmpeg (a hard runtime dependency of
    the app itself); skip fixture-based tests on runners that lack it instead
    of failing the whole suite with FileNotFoundError."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed — skipping audio-fixture test")


def _silent_mp3_with_bpm(tmp_path: Path) -> Path:
    """1s silent MP3 tagged with an existing BPM, via ffmpeg + mutagen."""
    _require_ffmpeg()
    p = tmp_path / "track.mp3"
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available; required to generate MP3 fixture")
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", "1", "-q:a", "9", str(p)],
        check=True, capture_output=True,
    )
    tags = ID3()
    tags.add(TBPM(encoding=3, text=["120"]))
    tags.save(str(p))
    return p


def _silent_mp3_with_key(tmp_path: Path) -> Path:
    """1s silent MP3 tagged with an existing key, via ffmpeg + mutagen."""
    _require_ffmpeg()
    p = tmp_path / "track_key.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", "1", "-q:a", "9", str(p)],
        check=True, capture_output=True,
    )
    tags = ID3()
    tags.add(TKEY(encoding=3, text=["8A"]))
    tags.save(str(p))
    return p


def test_existing_bpm_skipped_without_force(tmp_path, monkeypatch):
    f = _silent_mp3_with_bpm(tmp_path)
    # isolate: no real detection needed for the skip path, but guard anyway
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: None)
    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False)
    assert r.skipped_bpm is True
    assert r.bpm_written is False


def test_force_bpm_overrides_existing_tag(tmp_path, monkeypatch):
    f = _silent_mp3_with_bpm(tmp_path)
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: ("AUDIO", 44100))
    monkeypatch.setattr(ap, "_detect_bpm", lambda *a, **k: 128.0)
    written = {}
    monkeypatch.setattr(ap, "_write_tags",
                        lambda path, bpm=None, key=None: written.update(bpm=bpm))
    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False,
                        force_bpm=True)
    assert r.skipped_bpm is False
    assert r.bpm_detected == 128.0
    assert written.get("bpm") == 128.0


def test_existing_key_skipped_without_force(tmp_path, monkeypatch):
    f = _silent_mp3_with_key(tmp_path)
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: None)
    r = ap.process_file(f, detect_bpm=False, detect_key=True, normalise=False)
    assert r.skipped_key is True
    assert r.key_written is False


def test_force_key_overrides_existing_tag(tmp_path, monkeypatch):
    f = _silent_mp3_with_key(tmp_path)
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: ("AUDIO", 44100))
    monkeypatch.setattr(ap, "_detect_key", lambda *a, **k: "3A")
    written = {}
    monkeypatch.setattr(ap, "_write_tags",
                        lambda path, bpm=None, key=None: written.update(key=key))
    r = ap.process_file(f, detect_bpm=False, detect_key=True, normalise=False,
                        force_key=True)
    assert r.skipped_key is False
    assert r.key_written is True
    assert r.key_detected == "3A"
    assert written.get("key") == "3A"


def test_process_directory_forwards_per_effect_force(tmp_path, monkeypatch):
    captured = {}

    def fake_process_file(path, **kwargs):
        captured.update(kwargs)
        return ap.ProcessResult(path=path)

    # one file so scan_directory yields something
    (tmp_path / "a.mp3").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(ap, "process_file", fake_process_file)
    monkeypatch.setattr("scanner.scan_directory",
                        lambda root: [type("T", (), {"path": tmp_path / "a.mp3"})()])

    ap.process_directory(
        tmp_path,
        force_bpm=True,
        force_key=False,
        force_normalize=True,
        force_enrich=True,
        normalise=False,
    )
    assert captured.get("force_bpm") is True
    assert captured.get("force_key") is False
    assert captured.get("force_normalize") is True
    assert captured.get("force_enrich") is True


def test_journal_records_per_effect_counts(tmp_path):
    import sys
    from pathlib import Path
    REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if str(REPO_ROOT / "chop_shop") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

    import cli
    from fablegear_database.database import FableGearDatabase
    from fablegear_database.schema import DatabaseConfig
    import json

    archive = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))
    r1 = ap.ProcessResult(path=tmp_path / "a.mp3", bpm_detected=120.0,
                          bpm_written=True, normalised=True)
    r2 = ap.ProcessResult(path=tmp_path / "b.mp3", key_detected="8A",
                          key_written=True, enrich_written=True)
    cli._persist_process_results([r1, r2], archive)

    # Read the fg_processing_log directly to get the metadata
    with archive.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT metadata FROM fg_processing_log WHERE operation_type='tag_tracks'"
        )
        rows = cursor.fetchall()

    assert len(rows) > 0, "No tag_tracks operation found in log"
    meta = json.loads(rows[-1][0])
    assert meta["normalized"] == 1
    assert meta["enrich_written"] == 1


def test_cli_parser_has_per_effect_force():
    import cli
    parser = cli.build_parser()
    ns = parser.parse_args(["process", "/tmp/x", "--force-bpm"])
    assert ns.force_bpm is True
    assert ns.force_key is False


def test_cli_parser_has_process_modes():
    import cli
    parser = cli.build_parser()
    ns = parser.parse_args(
        [
            "process", "/tmp/x",
            "--bpm-mode", "aggressive",
            "--key-mode", "off",
            "--normalize-mode", "passive",
            "--enrich-mode", "aggressive",
            "--rename-mode", "passive",
        ]
    )
    assert ns.bpm_mode == "aggressive"
    assert ns.key_mode == "off"
    assert ns.normalize_mode == "passive"
    assert ns.enrich_mode == "aggressive"
    assert ns.rename_mode == "passive"
