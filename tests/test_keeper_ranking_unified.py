"""
Regression test for audit M1: the keeper a human reviews in the duplicate
report must be the keeper the pruner actually retains.

Before the fix the detector ranked groups by RARP only (PN > MIK > RAW,
_rank_file), while the pruner re-ranked by quality_score (format tier, size,
RARP, tags) and silently ignored the CSV's KEEP column. So a group of
[01 - track.mp3 (PN), track.aiff (lossless)] showed "keep the mp3" in the
report the human approved, but the pruner kept the aiff and deleted the mp3.

Both now rank by the single shared key pruner.dedupe_sort_key, so the report
and the execution agree.

Run from the repo root:
    python3 -m pytest tests/test_keeper_ranking_unified.py -v
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

from duplicate_detector import _RANK_LABELS, _rank_file, _rank_group
from pruner import dedupe_sort_key


def _pruner_keeper(paths):
    """What the pruner would keep: highest dedupe_sort_key wins."""
    return max(paths, key=lambda p: dedupe_sort_key(p, _RANK_LABELS[_rank_file(p)]))


def test_detector_keeper_matches_pruner_keeper(tmp_path):
    # A Pioneer-numbered mp3 (RARP=PN, the old detector's top pick) vs a
    # lossless aiff (higher format tier, the pruner's pick).
    pn_mp3 = tmp_path / "01 - track.mp3"
    aiff = tmp_path / "track.aiff"
    pn_mp3.write_bytes(b"m" * 5000)
    aiff.write_bytes(b"a" * 5000)

    paths = [pn_mp3, aiff]
    detector_keeper = _rank_group(paths)[0]
    pruner_keeper = _pruner_keeper(paths)

    assert detector_keeper == pruner_keeper
    # And concretely: the lossless file is the one kept, not the PN mp3.
    assert detector_keeper == aiff


def test_agreement_holds_across_orderings(tmp_path):
    # Order-independence: the recommended keeper must not depend on input order.
    a = tmp_path / "01 - song.mp3"
    b = tmp_path / "song.flac"
    c = tmp_path / "song.wav"
    for p, n in ((a, 4000), (b, 8000), (c, 9000)):
        p.write_bytes(b"x" * n)

    import itertools
    keepers = {_rank_group(list(perm))[0] for perm in itertools.permutations([a, b, c])}
    assert len(keepers) == 1, "keeper depends on input order — ranking not total"
    assert keepers.pop() == _pruner_keeper([a, b, c])
