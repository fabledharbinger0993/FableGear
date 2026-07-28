"""
Tests for the push-recovery hygiene breakdown (PushReport).

The push filters recovered crates for four distinct reasons — auto-generated
junk crates, crates below the min-tracks threshold, crates with no track in the
live collection, and names already present. These used to be invisible (the
junk skip had no counter) or conflated (too-small lumped in with no-match).
Pin that each reason is counted separately and surfaced.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# playlist_recovery keeps pyrekordbox imports lazy (inside functions), so the
# module imports cleanly without the heavy DB deps.
import playlist_recovery as P  # noqa: E402


def test_crates_filtered_sums_all_reasons():
    r = P.PushReport(total_crates=100, crates_planned=40, links_planned=900,
                     skipped_existing=20, crates_no_match=10,
                     crates_too_small=25, crates_junk_filtered=5)
    assert r.crates_filtered == 60


def test_hygiene_summary_lists_each_reason():
    r = P.PushReport(skipped_existing=20, crates_no_match=10,
                     crates_too_small=25, crates_junk_filtered=5)
    s = r.hygiene_summary()
    assert "5 junk/auto-generated" in s
    assert "25 below min-tracks" in s
    assert "10 with no tracks in the live collection" in s
    assert "20 already in the library" in s


def test_hygiene_summary_empty_is_none():
    r = P.PushReport(total_crates=5, crates_planned=5)
    assert r.crates_filtered == 0
    assert r.hygiene_summary() == "none"


def test_hygiene_summary_omits_zero_categories():
    r = P.PushReport(crates_too_small=3)  # only one reason present
    assert r.hygiene_summary() == "3 below min-tracks"
