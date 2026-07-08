import subprocess, sys
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TBPM

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audio_processor as ap


def _silent_mp3_with_bpm(tmp_path: Path) -> Path:
    """1s silent MP3 tagged with an existing BPM, via ffmpeg + mutagen."""
    p = tmp_path / "track.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", "1", "-q:a", "9", str(p)],
        check=True, capture_output=True,
    )
    tags = ID3()
    tags.add(TBPM(encoding=3, text=["120"]))
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
