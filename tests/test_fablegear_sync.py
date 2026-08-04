"""
Tests for database/filesystem reconciliation (``fablegear_database.sync``).

Run from the repo root:
    pip install pytest && python3 -m pytest tests/test_fablegear_sync.py -v

Uses an injected fake scanner over real temp files (so existence + content
hashing are real) — no app config required. The headline case is move
detection: a file that moves on disk must keep its database row, id, and
attached fingerprint rather than becoming a dead record plus a fresh one.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import FableGearDatabase
from fablegear_database.importer import FileImporter
from fablegear_database.schema import DatabaseConfig
from fablegear_database.sync import DatabaseSync


@dataclass
class FakeTrack:
    path: Path
    title: str | None = None
    file_size: int | None = None
    file_type: str | None = None
    errors: list[str] = field(default_factory=list)


class FakeScanner:
    """Yields tracks under root whose file currently exists (mirrors real walk)."""

    def __init__(self, tracks):
        self.tracks = list(tracks)

    def scan_directory(self, root):
        root = Path(root)
        for t in self.tracks:
            p = Path(t.path)
            if not p.exists():
                continue
            try:
                p.relative_to(root)
            except ValueError:
                continue
            yield t

    def count_scannable_files(self, root):
        return sum(1 for _ in self.scan_directory(root))


@pytest.fixture
def db(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))


def _mk(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _sync(db, scanner):
    return DatabaseSync(db, importer=FileImporter(db, scanner_module=scanner))


def test_reconcile_imports_new_files(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    b = _mk(music / "b.mp3", b"bbbb")
    sync = _sync(db, FakeScanner([FakeTrack(a, title="A"), FakeTrack(b, title="B")]))

    stats = sync.reconcile([music])
    assert stats["imported_new"] == 2
    assert stats["missing"] == 0
    assert db.get_statistics()["total_tracks"] == 2


def test_reconcile_flags_missing_files(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    b = _mk(music / "b.mp3", b"bbbb")
    scanner = FakeScanner([FakeTrack(a), FakeTrack(b)])
    sync = _sync(db, scanner)
    sync.reconcile([music])

    a.unlink()  # a.mp3 disappears
    stats = sync.reconcile([music])

    assert stats["missing"] == 1
    assert stats["removed"] == 0
    assert db.get_statistics()["total_tracks"] == 2  # still present, just flagged
    assert db.get_content_by_path(str(a)).processing_status == "missing"


def test_reconcile_remove_missing_deletes_record(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    scanner = FakeScanner([FakeTrack(a)])
    sync = _sync(db, scanner)
    sync.reconcile([music])

    a.unlink()
    stats = sync.reconcile([music], remove_missing=True)

    assert stats["removed"] == 1
    assert db.get_statistics()["total_tracks"] == 0


def test_reconcile_detects_move_and_preserves_record(db, tmp_path):
    """A moved file keeps its row, id, and fingerprint — the core guarantee."""
    music = tmp_path / "music"
    old = _mk(music / "a.mp3", b"identical-content")
    scanner = FakeScanner([FakeTrack(old, title="A")])
    importer = FileImporter(db, scanner_module=scanner)
    sync = DatabaseSync(db, importer=importer)

    sync.reconcile([music])
    rec = db.get_content_by_path(str(old))
    original_id = rec.id
    importer.update_fingerprint(old, "FP-PRESERVE-ME", quality=99)

    # Move the file: same bytes, new path.
    new = music / "sub" / "a.mp3"
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    scanner.tracks = [FakeTrack(new, title="A")]

    stats = sync.reconcile([music])

    assert stats["moved"] == 1
    assert stats["missing"] == 0
    assert stats["moves"][0]["from"] == str(old)
    assert stats["moves"][0]["to"] == str(new)

    assert db.get_statistics()["total_tracks"] == 1          # not duplicated
    assert db.get_content_by_path(str(old)) is None
    moved = db.get_content_by_path(str(new))
    assert moved.id == original_id                            # same row survived
    assert moved.acoustic_fingerprint == "FP-PRESERVE-ME"     # association preserved


def test_find_orphaned_files(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    b = _mk(music / "b.mp3", b"bbbb")
    scanner = FakeScanner([FakeTrack(a), FakeTrack(b)])
    sync = _sync(db, scanner)

    # Import only a.mp3 by hiding b from a first pass.
    scanner.tracks = [FakeTrack(a)]
    sync.reconcile([music])
    scanner.tracks = [FakeTrack(a), FakeTrack(b)]

    orphans = sync.find_orphaned_files([music])
    assert orphans == [b]


def test_cleanup_stale_records(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    scanner = FakeScanner([FakeTrack(a)])
    sync = _sync(db, scanner)
    sync.reconcile([music])

    a.unlink()
    assert sync.cleanup_stale_records(dry_run=True) == 1
    assert db.get_statistics()["total_tracks"] == 1   # dry run changed nothing
    assert sync.cleanup_stale_records() == 1
    assert db.get_statistics()["total_tracks"] == 0


# --------------------------------------------------------------------------- #
# Persistence contract: every sync action is logged
# --------------------------------------------------------------------------- #

def test_reconcile_logs_to_processing_log(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    b = _mk(music / "b.mp3", b"bbbb")
    scanner = FakeScanner([FakeTrack(a, title="A"), FakeTrack(b, title="B")])
    sync = _sync(db, scanner)
    sync.reconcile([music])

    assert db.count_operations("import") >= 1
    assert db.count_operations("sync_reconcile") == 1


def test_reconcile_logs_moves(db, tmp_path):
    music = tmp_path / "music"
    old = _mk(music / "a.mp3", b"same-content")
    scanner = FakeScanner([FakeTrack(old, title="A")])
    sync = _sync(db, scanner)
    sync.reconcile([music])

    new = music / "sub" / "a.mp3"
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    scanner.tracks = [FakeTrack(new, title="A")]

    sync.reconcile([music])
    assert db.count_operations("sync_move") == 1


def test_reconcile_logs_missing_and_removed(db, tmp_path):
    music = tmp_path / "music"
    a = _mk(music / "a.mp3", b"aaaa")
    scanner = FakeScanner([FakeTrack(a)])
    sync = _sync(db, scanner)
    sync.reconcile([music])

    a.unlink()
    sync.reconcile([music])
    assert db.count_operations("sync_missing") == 1

    # Re-add so we can test remove path
    a = _mk(music / "b.mp3", b"bbbb")
    scanner.tracks = [FakeTrack(a)]
    sync.reconcile([music])
    a.unlink()
    sync.reconcile([music], remove_missing=True)
    assert db.count_operations("sync_remove") >= 1
