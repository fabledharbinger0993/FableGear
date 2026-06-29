"""
Tests for the fingerprint edge of the Archive (fablegear_database.fingerprinter).

Run from the repo root:
    pip install pytest && python3 -m pytest tests/test_fablegear_fingerprint.py -v

This pins the persistence contract for the first producer->consumer edge:
fingerprints are computed once, persisted into fg_content, logged in
fg_processing_log, never recomputed (idempotent + resumable), and read back by
the duplicate scanner instead of recomputed. The fingerprint function is
injected so no fpcalc/audio is needed — we count its calls to prove
"compute only the misses".
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.fingerprinter import LibraryFingerprinter
from fablegear_database.schema import DatabaseConfig


@pytest.fixture
def db(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))


def _add(db, tmp_path, name, content=b"audio"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    db.insert_content(ContentRecord(
        file_path=str(p), file_name=p.name, file_size=p.stat().st_size, title=name,
    ))
    return p


class CountingFP:
    """A fake fingerprint function that records which paths it was asked for."""
    def __init__(self, mapping=None):
        self.calls = []
        self.mapping = mapping or {}

    def __call__(self, path):
        self.calls.append(str(path))
        return self.mapping.get(str(path), f"FP::{Path(path).name}")


# --------------------------------------------------------------------------- #
# Persisted + reused + idempotent
# --------------------------------------------------------------------------- #

def test_fingerprints_are_persisted_and_logged(db, tmp_path):
    a = _add(db, tmp_path, "a.mp3")
    b = _add(db, tmp_path, "b.mp3")
    fp = CountingFP()
    fpr = LibraryFingerprinter(db, fingerprint_fn=fp)

    stats = fpr.fingerprint_missing()
    assert stats == {"missing": 2, "fingerprinted": 2, "failed": 0, "vanished": 0}

    # persisted into fg_content
    assert db.get_content_by_path(str(a)).acoustic_fingerprint == "FP::a.mp3"
    assert db.get_content_by_path(str(b)).processing_status == "fingerprinted"
    # logged into the Archive ledger
    assert db.count_operations("fingerprint") == 2
    # reflected in the stats Record Room shows
    assert db.get_statistics()["fingerprinted"] == 2


def test_rerun_computes_only_the_misses(db, tmp_path):
    _add(db, tmp_path, "a.mp3")
    _add(db, tmp_path, "b.mp3")
    fp = CountingFP()
    fpr = LibraryFingerprinter(db, fingerprint_fn=fp)

    fpr.fingerprint_missing()
    assert len(fp.calls) == 2

    # add a third file; a second pass must only fingerprint the new one
    _add(db, tmp_path, "c.mp3")
    fp.calls.clear()
    stats = fpr.fingerprint_missing()
    assert stats["missing"] == 1
    assert fp.calls == [str(tmp_path / "c.mp3")]      # only the miss recomputed
    assert db.get_statistics()["fingerprinted"] == 3


def test_resumable_after_interrupt(db, tmp_path):
    for n in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
        _add(db, tmp_path, n)

    # A fingerprint fn that dies on the 3rd file, simulating an interrupted run.
    class Boom(CountingFP):
        def __call__(self, path):
            if len(self.calls) == 2:
                self.calls.append(str(path))
                raise KeyboardInterrupt("interrupted")
            return super().__call__(path)

    boom = Boom()
    fpr = LibraryFingerprinter(db, fingerprint_fn=boom)
    with pytest.raises(KeyboardInterrupt):
        fpr.fingerprint_missing()

    # two were persisted before the crash
    assert db.get_statistics()["fingerprinted"] == 2

    # resume with a healthy fn — only the remaining two get computed
    good = CountingFP()
    LibraryFingerprinter(db, fingerprint_fn=good).fingerprint_missing()
    assert len(good.calls) == 2
    assert db.get_statistics()["fingerprinted"] == 4
    assert db.get_unfingerprinted() == []


def test_failed_fingerprint_is_logged_not_persisted(db, tmp_path):
    a = _add(db, tmp_path, "a.mp3")
    fp = CountingFP(mapping={str(a): None})  # fn returns None = failure
    stats = LibraryFingerprinter(db, fingerprint_fn=fp).fingerprint_missing()

    assert stats["failed"] == 1
    assert db.get_content_by_path(str(a)).acoustic_fingerprint is None
    assert db.count_operations("fingerprint") == 1   # failure still logged


def test_vanished_file_is_logged(db, tmp_path):
    a = _add(db, tmp_path, "a.mp3")
    a.unlink()  # file gone before fingerprinting
    stats = LibraryFingerprinter(db, fingerprint_fn=CountingFP()).fingerprint_missing()
    assert stats["vanished"] == 1
    assert stats["fingerprinted"] == 0


# --------------------------------------------------------------------------- #
# Deduper reads from the Archive (compute only misses, then group)
# --------------------------------------------------------------------------- #

def test_duplicate_groups_read_from_archive(db, tmp_path):
    # a + b share content (same fingerprint); c is unique
    a = _add(db, tmp_path, "a.mp3", content=b"same")
    b = _add(db, tmp_path, "b.mp3", content=b"same")
    _add(db, tmp_path, "c.mp3", content=b"unique")
    shared = {str(a): "DUP", str(b): "DUP"}
    fp = CountingFP(mapping=shared)
    fpr = LibraryFingerprinter(db, fingerprint_fn=fp)

    groups = fpr.duplicate_groups()
    fp_groups = groups["by_fingerprint"]
    assert len(fp_groups) == 1
    assert sorted(fp_groups[0]) == [1, 2]   # a and b grouped by shared fingerprint

    # second call: fingerprints already in the Archive -> nothing recomputed
    fp.calls.clear()
    again = fpr.duplicate_groups()
    assert fp.calls == []
    assert again["by_fingerprint"] == fp_groups
