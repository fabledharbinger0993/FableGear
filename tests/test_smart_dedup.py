"""
Tests for the Smart Deduper's pure decision core (smart_dedup).

The whole point of this tool is that resolving a duplicate must NEVER orphan a
playlist. These pin the two guarantees that deliver that:

* survivor choice follows the documented preference order (existing file →
  canonical location → most memberships → flag on a tie), differing correctly
  between DATABASE and PHYSICAL modes;
* the re-wire plan moves every membership of a doomed record onto the survivor,
  and drops (rather than duplicates) a membership the survivor already has.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import smart_dedup as SD  # noqa: E402


def _t(i, title="Song", artist="Artist", path=None, playlists=(), duration=200):
    return SD.TrackRec(id=i, title=title, artist=artist, folder_path=path,
                       playlist_ids=tuple(playlists), duration=duration)


NONE_EXIST = lambda p: False          # noqa: E731
ALL_EXIST = lambda p: True            # noqa: E731


# ── grouping ────────────────────────────────────────────────────────────────

def test_grouping_matches_on_title_and_artist_case_insensitively():
    tracks = [_t(1, "Boogie", "Kenny Dope"), _t(2, "boogie", " kenny dope "),
              _t(3, "Other", "X")]
    groups = SD.find_duplicate_groups(tracks)
    assert len(groups) == 1 and {r.id for r in groups[0]} == {1, 2}


def test_empty_title_never_groups():
    assert SD.find_duplicate_groups([_t(1, ""), _t(2, "")]) == []


# ── duration guards the identity (false-merge protection) ───────────────────

def test_radio_edit_and_extended_mix_never_merge():
    """The defect this guards: same title+artist, wildly different lengths are
    different recordings. Merging them would delete one and re-point its crates."""
    tracks = [_t(1, "Boogie", "Kenny Dope", duration=210),   # 3:30 radio edit
              _t(2, "Boogie", "Kenny Dope", duration=420)]   # 7:00 extended mix
    assert SD.find_duplicate_groups(tracks) == []


def test_small_duration_disagreement_still_groups():
    # Encoders/metadata routinely differ by a second or two — still one track.
    tracks = [_t(1, "Boogie", "Kenny Dope", duration=210),
              _t(2, "Boogie", "Kenny Dope", duration=211)]
    groups = SD.find_duplicate_groups(tracks)
    assert len(groups) == 1 and {r.id for r in groups[0]} == {1, 2}


def test_no_bucket_boundary_artifact():
    """Proximity clustering, not fixed buckets: a 1s difference must group no
    matter where the absolute values fall."""
    for base in range(200, 212):
        tracks = [_t(1, duration=base), _t(2, duration=base + 1)]
        groups = SD.find_duplicate_groups(tracks)
        assert len(groups) == 1, f"failed to group at base={base}"


def test_unknown_duration_groups_only_with_unknown():
    tracks = [_t(1, duration=None), _t(2, duration=None), _t(3, duration=200)]
    groups = SD.find_duplicate_groups(tracks)
    assert len(groups) == 1 and {r.id for r in groups[0]} == {1, 2}


def test_distinct_length_clusters_split_into_separate_groups():
    # Two real duplicate pairs of different mixes under one name.
    tracks = [_t(1, duration=210), _t(2, duration=210),
              _t(3, duration=420), _t(4, duration=420)]
    groups = SD.find_duplicate_groups(tracks)
    assert len(groups) == 2
    assert {frozenset(r.id for r in g) for g in groups} == {frozenset({1, 2}), frozenset({3, 4})}


def test_same_path_duplicates_are_detected():
    """A double drag-and-drop yields two records with the SAME path — the case
    database_dedup.py skips (it requires >1 distinct path)."""
    tracks = [_t(1, path="/lib/a.mp3", playlists=(1, 2)),
              _t(2, path="/lib/a.mp3", playlists=(3,))]
    groups = SD.find_duplicate_groups(tracks)
    assert len(groups) == 1 and len(groups[0]) == 2


# ── survivor choice ─────────────────────────────────────────────────────────

def test_database_mode_prefers_the_only_existing_file():
    recs = [_t(1, path="/gone/a.mp3"), _t(2, path="/here/a.mp3")]
    survivor, _ = SD.choose_survivor(recs, SD.DedupMode.DATABASE,
                                     lambda p: p == "/here/a.mp3")
    assert survivor.id == 2


def test_prefers_canonical_location_when_both_exist():
    recs = [_t(1, path="/x/Music for Dj's/a.mp3"), _t(2, path="/y/dl/a.mp3")]
    survivor, reason = SD.choose_survivor(recs, SD.DedupMode.DATABASE, ALL_EXIST)
    assert survivor.id == 1 and "canonical" in reason


def test_breaks_tie_on_playlist_membership_count():
    recs = [_t(1, path="/a.mp3", playlists=(10, 11, 12)), _t(2, path="/b.mp3", playlists=(10,))]
    survivor, reason = SD.choose_survivor(recs, SD.DedupMode.DATABASE, NONE_EXIST)
    assert survivor.id == 1 and "membership" in reason


def test_true_tie_is_flagged_not_guessed():
    recs = [_t(1, path="/a.mp3", playlists=(10,)), _t(2, path="/b.mp3", playlists=(11,))]
    survivor, _ = SD.choose_survivor(recs, SD.DedupMode.DATABASE, NONE_EXIST)
    assert survivor is None  # equal candidates → manual review


def test_physical_mode_requires_two_files_on_disk():
    recs = [_t(1, path="/here/a.mp3"), _t(2, path="/gone/a.mp3")]
    # only one exists → not a physical duplicate
    survivor, _ = SD.choose_survivor(recs, SD.DedupMode.PHYSICAL,
                                     lambda p: p == "/here/a.mp3")
    assert survivor is None


# ── re-wire planning (the non-breaking guarantee) ───────────────────────────

def test_rewire_moves_and_drops_correctly():
    # survivor is id1 (3 memberships, the most); id2 in {10 (shared), 30}
    recs = [_t(1, path="/a.mp3", playlists=(10, 20, 21)),
            _t(2, path="/b.mp3", playlists=(10, 30))]
    gp = SD.plan_group(recs, SD.DedupMode.DATABASE, NONE_EXIST)
    assert gp.resolution == "auto" and gp.survivor.id == 1
    rw = gp.rewires[2]
    assert rw["move"] == [30]   # 30 not on survivor → moved
    assert rw["drop"] == [10]   # 10 already on survivor → dropped
    assert gp.links_rewired == 1 and gp.links_dropped == 1


def test_no_membership_is_lost_across_the_group():
    # id1 has the most memberships → unambiguous survivor.
    recs = [_t(1, path="/a.mp3", playlists=(1, 2, 5)),
            _t(2, path="/b.mp3", playlists=(2, 3)),
            _t(3, path="/c.mp3", playlists=(4,))]
    gp = SD.plan_group(recs, SD.DedupMode.DATABASE, NONE_EXIST)
    assert gp.resolution == "auto"
    # every playlist the group touched must end up reachable via the survivor:
    survivor_final = set(gp.survivor.playlist_ids)
    for r in gp.to_remove:
        survivor_final |= set(gp.rewires[r.id]["move"])
    union = set().union(*[set(r.playlist_ids) for r in recs])
    assert survivor_final == union  # nothing dropped from the collection


def test_flagged_group_has_no_rewire():
    recs = [_t(1, path="/a.mp3", playlists=(1,)), _t(2, path="/b.mp3", playlists=(2,))]
    gp = SD.plan_group(recs, SD.DedupMode.DATABASE, NONE_EXIST)
    assert gp.resolution == "flagged" and gp.rewires == {}


# ── plan summary ────────────────────────────────────────────────────────────

def test_build_plan_summary_counts():
    tracks = [
        _t(1, "A", "x", "/here/a.mp3", (1,)), _t(2, "A", "x", "/gone/a.mp3", (2,)),  # auto
        _t(3, "B", "y", "/p.mp3", (1,)), _t(4, "B", "y", "/q.mp3", (2,)),            # tie → flagged
    ]
    plan = SD.build_plan(tracks, SD.DedupMode.DATABASE, lambda p: p == "/here/a.mp3")
    s = plan.summary()
    assert s["duplicate_groups"] == 2
    assert s["auto_resolvable"] == 1
    assert s["flagged_for_review"] == 1
    assert s["records_to_remove"] == 1
