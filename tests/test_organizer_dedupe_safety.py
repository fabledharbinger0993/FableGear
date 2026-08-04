"""
Regression tests for the critical assimilate-mode duplicate-removal path.

Context (audit C1): organize_library in assimilate mode used to declare a
source file a duplicate of whatever already sat at its canonical destination
based on FILE SIZE ALONE, then permanently unlink() the source. Two unrelated
tracks that coincidentally share a byte count → one silently, unrecoverably
deleted. No recovery path anywhere covered it.

The shipped fix, pinned here:
  1. A duplicate verdict requires byte-identical CONTENT (SHA256, with a
     size pre-filter only to skip the hash when sizes already differ) — never
     a size match alone.
  2. A confirmed duplicate's source is MOVED into a per-run Trash folder
     (~/.Trash/FableGear_OrgDupes_<timestamp>), never hard-deleted, and the
     move destination is recorded on MoveResult.trash_path.

No test previously existed for this path in either implementation — this
file covers it directly against the shipped API (_resolve_dest,
_sha256_file, organize_library, MoveResult.trash_path).

Run from the repo root:
    python3 -m pytest tests/test_organizer_dedupe_safety.py -v
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import config and scanner NOW, under the real (configured) home, so their
# module-level config load is cached before any test monkeypatches Path.home.
# Otherwise the e2e tests' home redirect makes config re-read from an empty
# sandbox and raise NotConfiguredError.
import config  # noqa: F401
import scanner  # noqa: F401
from chop_shop.library_organizer import (
    _resolve_dest,
    _sha256_file,
    organize_library,
)

# ── _sha256_file: the proof-of-identity primitive ────────────────────────────

def test_same_size_different_content_hashes_differ(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"A" * 4096)
    b.write_bytes(b"B" * 4096)  # identical size, different bytes
    assert a.stat().st_size == b.stat().st_size
    assert _sha256_file(a) != _sha256_file(b)


def test_byte_identical_hashes_match(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    payload = b"the same bytes" * 300
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert _sha256_file(a) == _sha256_file(b)


# ── _resolve_dest: the delete-or-rename decision ─────────────────────────────

def test_resolve_dest_same_size_different_content_never_skips(tmp_path):
    """A same-size, different-content collision must resolve to a numbered
    rename ('conflict_renamed'), NOT 'skipped' — 'skipped' is the verdict
    assimilate mode acts on by removing the source."""
    src = tmp_path / "src" / "track.mp3"
    src.parent.mkdir()
    src.write_bytes(b"X" * 2048)

    dest = tmp_path / "dst" / "track.mp3"
    dest.parent.mkdir()
    dest.write_bytes(b"Y" * 2048)  # same size, different content

    final, action = _resolve_dest(src, dest)
    assert action == "conflict_renamed"
    assert final is not None and final != dest


def test_resolve_dest_identical_content_skips(tmp_path):
    src = tmp_path / "src" / "track.mp3"
    src.parent.mkdir()
    payload = b"identical" * 200
    src.write_bytes(payload)

    dest = tmp_path / "dst" / "track.mp3"
    dest.parent.mkdir()
    dest.write_bytes(payload)

    final, action = _resolve_dest(src, dest)
    assert action == "skipped"
    assert final is None


# ── End-to-end: assimilate never destroys a non-duplicate; dups are recoverable

def test_assimilate_never_deletes_a_same_size_nonduplicate(tmp_path, monkeypatch):
    # Redirect ~/.Trash into the tmp sandbox so the recovery folder is hermetic.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home").mkdir()

    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "incoming"
    source.mkdir()

    # No artist tags → routes to Orphaned Tracks/<year>/<name>.
    victim = source / "song.mp3"
    victim.write_bytes(b"ORIGINAL CONTENT----" * 100)

    organize_library(source, target, mode="assimilate", dry_run=False)
    moved_copies = list(target.rglob("song.mp3"))
    assert len(moved_copies) == 1, "first organize should place the file once"
    canonical = moved_copies[0]

    # A DIFFERENT file with the SAME size lands at the same canonical name.
    same_size_different = source / "song.mp3"
    payload = b"DIFFERENT CONTENT---" * 100  # same length as the original
    same_size_different.write_bytes(payload)
    assert same_size_different.stat().st_size == canonical.stat().st_size

    organize_library(source, target, mode="assimilate", dry_run=False)

    # The different-content file must still exist somewhere — never deleted.
    survivors = [p for p in target.rglob("song*.mp3") if p.read_bytes() == payload]
    assert survivors, "same-size, different-content file was destroyed — regression!"


def test_assimilate_moves_true_duplicate_to_recoverable_trash(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    target = tmp_path / "library"
    target.mkdir()
    source = tmp_path / "incoming"
    source.mkdir()

    original = source / "song.mp3"
    payload = b"REAL DUPLICATE BYTES" * 100
    original.write_bytes(payload)
    organize_library(source, target, mode="assimilate", dry_run=False)
    canonical = next(target.rglob("song.mp3"))

    # An exact byte-for-byte duplicate arrives at the source again.
    dup = source / "song.mp3"
    dup.write_bytes(payload)

    results = organize_library(source, target, mode="assimilate", dry_run=False)

    # Source duplicate is gone from the source tree...
    assert not dup.exists()
    # ...but preserved in a restorable recovery folder, not destroyed, and the
    # result records exactly where it went.
    trashed = [r for r in results if r.trash_path is not None]
    assert len(trashed) == 1
    assert trashed[0].trash_path.exists()
    assert trashed[0].trash_path.read_bytes() == payload
    assert trashed[0].trash_path.parent.name.startswith("FableGear_OrgDupes_")
    assert trashed[0].trash_path.parent.parent == home / ".Trash"
    # The canonical copy in the library is untouched.
    assert canonical.read_bytes() == payload
