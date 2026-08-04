"""
Tests for the tagger → deduper Archive edge (Stage 2 of the interconnection
contract).

Run from the repo root:
    python3 -m pytest tests/test_tagger_dedupe_edge.py -v

The contract under test:
  1. The tagger persists BPM/key analysis into fg_content and logs the run.
  2. The duplicate scanner READS fingerprints from the Archive and runs
     fpcalc only on the misses (persisted-and-reused).
  3. What the scanner computes is WRITTEN BACK so the next run starts warm.
  4. A stale row (file_size changed on disk) is recomputed, not trusted.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

import duplicate_detector

from fablegear_database.database import FableGearDatabase
from fablegear_database.schema import DatabaseConfig


@pytest.fixture
def archive(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))


def _make_file(tmp_path, name, content=b"x" * 64):
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ── 1. Tagger persists analysis ───────────────────────────────────────────────

def test_tagger_persists_bpm_key_and_logs(archive, tmp_path):
    from audio_processor import ProcessResult
    from cli import _persist_process_results

    f1 = _make_file(tmp_path, "a.mp3")
    f2 = _make_file(tmp_path, "b.mp3")
    results = [
        ProcessResult(path=f1, bpm_detected=128.0, key_detected="8A"),
        ProcessResult(path=f2, key_detected="9B"),
        ProcessResult(path=tmp_path / "untouched.mp3"),  # nothing detected → skipped
    ]

    written = _persist_process_results(results, archive)

    assert written == 2
    rec1 = archive.get_content_by_path(str(f1))
    assert rec1 is not None and rec1.bpm == 128.0 and rec1.key == "8A"
    rec2 = archive.get_content_by_path(str(f2))
    assert rec2 is not None and rec2.key == "9B"
    assert archive.count_operations("tag_tracks") == 1


def test_tagger_persist_does_not_clobber_existing_values(archive, tmp_path):
    from audio_processor import ProcessResult
    from cli import _persist_process_results

    f1 = _make_file(tmp_path, "a.mp3")
    archive.bulk_set_analysis([(str(f1), 140.0, "5A", 64)])

    # Second run detected only key — bpm=None must NOT erase the stored 140.
    _persist_process_results([ProcessResult(path=f1, key_detected="6A")], archive)

    rec = archive.get_content_by_path(str(f1))
    assert rec.bpm == 140.0
    assert rec.key == "6A"


# ── 2/3/4. Deduper reuse, write-back, staleness ──────────────────────────────

def _fp_stub(fingerprints: dict):
    """Build a _fingerprint_with_duration replacement serving canned prints."""
    calls = []

    def stub(path):
        calls.append(Path(path))
        fp = fingerprints.get(Path(path).name)
        if fp is None:
            return None
        return (120.0, fp)

    return stub, calls


def test_deduper_reuses_archive_fingerprints(archive, tmp_path, monkeypatch):
    a = _make_file(tmp_path, "a.mp3")
    b = _make_file(tmp_path, "b.mp3")
    archive.bulk_set_fingerprints([
        (str(a), "FP_SAME", 120.0, a.stat().st_size),
        (str(b), "FP_SAME", 120.0, b.stat().st_size),
    ])

    def explode(path):  # fpcalc must never run — everything is in the Archive
        raise AssertionError(f"fpcalc invoked for {path} despite archive hit")

    monkeypatch.setattr(duplicate_detector, "_fingerprint_with_duration", explode)

    result = duplicate_detector.scan_duplicates(tmp_path, match_mode="exact", archive=archive)

    assert len(result.groups) == 1
    assert {p.name for p in result.groups[0].files} == {"a.mp3", "b.mp3"}


def test_deduper_writes_back_computed_fingerprints(archive, tmp_path, monkeypatch):
    a = _make_file(tmp_path, "a.mp3")
    _make_file(tmp_path, "b.mp3")
    stub, calls = _fp_stub({"a.mp3": "FP_NEW", "b.mp3": "FP_NEW"})
    monkeypatch.setattr(duplicate_detector, "_fingerprint_with_duration", stub)

    result = duplicate_detector.scan_duplicates(tmp_path, match_mode="exact", archive=archive)

    assert len(result.groups) == 1
    assert len(calls) == 2  # both computed this run…
    rec = archive.get_content_by_path(str(a))
    assert rec is not None and rec.acoustic_fingerprint == "FP_NEW"

    # …and a second run computes nothing.
    calls.clear()
    result2 = duplicate_detector.scan_duplicates(tmp_path, match_mode="exact", archive=archive)
    assert len(result2.groups) == 1
    assert calls == []


def test_deduper_recomputes_stale_rows(archive, tmp_path, monkeypatch):
    a = _make_file(tmp_path, "a.mp3", b"original content")
    archive.bulk_set_fingerprints([(str(a), "FP_OLD", 120.0, a.stat().st_size)])

    # File changed on disk → stored file_size no longer matches.
    a.write_bytes(b"different, much longer content than before....")

    stub, calls = _fp_stub({"a.mp3": "FP_FRESH"})
    monkeypatch.setattr(duplicate_detector, "_fingerprint_with_duration", stub)

    duplicate_detector.scan_duplicates(tmp_path, match_mode="exact", archive=archive)

    assert calls == [a], "stale archive row must be recomputed"
    rec = archive.get_content_by_path(str(a))
    assert rec.acoustic_fingerprint == "FP_FRESH"


def test_deduper_without_archive_still_works(tmp_path, monkeypatch):
    _make_file(tmp_path, "a.mp3")
    _make_file(tmp_path, "b.mp3")
    stub, calls = _fp_stub({"a.mp3": "FP1", "b.mp3": "FP1"})
    monkeypatch.setattr(duplicate_detector, "_fingerprint_with_duration", stub)

    result = duplicate_detector.scan_duplicates(tmp_path, match_mode="exact", archive=None)

    assert len(result.groups) == 1
    assert len(calls) == 2
