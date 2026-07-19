"""
Tests for chop_shop.novelty_scanner's copy_to / destination split.

Regression coverage for the bug found in live use: scan_novel() used a
single `destination` argument for both (a) the comparison reference
("is this track already owned?") and (b) the copy target. Pointing that
one argument at a fresh, empty folder — intending it purely as a copy
target — made *everything* register as novel, since there was nothing to
compare against. copy_to decouples the two: destination stays the
comparison reference, copy_to (optional) is where confirmed-novel tracks
actually land.

Uses match_mode="filename" throughout — it only needs a normalized
filename match, so these tests don't depend on real audio content or
Chromaprint.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CHOP_SHOP = REPO_ROOT / "chop_shop"
if str(CHOP_SHOP) not in sys.path:
    sys.path.insert(0, str(CHOP_SHOP))

from novelty_scanner import scan_novel  # noqa: E402


def _write(path: Path, content: bytes = b"not-real-audio-but-thats-fine") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _copied_names(root: Path) -> set[str]:
    return {p.name for p in root.rglob("*") if p.is_file()}


def test_copy_to_defaults_to_destination_when_omitted(tmp_path):
    """Old single-folder behavior: copy_to unset -> files land in destination."""
    source = tmp_path / "source"
    destination = tmp_path / "home_library"
    _write(source / "Track A.mp3")

    result = scan_novel(
        [source], destination,
        dry_run=False, match_mode="filename",
    )

    assert len(result.novel) == 1
    assert "Track A.mp3" in _copied_names(destination)


def test_copy_to_separates_copy_target_from_comparison_reference(tmp_path):
    """The bug scenario, fixed: compare against the real library, but copy
    novel finds into a separate, freshly-created holding folder."""
    source = tmp_path / "source"
    home_library = tmp_path / "home_library"
    holding = tmp_path / "new_finds"   # does not exist yet

    # Already owned — filename match in the real library.
    _write(source / "Bob Marley - Jamming.mp3")
    _write(home_library / "Reggae" / "Bob Marley - Jamming.mp3")

    # Genuinely new.
    _write(source / "Unknown Artist - Rare Dub.mp3")

    result = scan_novel(
        [source], home_library,
        copy_to=holding,
        dry_run=False, match_mode="filename",
    )

    assert len(result.present) == 1   # Jamming recognized as already owned
    assert len(result.novel) == 1     # only the rare one counted as novel

    copied = _copied_names(holding)
    assert "Unknown Artist - Rare Dub.mp3" in copied
    assert "Bob Marley - Jamming.mp3" not in copied

    # Nothing was written into the compared-against library itself.
    assert not any(home_library.rglob("Rare Dub*"))


def test_pointing_destination_at_an_empty_folder_reproduces_the_original_bug(tmp_path):
    """Documents *why* the fix matters: comparing against an empty folder
    makes even already-owned tracks look novel. copy_to doesn't change this
    — it's still the caller's job to pass the real library as destination."""
    source = tmp_path / "source"
    empty_test_folder = tmp_path / "Novelty test"   # never populated
    _write(source / "Bob Marley - Jamming.mp3")

    result = scan_novel(
        [source], empty_test_folder,
        dry_run=False, match_mode="filename",
    )

    # Nothing to compare against -> even a mainstream, presumably-owned
    # track gets flagged novel. Not a bug in this call — a reminder that
    # destination must be the real library for the comparison to mean
    # anything.
    assert len(result.novel) == 1
    assert len(result.present) == 0
