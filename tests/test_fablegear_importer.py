"""
Tests for the database-first importer (``fablegear_database.importer``).

Run from the repo root:
    pip install pytest && python3 -m pytest tests/test_fablegear_importer.py -v

Two layers:

1. Unit tests inject a fake scanner that yields TrackInfo-like records pointing
   at real temp files. This exercises the importer's own logic — TrackInfo →
   ContentRecord mapping, the single bulk transaction, size+mtime change
   detection, the drive identifier, corruption flagging, fingerprinting — with
   no app config and no need for decodable audio.

2. One integration test drives the *real* ``scanner`` module over generated
   WAV files to prove the default wiring works end to end.
"""

import os
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import FableGearDatabase
from fablegear_database.importer import FileImporter
from fablegear_database.schema import DatabaseConfig


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

@dataclass
class FakeTrack:
    """Mirrors the attribute surface of scanner.TrackInfo."""
    path: Path
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    track_number: Optional[int] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    duration_seconds: Optional[float] = None
    bitrate: Optional[int] = None
    sample_rate: Optional[int] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    errors: List[str] = field(default_factory=list)


class FakeScanner:
    """Stand-in for the scanner module; yields tracks whose path is under root."""

    def __init__(self, tracks):
        self._tracks = tracks

    def scan_directory(self, root):
        root = Path(root)
        for t in self._tracks:
            try:
                Path(t.path).relative_to(root)
            except ValueError:
                continue
            yield t


@pytest.fixture
def db(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))


def _make_file(path: Path, content: bytes = b"audio-bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --------------------------------------------------------------------------- #
# Mapping + bulk insert
# --------------------------------------------------------------------------- #

def test_imports_new_files_with_mapped_metadata(db, tmp_path):
    music = tmp_path / "music"
    a = _make_file(music / "a.mp3")
    b = _make_file(music / "sub" / "b.flac")

    scanner = FakeScanner([
        FakeTrack(a, title="Track A", artist="Artist A", bpm=128.0,
                  key="8A", duration_seconds=200.0, sample_rate=44100,
                  file_type="MP3"),
        FakeTrack(b, title="Track B", artist="Artist B", genre="House",
                  file_type="FLAC"),
    ])
    stats = FileImporter(db, scanner_module=scanner).import_files([music])

    assert stats["total_files"] == 2
    assert stats["new_files"] == 2
    assert stats["updated_files"] == 0
    assert stats["error_files"] == 0

    rec_a = db.get_content_by_path(str(a))
    assert rec_a.title == "Track A"
    assert rec_a.artist == "Artist A"
    assert rec_a.bpm == 128.0
    assert rec_a.key == "8A"
    assert rec_a.format == "mp3"          # lowercased from file_type
    assert rec_a.sample_rate == 44100
    assert rec_a.file_hash                # hash was computed
    assert rec_a.processing_status == "scanned"

    rec_b = db.get_content_by_path(str(b))
    assert rec_b.genre == "House"
    assert rec_b.format == "flac"


def test_import_uses_a_single_bulk_transaction(db, tmp_path):
    music = tmp_path / "music"
    tracks = [FakeTrack(_make_file(music / f"{i}.mp3", f"x{i}".encode()),
                        title=f"T{i}") for i in range(5)]

    calls = {"n": 0, "sizes": []}
    original = db.bulk_upsert_content

    def spy(records):
        calls["n"] += 1
        calls["sizes"].append(len(records))
        return original(records)

    db.bulk_upsert_content = spy
    stats = FileImporter(db, scanner_module=FakeScanner(tracks)).import_files([music])

    assert stats["new_files"] == 5
    assert calls["n"] == 1                # one transaction for the whole batch
    assert calls["sizes"] == [5]
    assert db.get_statistics()["total_tracks"] == 5


# --------------------------------------------------------------------------- #
# Change detection
# --------------------------------------------------------------------------- #

def test_unchanged_files_are_skipped_on_rescan(db, tmp_path):
    music = tmp_path / "music"
    a = _make_file(music / "a.mp3")
    scanner = FakeScanner([FakeTrack(a, title="A", file_size=a.stat().st_size)])
    importer = FileImporter(db, scanner_module=scanner)

    first = importer.import_files([music])
    assert first["new_files"] == 1

    second = importer.import_files([music])
    assert second["new_files"] == 0
    assert second["updated_files"] == 0
    assert second["skipped_files"] == 1
    assert db.get_statistics()["total_tracks"] == 1


def test_changed_file_is_reimported(db, tmp_path):
    music = tmp_path / "music"
    a = _make_file(music / "a.mp3", b"original")
    scanner = FakeScanner([FakeTrack(a, title="A")])
    importer = FileImporter(db, scanner_module=scanner)

    importer.import_files([music])
    original_hash = db.get_content_by_path(str(a)).file_hash

    # Change content + bump mtime so size/mtime differ from the stored row.
    a.write_bytes(b"different content entirely")
    os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 10))

    stats = importer.import_files([music])
    assert stats["updated_files"] == 1
    assert stats["new_files"] == 0
    assert db.get_content_by_path(str(a)).file_hash != original_hash
    assert db.get_statistics()["total_tracks"] == 1   # updated in place, not duplicated


