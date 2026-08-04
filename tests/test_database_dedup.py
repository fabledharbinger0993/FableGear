"""
Tests for database_dedup.py — the Database Library duplicate resolver.

Unlike chop_shop/duplicate_detector.py (acoustic fingerprinting of files on
disk), this module never reads audio. Its whole job is: group DjmdContent
records by artist+title+duration, pick a survivor without guessing when the
choice would be a coin flip, then re-wire playlist memberships onto the
survivor before the redundant records are removed. These tests pin that
contract with lightweight fakes — no real Rekordbox database required.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from database_dedup import choose_keeper, execute_plan, scan_conflicts

# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeArtist:
    def __init__(self, name):
        self.Name = name


class FakeTrack:
    def __init__(self, id_, title, artist, length, path):
        self.ID = id_
        self.Title = title
        self.Artist = FakeArtist(artist) if artist else None
        self.Length = length
        self.FolderPath = path


class FakeQuery(list):
    def all(self):
        return list(self)


class FakeSongRow:
    def __init__(self, content_id, playlist_id):
        self.ContentID = content_id
        self.PlaylistID = playlist_id


class FakeSession:
    def __init__(self):
        self.deleted = []

    def delete(self, obj):
        self.deleted.append(obj)


class FakeDB:
    """Enough of the pyrekordbox surface for scan_conflicts()/execute_plan()."""

    def __init__(self, tracks, song_rows):
        self._tracks = tracks
        self._songs = song_rows
        self.session = FakeSession()

    def get_content(self, ID=None):
        live_tracks = [t for t in self._tracks if t not in self.session.deleted]
        if ID is not None:
            for t in live_tracks:
                if str(t.ID) == str(ID):
                    return t
            return None
        return FakeQuery(live_tracks)

    def get_playlist_songs(self, ContentID=None, PlaylistID=None):
        rows = [
            s for s in self._songs
            if s not in self.session.deleted
            and (ContentID is None or str(s.ContentID) == str(ContentID))
            and (PlaylistID is None or str(s.PlaylistID) == str(PlaylistID))
        ]
        return FakeQuery(rows)


# ── choose_keeper ──────────────────────────────────────────────────────────

def test_choose_keeper_prefers_path_that_exists():
    entries = [
        {"content_id": "1", "path": "/a/broken.mp3", "exists_on_disk": False, "playlist_ref_count": 5},
        {"content_id": "2", "path": "/a/good.mp3", "exists_on_disk": True, "playlist_ref_count": 0},
    ]
    keeper, ambiguous = choose_keeper(entries)
    assert keeper["content_id"] == "2"
    assert ambiguous is False


def test_choose_keeper_prefers_more_playlist_refs_when_both_exist():
    entries = [
        {"content_id": "1", "path": "/a/1.mp3", "exists_on_disk": True, "playlist_ref_count": 1},
        {"content_id": "2", "path": "/a/2.mp3", "exists_on_disk": True, "playlist_ref_count": 4},
    ]
    keeper, ambiguous = choose_keeper(entries)
    assert keeper["content_id"] == "2"
    assert ambiguous is False


def test_choose_keeper_flags_ambiguous_when_tied():
    entries = [
        {"content_id": "1", "path": "/a/1.mp3", "exists_on_disk": True, "playlist_ref_count": 2},
        {"content_id": "2", "path": "/a/2.mp3", "exists_on_disk": True, "playlist_ref_count": 2},
    ]
    keeper, ambiguous = choose_keeper(entries)
    assert ambiguous is True
    # Still returns a deterministic pick so callers *can* act on it if they choose to.
    assert keeper is not None


def test_choose_keeper_flags_ambiguous_when_both_broken_and_tied():
    entries = [
        {"content_id": "1", "path": "/a/1.mp3", "exists_on_disk": False, "playlist_ref_count": 0},
        {"content_id": "2", "path": "/a/2.mp3", "exists_on_disk": False, "playlist_ref_count": 0},
    ]
    _, ambiguous = choose_keeper(entries)
    assert ambiguous is True


def test_choose_keeper_empty_entries():
    keeper, ambiguous = choose_keeper([])
    assert keeper is None
    assert ambiguous is False


# ── scan_conflicts ─────────────────────────────────────────────────────────

def test_scan_conflicts_groups_by_artist_title_duration(tmp_path):
    good = tmp_path / "good.mp3"
    good.write_bytes(b"x")

    tracks = [
        FakeTrack(1, "Sunset", "Kerri Chandler", 300, str(good)),
        FakeTrack(2, "Sunset", "Kerri Chandler", 300, "/broken/path.mp3"),
        FakeTrack(3, "A Different Track", "Someone Else", 200, str(tmp_path / "other.mp3")),
    ]
    db = FakeDB(tracks, song_rows=[])

    scanned, conflicts = scan_conflicts(db)

    assert scanned == 3
    assert len(conflicts) == 1
    group = conflicts[0]
    assert group["signature"]["title"] == "sunset"
    assert group["path_count"] == 2
    ids = {e["content_id"] for e in group["entries"]}
    assert ids == {"1", "2"}
    exists_by_id = {e["content_id"]: e["exists_on_disk"] for e in group["entries"]}
    assert exists_by_id["1"] is True
    assert exists_by_id["2"] is False


def test_scan_conflicts_ignores_tracks_with_no_path_conflict():
    tracks = [
        FakeTrack(1, "Track A", "Artist", 100, "/only/one/path.mp3"),
    ]
    db = FakeDB(tracks, song_rows=[])
    scanned, conflicts = scan_conflicts(db)
    assert scanned == 1
    assert conflicts == []


# ── execute_plan ───────────────────────────────────────────────────────────

def _plan(entries):
    return {"plans": entries}


def test_execute_plan_rewires_and_removes_loser():
    tracks = [
        FakeTrack(1, "Sunset", "Kerri Chandler", 300, "/keep.mp3"),
        FakeTrack(2, "Sunset", "Kerri Chandler", 300, "/broken.mp3"),
    ]
    songs = [FakeSongRow(content_id=2, playlist_id="P1")]
    db = FakeDB(tracks, songs)

    plan = _plan([{
        "signature": {"artist": "kerri chandler", "title": "sunset", "duration": 300},
        "keeper": {"content_id": "1", "path": "/keep.mp3"},
        "remove_candidates": [{"content_id": "2", "path": "/broken.mp3"}],
        "ambiguous": False,
    }])

    summary = execute_plan(db, plan)

    assert summary["groups_resolved"] == 1
    assert summary["content_removed"] == 1
    assert summary["playlists_rethreaded"] == 1
    # The playlist row now points at the survivor.
    assert songs[0].ContentID == 1
    # The loser's DjmdContent row was deleted.
    assert db.get_content(ID=2) is None
    assert db.get_content(ID=1) is not None


def test_execute_plan_drops_redundant_membership_instead_of_duplicating():
    tracks = [
        FakeTrack(1, "Sunset", "Kerri Chandler", 300, "/keep.mp3"),
        FakeTrack(2, "Sunset", "Kerri Chandler", 300, "/broken.mp3"),
    ]
    # Both records already sit in playlist P1 — the duplicate's slot should
    # be dropped, not turned into a second entry for the same playlist.
    songs = [
        FakeSongRow(content_id=1, playlist_id="P1"),
        FakeSongRow(content_id=2, playlist_id="P1"),
    ]
    db = FakeDB(tracks, songs)

    plan = _plan([{
        "signature": {"artist": "kerri chandler", "title": "sunset", "duration": 300},
        "keeper": {"content_id": "1", "path": "/keep.mp3"},
        "remove_candidates": [{"content_id": "2", "path": "/broken.mp3"}],
        "ambiguous": False,
    }])

    summary = execute_plan(db, plan)

    assert summary["playlists_rethreaded"] == 0  # nothing to re-point, just dropped
    remaining = db.get_playlist_songs(PlaylistID="P1").all()
    assert len(remaining) == 1
    assert remaining[0].ContentID == 1


def test_execute_plan_skips_ambiguous_groups():
    tracks = [
        FakeTrack(1, "Sunset", "Kerri Chandler", 300, "/a.mp3"),
        FakeTrack(2, "Sunset", "Kerri Chandler", 300, "/b.mp3"),
    ]
    db = FakeDB(tracks, song_rows=[])

    plan = _plan([{
        "signature": {"artist": "kerri chandler", "title": "sunset", "duration": 300},
        "keeper": {"content_id": "1", "path": "/a.mp3"},
        "remove_candidates": [{"content_id": "2", "path": "/b.mp3"}],
        "ambiguous": True,
    }])

    summary = execute_plan(db, plan)

    assert summary["groups_resolved"] == 0
    assert summary["groups_skipped_ambiguous"] == 1
    assert summary["content_removed"] == 0
    # Nothing was touched.
    assert db.get_content(ID=1) is not None
    assert db.get_content(ID=2) is not None


def test_execute_plan_signatures_filter_restricts_to_reviewed_groups():
    tracks = [
        FakeTrack(1, "Track A", "Artist A", 100, "/a1.mp3"),
        FakeTrack(2, "Track A", "Artist A", 100, "/a2.mp3"),
        FakeTrack(3, "Track B", "Artist B", 200, "/b1.mp3"),
        FakeTrack(4, "Track B", "Artist B", 200, "/b2.mp3"),
    ]
    db = FakeDB(tracks, song_rows=[])

    plan = _plan([
        {
            "signature": {"artist": "artist a", "title": "track a", "duration": 100},
            "keeper": {"content_id": "1", "path": "/a1.mp3"},
            "remove_candidates": [{"content_id": "2", "path": "/a2.mp3"}],
            "ambiguous": False,
        },
        {
            "signature": {"artist": "artist b", "title": "track b", "duration": 200},
            "keeper": {"content_id": "3", "path": "/b1.mp3"},
            "remove_candidates": [{"content_id": "4", "path": "/b2.mp3"}],
            "ambiguous": False,
        },
    ])

    summary = execute_plan(
        db, plan, signatures=[{"artist": "artist a", "title": "track a", "duration": 100}],
    )

    assert summary["groups_resolved"] == 1
    assert summary["groups_skipped_filtered"] == 1
    assert db.get_content(ID=2) is None      # resolved group — loser removed
    assert db.get_content(ID=4) is not None  # filtered-out group — untouched
