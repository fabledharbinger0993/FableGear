"""
Regression tests for two Chop Shop / Organize behaviours:

1. Pathological destination file names (over the filesystem's per-component byte
   limit, often because the name is its own data copied several times) must be
   *repaired* — collapsed and/or truncated — not passed through to a move that
   fails with ENAMETOOLONG.

2. Source folders left holding only OS-metadata junk (.DS_Store, AppleDouble
   ._* files, Thumbs.db, …) must be pruned in assimilate mode; folders that
   still hold real files must be left alone.

Run from the repo root:
    python3 -m pytest tests/test_library_organizer_cleanup.py -v
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from chop_shop.library_organizer import (  # noqa: E402
    _MAX_NAME_BYTES,
    _prune_empty_dirs,
    _sanitize_filename,
)


# ── _sanitize_filename ───────────────────────────────────────────────────────

def test_normal_name_unchanged():
    assert _sanitize_filename("Daft Punk - Around the World.mp3") == \
        "Daft Punk - Around the World.mp3"


def test_legit_repeated_title_within_limit_unchanged():
    # Must NOT collapse a real title that happens to repeat — it's under the cap.
    assert _sanitize_filename("New York New York.flac") == "New York New York.flac"


def test_pathological_repeated_name_is_collapsed():
    unit = "Some Track Title "
    bloated = (unit * 40).strip() + ".aiff"          # far over the limit
    out = _sanitize_filename(bloated)
    assert out.endswith(".aiff")
    assert len(out.encode("utf-8")) <= _MAX_NAME_BYTES
    assert out == "Some Track Title.aiff"            # collapsed to one copy


def test_overlong_nonrepeating_name_is_truncated():
    stem = "".join(chr(97 + (i % 26)) for i in range(500))  # 500 non-repeating chars
    out = _sanitize_filename(stem + ".wav")
    assert out.endswith(".wav")
    assert len(out.encode("utf-8")) <= _MAX_NAME_BYTES


def test_extension_preserved_and_bytes_capped_for_unicode():
    out = _sanitize_filename(("é" * 300) + ".mp3")         # multibyte chars
    assert out.endswith(".mp3")
    assert len(out.encode("utf-8")) <= _MAX_NAME_BYTES     # byte-aware, not char
    # truncation lands on a char boundary (decodable, no replacement chars)
    out.encode("utf-8").decode("utf-8")


def test_unsafe_characters_stripped():
    assert "/" not in _sanitize_filename("AC/DC - Thunder.mp3")


# ── _prune_empty_dirs ────────────────────────────────────────────────────────

def test_folder_with_only_ds_store_is_pruned(tmp_path):
    d = tmp_path / "Old Album"
    d.mkdir()
    (d / ".DS_Store").write_bytes(b"\x00")
    _prune_empty_dirs(tmp_path)
    assert not d.exists()


def test_folder_with_appledouble_junk_is_pruned(tmp_path):
    d = tmp_path / "Mixes"
    d.mkdir()
    (d / "._leftover").write_bytes(b"\x00")
    (d / "Thumbs.db").write_bytes(b"\x00")
    _prune_empty_dirs(tmp_path)
    assert not d.exists()


def test_folder_with_real_file_is_kept(tmp_path):
    d = tmp_path / "Has Art"
    d.mkdir()
    (d / ".DS_Store").write_bytes(b"\x00")
    (d / "cover.jpg").write_bytes(b"img")
    _prune_empty_dirs(tmp_path)
    assert d.exists()
    assert (d / "cover.jpg").exists()


def test_nested_empty_dirs_pruned_bottom_up(tmp_path):
    deep = tmp_path / "Artist" / "Album"
    deep.mkdir(parents=True)
    (deep / ".DS_Store").write_bytes(b"\x00")
    _prune_empty_dirs(tmp_path)
    assert not (tmp_path / "Artist").exists()   # both levels pruned


def test_root_itself_never_removed(tmp_path):
    (tmp_path / ".DS_Store").write_bytes(b"\x00")
    _prune_empty_dirs(tmp_path)
    assert tmp_path.exists()
