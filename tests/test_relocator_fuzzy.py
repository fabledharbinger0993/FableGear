"""
Tests for relocator.build_fuzzy_index's stem-collision tie-break (audit F-06).

Context: build_fuzzy_index maps a lowercase filename stem to a single Path.
Two files that share a stem (e.g. track.mp3 and track.aiff — the common
shape after a format-conversion batch) collide on that key. The prior
implementation kept "whichever file os.walk() visited last," which is
filesystem-order-dependent and can flip between runs on the same directory
tree with no content change at all — a relocate run could point a DB row at
the .mp3 today and the .aiff tomorrow.

These tests pin the fix: collisions must resolve deterministically by
format-quality tier (chop_shop.pruner.FORMAT_TIER — higher tier wins), and
same-tier collisions must resolve by path sort order, regardless of the
order files are passed in.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

from relocator import build_fuzzy_index
from pruner import FORMAT_TIER

# Sanity-check the assumption the tests below rely on, rather than hardcoding
# "aiff beats mp3" without verifying it against the real constant.
assert FORMAT_TIER[".aiff"] > FORMAT_TIER[".mp3"], (
    "Test assumes .aiff outranks .mp3 in FORMAT_TIER — constant changed?"
)


@pytest.fixture
def track_pair(tmp_path):
    """Two files sharing the stem 'track', differing only in format."""
    mp3 = tmp_path / "track.mp3"
    aiff = tmp_path / "track.aiff"
    mp3.write_bytes(b"mp3-bytes")
    aiff.write_bytes(b"aiff-bytes")
    return mp3, aiff


def test_higher_tier_format_wins_collision(track_pair):
    mp3, aiff = track_pair
    index = build_fuzzy_index([mp3, aiff])
    assert index["track"] == aiff


def test_collision_winner_is_order_independent(track_pair):
    mp3, aiff = track_pair
    forward = build_fuzzy_index([mp3, aiff])
    reversed_ = build_fuzzy_index([aiff, mp3])
    assert forward["track"] == reversed_["track"] == aiff


def test_same_stem_same_format_collision_is_deterministic_by_path(tmp_path):
    """
    True duplicates (same stem, same format) have no quality signal to break
    the tie on, so the winner must be picked by a stable, order-independent
    rule (path sort) rather than "whichever came first/last in the list."
    """
    sub1 = tmp_path / "dir_b"
    sub2 = tmp_path / "dir_a"
    sub1.mkdir()
    sub2.mkdir()
    f1 = sub1 / "track.mp3"
    f2 = sub2 / "track.mp3"
    f1.write_bytes(b"one")
    f2.write_bytes(b"two")

    expected_winner = min([f1, f2], key=lambda p: str(p))

    forward = build_fuzzy_index([f1, f2])
    backward = build_fuzzy_index([f2, f1])

    assert forward["track"] == expected_winner
    assert backward["track"] == expected_winner
    assert forward["track"] == backward["track"]
