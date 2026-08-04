"""
Regression test for audit M2: fuzzy fingerprint matching must not treat a
short acoustic prefix as a duplicate of a much longer track.

_hamming_similarity compared only the overlapping prefix and normalized by
that prefix length, so a 30-second clip whose fingerprint matched the opening
of a 6-minute track scored ~1.0 and got union-merged into the long track's
duplicate group. Chromaprint length is proportional to duration, so a large
length mismatch means different-duration tracks — now rejected outright.

Run from the repo root:
    python3 -m pytest tests/test_fuzzy_length_guard.py -v
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

from duplicate_detector import _FP_LENGTH_RATIO_MIN, _hamming_similarity


def test_short_prefix_of_long_track_is_not_a_match():
    # `short` is a byte-perfect prefix of `long` — identical where they overlap.
    long = list(range(1000))
    short = list(range(200))  # 20% of the length — a prefix, but a different track
    assert _hamming_similarity(short, long) == 0.0
    assert _hamming_similarity(long, short) == 0.0  # order-independent


def test_close_length_identical_content_scores_high():
    a = list(range(1000))
    b = list(range(1000))
    assert _hamming_similarity(a, b) == 1.0


def test_length_within_ratio_still_compared():
    # 95% length ratio (above the 0.90 floor) with identical overlap → still matches.
    longer = list(range(1000))
    shorter = list(range(950))
    assert 950 / 1000 >= _FP_LENGTH_RATIO_MIN
    assert _hamming_similarity(shorter, longer) == 1.0


def test_length_just_below_ratio_rejected():
    longer = list(range(1000))
    shorter = list(range(int(1000 * _FP_LENGTH_RATIO_MIN) - 1))
    assert _hamming_similarity(shorter, longer) == 0.0
