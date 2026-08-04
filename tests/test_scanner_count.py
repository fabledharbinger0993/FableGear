"""
Tests for scanner.count_scannable_files().

fablegear_database.importer.import_files() uses this for a cheap pre-count —
so callers get an accurate total from the very first progress tick instead of
finding out only once the whole (expensive, tag-extracting) scan has
finished. It must therefore agree exactly with scan_directory()'s own skip
rules (dir-pruning, extension, minimum size) without doing the expensive
metadata extraction scan_directory does per file.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scanner import count_scannable_files, scan_directory


def _write(path: Path, content: bytes = b"0123456789") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_count_matches_scan_directory_on_a_plain_mix(tmp_path):
    _write(tmp_path / "a.mp3")
    _write(tmp_path / "sub" / "b.flac")
    _write(tmp_path / "notes.txt")          # wrong extension
    _write(tmp_path / ".DS_Store")          # skip prefix

    assert count_scannable_files(tmp_path) == sum(1 for _ in scan_directory(tmp_path))
    assert count_scannable_files(tmp_path) == 2


def test_count_prunes_skip_dirs(tmp_path):
    _write(tmp_path / "keep.mp3")
    _write(tmp_path / "node_modules" / "junk.mp3")
    _write(tmp_path / "PIONEER" / "sidecar.mp3")

    assert count_scannable_files(tmp_path) == 1


def test_count_skips_undersized_files(tmp_path):
    _write(tmp_path / "real.mp3", b"0123456789")
    _write(tmp_path / "tiny.mp3", b"")       # under MIN_FILE_BYTES

    assert count_scannable_files(tmp_path) == 1


def test_count_raises_on_non_directory(tmp_path):
    f = _write(tmp_path / "a.mp3")
    with pytest.raises(ValueError):
        count_scannable_files(f)
