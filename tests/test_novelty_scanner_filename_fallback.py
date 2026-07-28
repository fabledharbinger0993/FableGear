"""
Tests for the novelty scanner's filename-based fingerprint-candidate
fallback in chop_shop/novelty_scanner.py.

Regression coverage for a bug found in live use: with no
~/rekordbox-toolkit/scan_index.json on disk (a fresh install, or files
never run through Tag Tracks), every source and destination track has
bpm=key=duration=None. _dest_candidates() then treats every comparison as
"no metadata on either side" and skips it entirely — so Phase 1 finds zero
candidates for every track, Phase 2 (fingerprinting) never runs, and every
source track gets copied as "novel", including literal drive-to-drive
copies of files already sitting in the destination.

The fix folds a normalized-filename match into the candidate set
regardless of metadata state, so a same-named destination file still gets
a real fingerprint check instead of being skipped straight to "novel".

These tests run with scan_index.json forced absent (the real-world trigger
condition) and mock duplicate_detector.fingerprint_file so they don't
depend on real audio content or fpcalc being installed.
"""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CHOP_SHOP = REPO_ROOT / "chop_shop"
if str(CHOP_SHOP) not in sys.path:
    sys.path.insert(0, str(CHOP_SHOP))

import novelty_scanner  # noqa: E402
from novelty_scanner import scan_novel  # noqa: E402


def _write(path: Path, content: bytes = b"not-real-audio-but-thats-fine") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _copied_names(root: Path) -> set[str]:
    return {p.name for p in root.rglob("*") if p.is_file()}


def _no_scan_index(monkeypatch):
    """Force _load_scan_index() to return {} — the real-world trigger
    condition (scan_index.json absent) — regardless of what's on the
    machine actually running the test."""
    monkeypatch.setattr(novelty_scanner, "_load_scan_index", lambda: {})


def test_filename_fallback_lets_a_literal_copy_be_fingerprint_confirmed(monkeypatch, tmp_path):
    """The actual fix: no scan_index.json, so BPM/key/duration are unknown
    on both sides — but the destination has a same-named file. The
    fingerprint check must still run and, when it agrees, the track is
    recognized as already present instead of being re-copied."""
    _no_scan_index(monkeypatch)
    source = tmp_path / "source"
    home_library = tmp_path / "home_library"
    _write(source / "Bob Marley - Jamming.mp3")
    _write(home_library / "Reggae" / "Bob Marley - Jamming.mp3")

    with patch("duplicate_detector.fingerprint_file", return_value="identical-fp"):
        result = scan_novel([source], home_library, dry_run=False, match_mode="fingerprint")

    assert len(result.present) == 1
    assert len(result.novel) == 0
    assert not any(home_library.rglob("*_1.mp3"))  # nothing re-copied in


def test_genuinely_different_filename_still_has_no_candidates_and_skips_fingerprinting(monkeypatch, tmp_path):
    """No scan_index and no filename match either -> still the fast "no
    candidates" path, same as before the fix. fingerprint_file must never
    be called — this is the exact case the "prohibitively slow" comment
    in _dest_candidates protects."""
    _no_scan_index(monkeypatch)
    source = tmp_path / "source"
    home_library = tmp_path / "home_library"
    _write(source / "Totally Unique Track.mp3")
    _write(home_library / "Reggae" / "Bob Marley - Jamming.mp3")

    with patch("duplicate_detector.fingerprint_file") as mock_fp:
        result = scan_novel([source], home_library, dry_run=False, match_mode="fingerprint")
        mock_fp.assert_not_called()

    assert len(result.novel) == 1
    assert "Totally Unique Track.mp3" in _copied_names(home_library)


def test_filename_match_but_fingerprint_disagrees_is_still_copied(monkeypatch, tmp_path):
    """Same filename, different audio content (e.g. a re-rip, or an
    unrelated track someone happened to name the same) -> fingerprint
    confirmation must still be the deciding factor, not the filename alone."""
    _no_scan_index(monkeypatch)
    source = tmp_path / "source"
    home_library = tmp_path / "home_library"
    _write(source / "Untitled.mp3")
    _write(home_library / "Untitled.mp3")

    with patch("duplicate_detector.fingerprint_file", side_effect=["fp-a", "fp-totally-different"]):
        result = scan_novel([source], home_library, dry_run=False, match_mode="fingerprint")

    assert len(result.novel) == 1
    assert len(result.present) == 0