def test_force_refresh_reimports_everything(db, tmp_path):
    music = tmp_path / "music"
    a = _make_file(music / "a.mp3")
    scanner = FakeScanner([FakeTrack(a, title="A")])
    importer = FileImporter(db, scanner_module=scanner)

    importer.import_files([music])
    stats = importer.import_files([music], force_refresh=True)
    assert stats["skipped_files"] == 0
    assert stats["updated_files"] == 1


# --------------------------------------------------------------------------- #
# Drive identifier + corruption + robustness
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path,expected", [
    ("/Volumes/USB_A/music/x.mp3", "USB_A"),
    ("/Volumes/USB_B/sets/y.flac", "USB_B"),
    ("/Users/dj/Music/z.mp3", "local"),
    ("/Volumes", "local"),
])
def test_drive_identifier(path, expected):
    assert FileImporter._drive_for_path(Path(path)) == expected


def test_read_failures_flag_corruption_but_missing_tags_do_not(db, tmp_path):
    music = tmp_path / "music"
    good = _make_file(music / "good.wav")
    bad = _make_file(music / "bad.mp3")
    scanner = FakeScanner([
        FakeTrack(good, title="Good", errors=["no tags found"]),
        FakeTrack(bad, errors=["mutagen open failed: bad header"]),
    ])
    FileImporter(db, scanner_module=scanner).import_files([music])

    assert db.get_content_by_path(str(good)).is_corrupted in (0, False)
    assert db.get_content_by_path(str(bad)).is_corrupted in (1, True)


def test_missing_root_is_ignored(db, tmp_path):
    scanner = FakeScanner([])
    stats = FileImporter(db, scanner_module=scanner).import_files(
        [tmp_path / "does_not_exist"]
    )
    assert stats["total_files"] == 0
    assert stats["new_files"] == 0


def test_import_is_logged_to_processing_log(db, tmp_path):
    music = tmp_path / "music"
    _make_file(music / "a.mp3")
    _make_file(music / "b.mp3")
    scanner = FakeScanner([
        FakeTrack(music / "a.mp3", title="A"),
        FakeTrack(music / "b.mp3", title="B"),
    ])
    FileImporter(db, scanner_module=scanner).import_files([music])

    assert db.count_operations("import") == 1


def test_update_fingerprint(db, tmp_path):
    music = tmp_path / "music"
    a = _make_file(music / "a.mp3")
    importer = FileImporter(db, scanner_module=FakeScanner([FakeTrack(a, title="A")]))
    importer.import_files([music])

    assert importer.update_fingerprint(a, "FP-XYZ", quality=88) is True
    rec = db.get_content_by_path(str(a))
    assert rec.acoustic_fingerprint == "FP-XYZ"
    assert rec.fingerprint_quality == 88
    assert rec.processing_status == "fingerprinted"


# --------------------------------------------------------------------------- #
# Integration: the real scanner over generated WAV files
# --------------------------------------------------------------------------- #

def _write_wav(path: Path, seconds: float = 3.0, rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00\x00\x00" * frames)  # silence, stereo 16-bit
    return path


def test_integration_real_scanner_imports_wav(tmp_path, monkeypatch):
    """Drive the actual scanner module (which needs app config) end to end."""
    home = tmp_path / "home"
    music = tmp_path / "music"
    backups = tmp_path / "backups"
    for d in (home, music, backups):
        d.mkdir(parents=True, exist_ok=True)
    (home / "local.db").touch()
    (home / "device.db").touch()

    cfg_dir = home / ".fablegear"
    cfg_dir.mkdir()
    import json
    (cfg_dir / "config.json").write_text(json.dumps({
        "local_db": str(home / "local.db"),
        "device_db": str(home / "device.db"),
        "music_root": str(music),
        "backup_dir": str(backups),
        "mobile_token": "test-token",
    }))

    monkeypatch.setenv("HOME", str(home))
    # Force a fresh config/scanner bound to this HOME, and restore afterwards.
    for mod in ("config", "scanner"):
        sys.modules.pop(mod, None)
    try:
        import scanner  # noqa: PLC0415 — imported under the redirected HOME

        _write_wav(music / "one.wav")
        _write_wav(music / "nested" / "two.wav")

        db = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fg.db"))
        stats = FileImporter(db, scanner_module=scanner).import_files([music])

        assert stats["new_files"] == 2, stats
        recs = db.get_all_content(limit=10)
        assert {r.format for r in recs} == {"wav"}
        for r in recs:
            assert r.sample_rate == 44100
            assert r.drive == "local"
            assert r.is_corrupted in (0, False)
            assert r.file_hash
    finally:
        for mod in ("config", "scanner"):
            sys.modules.pop(mod, None)
