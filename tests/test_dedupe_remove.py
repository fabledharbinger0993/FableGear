"""De-Duplicate's Remove step (chop_shop/pruner.py).

Duplicates move to a "FableGear Duplicates" folder beside the library, and their
rows leave the FableGear database after everything attached to them (playlists, cues,
beat grid, missing metadata) has been moved onto the kept track. These tests run
against a real FableGear database, not mocks. Rekordbox's database is never involved.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("", "chop_shop"):
    p = str(REPO_ROOT / sub) if sub else str(REPO_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

import pruner

from config import DUPLICATES_DIR_NAME, SKIP_DIRS
from fablegear_database.database import ContentRecord, DatabaseConfig, FableGearDatabase


@pytest.fixture
def archive(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fg.db"))


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Music"
    (root / "House").mkdir(parents=True)
    (root / "Techno").mkdir()
    return root


def _track(archive, path: Path, **fields) -> int:
    path.write_bytes(b"audio" * 20)
    return archive.insert_content(ContentRecord(
        file_path=str(path), file_name=path.name, file_size=path.stat().st_size, **fields))


def _sql(archive, query, args=()):
    with archive.transaction() as conn:
        cur = conn.execute(query, args)
        return cur.fetchall()


def _playlist(archive, name, *content_ids) -> int:
    with archive.transaction() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO fg_playlist (name) VALUES (?)", (name,))
        pid = cur.lastrowid
        for n, cid in enumerate(content_ids, 1):
            cur.execute("INSERT INTO fg_playlist_song (playlist_id, content_id, track_number) VALUES (?,?,?)",
                        (pid, cid, n))
        archive._refresh_playlist_count(cur, pid)
    return pid


def _cue(archive, content_id, kind, slot, in_msec):
    _sql(archive, "INSERT INTO fg_cue (content_id, kind, slot, in_msec) VALUES (?,?,?,?)",
         (content_id, kind, slot, in_msec))


def _remove(archive, library, dup, keeper, **kw):
    return pruner.prune_files(
        [str(dup)], archive=archive, keeper_map={str(dup): str(keeper)},
        library_root=library, **kw)


# ── Where the duplicates go ──────────────────────────────────────────────────

def test_duplicates_folder_sits_beside_the_library_not_inside(tmp_path, library):
    folder = pruner.duplicates_folder(library)
    assert folder == tmp_path / DUPLICATES_DIR_NAME
    assert library not in folder.parents


def test_duplicates_folder_for_a_whole_drive_goes_at_the_top_of_the_drive():
    folder = pruner.duplicates_folder(Path("/Volumes/Passport"))
    assert folder == Path("/Volumes/Passport") / DUPLICATES_DIR_NAME


def test_scanners_skip_the_duplicates_folder():
    import scanner
    assert DUPLICATES_DIR_NAME in SKIP_DIRS
    assert scanner._should_skip_dir(Path("/anywhere") / DUPLICATES_DIR_NAME)


def test_destination_keeps_the_path_relative_to_the_library(tmp_path, library):
    dup_dir = pruner.duplicates_folder(library)
    src = library / "House" / "a.mp3"
    dest = pruner._archive_destination(src, library, dup_dir)
    assert dest == dup_dir / "Music" / "House" / "a.mp3"


def test_same_name_never_overwrites_an_earlier_duplicate(tmp_path, library):
    dup_dir = pruner.duplicates_folder(library)
    src = library / "House" / "a.mp3"
    first = pruner._archive_destination(src, library, dup_dir)
    first.parent.mkdir(parents=True)
    first.write_bytes(b"x")
    second = pruner._archive_destination(src, library, dup_dir)
    assert second != first and second.name == "a (2).mp3"


# ── The remove itself ────────────────────────────────────────────────────────

def test_file_moves_and_row_leaves_the_database(archive, library):
    keeper = library / "House" / "keep.mp3"
    dup = library / "Techno" / "dup.mp3"
    _track(archive, keeper)
    _track(archive, dup)

    summary = _remove(archive, library, dup, keeper)

    assert summary["files_moved"] == 1 and summary["db_removed"] == 1 and not summary["errors"]
    assert not dup.exists()
    moved = Path(summary["duplicates_dir"]) / "Music" / "Techno" / "dup.mp3"
    assert moved.is_file()
    assert archive.get_content_by_path(str(dup)) is None
    assert archive.get_content_by_path(str(keeper)) is not None


def test_a_move_log_is_written_beside_the_files(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    _track(archive, keeper)
    _track(archive, dup)
    summary = _remove(archive, library, dup, keeper)
    lines = (Path(summary["duplicates_dir"]) / "moves.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert entry["original"] == str(dup) and entry["kept"] == str(keeper)
    assert Path(entry["moved_to"]).is_file()


def test_playlist_slot_moves_to_the_keeper_and_counts_refresh(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    kid, did = _track(archive, keeper), _track(archive, dup)
    pid = _playlist(archive, "Friday", did)

    summary = _remove(archive, library, dup, keeper)

    assert summary["playlists_rethreaded"] == 1
    assert _sql(archive, "SELECT content_id FROM fg_playlist_song WHERE playlist_id = ?", (pid,)) == [(kid,)]
    assert _sql(archive, "SELECT track_count FROM fg_playlist WHERE id = ?", (pid,)) == [(1,)]


def test_playlist_that_already_has_the_keeper_does_not_get_it_twice(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    kid, did = _track(archive, keeper), _track(archive, dup)
    pid = _playlist(archive, "Both", kid, did)

    _remove(archive, library, dup, keeper)

    assert _sql(archive, "SELECT content_id FROM fg_playlist_song WHERE playlist_id = ?", (pid,)) == [(kid,)]
    assert _sql(archive, "SELECT track_count FROM fg_playlist WHERE id = ?", (pid,)) == [(1,)]


def test_hot_cue_slot_conflict_keeper_wins_and_other_cues_move(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    kid, did = _track(archive, keeper), _track(archive, dup)
    _cue(archive, kid, 1, 1, 1000)    # keeper's hot cue in pad 1
    _cue(archive, did, 1, 1, 2000)    # conflicts: dropped
    _cue(archive, did, 1, 2, 3000)    # free pad: moves
    _cue(archive, did, 0, None, 4000) # memory cue: moves

    _remove(archive, library, dup, keeper)

    rows = _sql(archive, "SELECT kind, slot, in_msec FROM fg_cue WHERE content_id = ? ORDER BY in_msec", (kid,))
    assert rows == [(1, 1, 1000), (1, 2, 3000), (0, None, 4000)]
    assert _sql(archive, "SELECT COUNT(*) FROM fg_cue WHERE content_id = ?", (did,)) == [(0,)]


def test_beat_grid_moves_only_when_the_keeper_has_none(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    kid, did = _track(archive, keeper), _track(archive, dup)
    _sql(archive, "INSERT INTO fg_beatgrid (content_id, beat_number, time_msec, bpm) VALUES (?,?,?,?)",
         (did, 1, 100, 128.0))

    _remove(archive, library, dup, keeper)

    assert _sql(archive, "SELECT bpm FROM fg_beatgrid WHERE content_id = ?", (kid,)) == [(128.0,)]


def test_keepers_own_beat_grid_is_never_replaced(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    kid, did = _track(archive, keeper), _track(archive, dup)
    _sql(archive, "INSERT INTO fg_beatgrid (content_id, beat_number, time_msec, bpm) VALUES (?,?,?,?)",
         (kid, 1, 50, 126.0))
    _sql(archive, "INSERT INTO fg_beatgrid (content_id, beat_number, time_msec, bpm) VALUES (?,?,?,?)",
         (did, 1, 100, 128.0))

    _remove(archive, library, dup, keeper)

    assert _sql(archive, "SELECT bpm FROM fg_beatgrid WHERE content_id = ?", (kid,)) == [(126.0,)]


def test_metadata_fills_only_the_keepers_empty_fields(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    _track(archive, keeper, artist="Keeper Artist", bpm=None)
    _track(archive, dup, artist="Other Artist", genre="House", bpm=128.0)

    summary = _remove(archive, library, dup, keeper)

    rec = archive.get_content_by_path(str(keeper))
    assert rec.artist == "Keeper Artist"      # keeper's own value wins
    assert rec.genre == "House" and rec.bpm == 128.0
    assert summary["metadata_backfilled"] == 2


# ── Safety ───────────────────────────────────────────────────────────────────

def test_track_in_a_playlist_with_no_kept_copy_in_the_database_is_left_alone(archive, library):
    keeper = library / "House" / "k.mp3"       # never added to the database
    keeper.write_bytes(b"x")
    dup = library / "Techno" / "d.mp3"
    did = _track(archive, dup)
    _playlist(archive, "Friday", did)

    summary = _remove(archive, library, dup, keeper)

    assert summary["skipped"] == 1 and summary["files_moved"] == 0
    assert dup.exists()
    assert archive.get_content_by_path(str(dup)) is not None


def test_database_failure_puts_the_file_back(archive, library, monkeypatch):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    _track(archive, keeper)
    _track(archive, dup)

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(pruner, "_rethread_playlists", boom)

    summary = _remove(archive, library, dup, keeper)

    assert summary["files_moved"] == 0 and len(summary["errors"]) == 1
    assert dup.exists()                                   # back where it was
    assert archive.get_content_by_path(str(dup)) is not None
    assert not (Path(summary["duplicates_dir"]) / "Music" / "Techno" / "d.mp3").exists()


def test_file_that_is_not_in_the_database_still_moves(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    _track(archive, keeper)
    dup.write_bytes(b"x")

    summary = _remove(archive, library, dup, keeper)

    assert summary["files_moved"] == 1 and summary["db_removed"] == 0
    assert not dup.exists()


def test_missing_file_is_skipped_not_an_error(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "gone.mp3"
    _track(archive, keeper)

    summary = _remove(archive, library, dup, keeper)

    assert summary["skipped"] == 1 and not summary["errors"]


def test_cancel_stops_before_the_next_file(archive, library):
    keeper, dup = library / "House" / "k.mp3", library / "Techno" / "d.mp3"
    _track(archive, keeper)
    _track(archive, dup)

    summary = _remove(archive, library, dup, keeper, should_cancel=lambda: True)

    assert summary["cancelled"] and dup.exists()


def test_the_pruner_never_touches_rekordbox():
    source = (REPO_ROOT / "chop_shop" / "pruner.py").read_text()
    for banned in ("pyrekordbox", "db_connection", "write_db", "DjmdContent", "master.db"):
        assert banned not in source.replace("Rekordbox's own database is never opened or written", ""), banned
