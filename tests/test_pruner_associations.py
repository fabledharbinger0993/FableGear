"""
Tests for pruner._rethread_associations() and its helpers.

Context: djmdContent has no cascade on Cues/MixerParams/MyTags/ActiveCensors
etc. (verified against pyrekordbox 0.4.4's ORM — no `cascade` kwarg on those
relationships, and pyrekordbox never turns on `PRAGMA foreign_keys`). Hard-
deleting a duplicate's djmdContent row without re-threading these tables
first leaves orphaned rows behind — Rekordbox will never surface them again.
These tests exercise the fix against a real (unencrypted) pyrekordbox-schema
SQLite database, not mocks, so a wrong ORM assumption fails loudly here
instead of on a user's live multi-hundred-thousand-track library.
"""

import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables as rb_tables
from sqlalchemy import create_engine, text

from rekordbox_meta_support import relaxed_rekordbox_nullability

import pruner


@pytest.fixture
def db(tmp_path):
    """A real, unencrypted pyrekordbox-schema database (unlock=False test path)."""
    db_path = tmp_path / "master.db"
    engine = create_engine(f"sqlite:///{db_path}")
    # pyrekordbox's Mapped[str] (non-Optional) annotations make SQLAlchemy 2.0
    # infer NOT NULL for nearly every column — a stricter constraint than the
    # real master.db actually enforces (pyrekordbox writes routinely leave
    # most descriptive fields as NULL). Relax nullability so the test schema
    # matches real-world data shapes; the helper restores the shared metadata
    # afterward so the mutation can't leak into later tests.
    with relaxed_rekordbox_nullability():
        rb_tables.Base.metadata.create_all(engine)
    engine.dispose()

    # djmdCloudExportSongPlaylist and djmdRecommendLike have no ORM model in
    # pyrekordbox 0.4.4, so pruner.py talks to them via raw SQL — create them
    # by hand to match the real live-schema columns found on a production library.
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE djmdCloudExportSongPlaylist ("
            "ID VARCHAR(255) PRIMARY KEY, CloudExportPlaylistID VARCHAR(255), "
            "ContentID VARCHAR(255), TrackNo INTEGER)"
        ))
        conn.execute(text(
            "CREATE TABLE djmdRecommendLike ("
            "ID VARCHAR(255) PRIMARY KEY, ContentID1 VARCHAR(255), "
            "ContentID2 VARCHAR(255), LikeRate INTEGER)"
        ))
    engine.dispose()

    handle = Rekordbox6Database(path=str(db_path), unlock=False)
    # Rekordbox6Database.commit() (as opposed to the lower-level
    # .session.commit()) bumps a global USN counter stored in this row. A
    # real master.db always has it; a from-scratch test schema doesn't, so
    # seed it for any test that calls db.commit() (e.g. via prune_files()).
    from datetime import datetime as _dt
    handle.session.add(rb_tables.AgentRegistry(
        registry_id="localUpdateCount", int_1=0,
        date_1=_dt.utcnow(), date_2=_dt.utcnow(),
        created_at=_dt.utcnow(), updated_at=_dt.utcnow(),
    ))
    handle.session.commit()
    yield handle
    handle.close()


def _make_content(db, **overrides):
    overrides.setdefault("FileNameL", "track.mp3")
    overrides.setdefault("FileNameS", "track.mp3")
    overrides.setdefault("Title", "Track")
    content = rb_tables.DjmdContent(ID=str(uuid.uuid4()), **overrides)
    db.session.add(content)
    return content


def _make_cue(db, content_id, **overrides):
    cue = rb_tables.DjmdCue(ID=str(uuid.uuid4()), ContentID=content_id, **overrides)
    db.session.add(cue)
    return cue


# ── Metadata backfill ────────────────────────────────────────────────────────

def test_backfill_fills_only_empty_keeper_fields(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3", BPM=None, GenreID=None, Rating=4)
    dup = _make_content(db, FolderPath="/dup.mp3", BPM=12800, GenreID="7", Rating=2)
    db.session.flush()

    filled = pruner._backfill_metadata(dup, keeper, emit=lambda m: None)

    assert filled == 2  # BPM and GenreID were empty on keeper
    assert keeper.BPM == 12800
    assert keeper.GenreID == "7"
    assert keeper.Rating == 4  # keeper's populated Rating must NOT be overwritten


# ── Playlist re-threading parity (regression vs. the old _rethread_playlists) ─

def test_group_table_rethreads_when_keeper_not_a_member(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    slot = rb_tables.DjmdSongPlaylist(ID=str(uuid.uuid4()), PlaylistID="PL1", ContentID=dup.ID, TrackNo=1)
    db.session.add(slot)
    db.session.flush()

    rethreaded, dropped = pruner._rethread_group_table(db, "get_playlist_songs", "PlaylistID", dup.ID, keeper.ID)

    assert (rethreaded, dropped) == (1, 0)
    assert slot.ContentID == keeper.ID


def test_group_table_drops_when_keeper_already_a_member(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    db.session.add(rb_tables.DjmdSongPlaylist(ID=str(uuid.uuid4()), PlaylistID="PL1", ContentID=keeper.ID, TrackNo=1))
    dup_slot = rb_tables.DjmdSongPlaylist(ID=str(uuid.uuid4()), PlaylistID="PL1", ContentID=dup.ID, TrackNo=2)
    db.session.add(dup_slot)
    db.session.flush()

    rethreaded, dropped = pruner._rethread_group_table(db, "get_playlist_songs", "PlaylistID", dup.ID, keeper.ID)

    assert (rethreaded, dropped) == (0, 1)
    remaining = db.get_playlist_songs(PlaylistID="PL1").all()
    assert len(remaining) == 1
    assert remaining[0].ContentID == keeper.ID


def test_group_table_preserves_distinct_memberships_across_multiple_groups(db):
    # A track can be a member of several distinct My-Tag lists — dropping the
    # whole table just because the keeper has *some* My-Tag row would silently
    # lose the other memberships. Two different MyTagIDs must both survive.
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    db.session.add(rb_tables.DjmdSongMyTag(ID=str(uuid.uuid4()), MyTagID="TAG_A", ContentID=keeper.ID, TrackNo=1))
    db.session.add(rb_tables.DjmdSongMyTag(ID=str(uuid.uuid4()), MyTagID="TAG_A", ContentID=dup.ID, TrackNo=1))
    db.session.add(rb_tables.DjmdSongMyTag(ID=str(uuid.uuid4()), MyTagID="TAG_B", ContentID=dup.ID, TrackNo=1))
    db.session.flush()

    rethreaded, dropped = pruner._rethread_group_table(db, "get_my_tag_songs", "MyTagID", dup.ID, keeper.ID)

    assert (rethreaded, dropped) == (1, 1)  # TAG_B re-threaded, TAG_A dropped (keeper already had it)
    keeper_tags = {row.MyTagID for row in db.get_my_tag_songs(ContentID=keeper.ID).all()}
    assert keeper_tags == {"TAG_A", "TAG_B"}


# ── Simple singleton tables ───────────────────────────────────────────────────

def test_simple_table_copies_when_keeper_has_none(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    db.session.add(rb_tables.DjmdMixerParam(ID=str(uuid.uuid4()), ContentID=dup.ID, GainHigh=1))
    db.session.flush()

    copied, dropped = pruner._copy_or_drop_simple(db, "get_mixer_param", dup.ID, keeper.ID)

    assert (copied, dropped) == (1, 0)
    assert db.get_mixer_param(ContentID=keeper.ID).all()[0].GainHigh == 1


def test_simple_table_drops_when_keeper_already_has_one(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    db.session.add(rb_tables.DjmdMixerParam(ID=str(uuid.uuid4()), ContentID=keeper.ID, GainHigh=5))
    db.session.add(rb_tables.DjmdMixerParam(ID=str(uuid.uuid4()), ContentID=dup.ID, GainHigh=1))
    db.session.flush()

    copied, dropped = pruner._copy_or_drop_simple(db, "get_mixer_param", dup.ID, keeper.ID)

    assert (copied, dropped) == (0, 1)
    remaining = db.get_mixer_param(ContentID=keeper.ID).all()
    assert len(remaining) == 1
    assert remaining[0].GainHigh == 5  # keeper's own row survives untouched


# ── Cues: hot-cue slot conflicts vs. positional memory cues ───────────────────

def test_hot_cue_copied_into_empty_slot(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    _make_cue(db, dup.ID, Kind=1, InMsec=1000)  # hot cue A
    db.session.flush()

    copied, dropped = pruner._rethread_cues(db, dup.ID, keeper.ID)

    assert (copied, dropped) == (1, 0)
    assert db.get_cue(ContentID=keeper.ID).all()[0].Kind == 1


def test_hot_cue_conflict_keeper_wins(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    _make_cue(db, keeper.ID, Kind=1, InMsec=500)   # keeper already has hot cue A
    _make_cue(db, dup.ID, Kind=1, InMsec=1000)     # duplicate also has hot cue A — conflict
    db.session.flush()

    copied, dropped = pruner._rethread_cues(db, dup.ID, keeper.ID)

    assert (copied, dropped) == (0, 1)
    remaining = db.get_cue(ContentID=keeper.ID).all()
    assert len(remaining) == 1
    assert remaining[0].InMsec == 500  # keeper's own hot cue A survives, duplicate's is dropped


def test_memory_cues_accumulate_without_slot_conflict(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    _make_cue(db, keeper.ID, Kind=0, InMsec=1000)  # keeper's memory cue
    _make_cue(db, dup.ID, Kind=0, InMsec=5000)     # duplicate's memory cue at a different position
    _make_cue(db, dup.ID, Kind=0, InMsec=1000)     # exact duplicate of keeper's position
    db.session.flush()

    copied, dropped = pruner._rethread_cues(db, dup.ID, keeper.ID)

    assert (copied, dropped) == (1, 1)  # the 5000ms cue copied over; the 1000ms exact dupe dropped
    positions = {c.InMsec for c in db.get_cue(ContentID=keeper.ID).all()}
    assert positions == {1000, 5000}


# ── Tables with no pyrekordbox ORM model (raw SQL) ────────────────────────────

def test_cloud_export_playlist_rethreads_via_raw_sql(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    db.session.flush()
    db.session.execute(
        text(
            "INSERT INTO djmdCloudExportSongPlaylist "
            "(ID, CloudExportPlaylistID, ContentID) VALUES (:id, 'CP1', :cid)"
        ),
        {"id": str(uuid.uuid4()), "cid": dup.ID},
    )
    db.session.flush()

    rethreaded, dropped = pruner._rethread_cloud_export_playlists(db, dup.ID, keeper.ID)

    assert (rethreaded, dropped) == (1, 0)
    row = db.session.execute(
        text("SELECT ContentID FROM djmdCloudExportSongPlaylist WHERE CloudExportPlaylistID = 'CP1'")
    ).fetchone()
    assert row[0] == keeper.ID


def test_recommend_like_repoints_without_self_pairing(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3")
    dup = _make_content(db, FolderPath="/dup.mp3")
    other = _make_content(db, FolderPath="/other.mp3")
    db.session.flush()
    # dup <-> other should re-point to keeper <-> other
    db.session.execute(
        text("INSERT INTO djmdRecommendLike (ID, ContentID1, ContentID2, LikeRate) VALUES (:id, :a, :b, 3)"),
        {"id": str(uuid.uuid4()), "a": dup.ID, "b": other.ID},
    )
    # dup <-> keeper should be dropped, not turned into a keeper <-> keeper self-pair
    db.session.execute(
        text("INSERT INTO djmdRecommendLike (ID, ContentID1, ContentID2, LikeRate) VALUES (:id, :a, :b, 5)"),
        {"id": str(uuid.uuid4()), "a": dup.ID, "b": keeper.ID},
    )
    db.session.flush()

    rethreaded, dropped = pruner._rethread_recommend_likes(db, dup.ID, keeper.ID)

    assert (rethreaded, dropped) == (1, 1)
    rows = db.session.execute(text("SELECT ContentID1, ContentID2 FROM djmdRecommendLike")).fetchall()
    assert (keeper.ID, other.ID) in rows
    assert not any(keeper.ID in r and keeper.ID == r[0] == r[1] for r in rows)  # no self-pair


# ── Full orchestrator ─────────────────────────────────────────────────────────

def test_rethread_associations_end_to_end(db):
    keeper = _make_content(db, FolderPath="/keeper.mp3", BPM=None)
    dup = _make_content(db, FolderPath="/dup.mp3", BPM=12800)
    db.session.flush()
    db.session.add(rb_tables.DjmdSongPlaylist(ID=str(uuid.uuid4()), PlaylistID="PL1", ContentID=dup.ID, TrackNo=1))
    _make_cue(db, dup.ID, Kind=2, InMsec=2000)
    db.session.flush()

    messages = []
    result = pruner._rethread_associations(dup, keeper.FolderPath, db, emit=messages.append)

    assert result["metadata_backfilled"] == 1
    assert result["playlists_rethreaded"] == 1
    assert result["associations_rethreaded"] == 1  # the hot cue
    assert keeper.BPM == 12800
    assert db.get_playlist_songs(PlaylistID="PL1").all()[0].ContentID == keeper.ID
    assert db.get_cue(ContentID=keeper.ID).all()[0].Kind == 2


# ── prune_files: per-path savepoint isolation on partial failure ────────────

def test_prune_files_rolls_back_only_the_failed_path_not_the_whole_batch(db, tmp_path, monkeypatch):
    """
    A path whose second content row fails mid-rethread must not lose (a) the
    first row's already-applied rethread for *that* path, silently committing
    a half-done state, nor (b) an unrelated, already-succeeded path earlier
    in the same batch. Before the begin_nested() fix, prune_files() had no
    per-path transaction boundary: a failure here could ride along into
    whatever the single end-of-run db.commit() picked up.
    """
    good_keeper = _make_content(db, FolderPath="/good_keeper.mp3")
    good_dup = _make_content(db, FolderPath="/good_dup.mp3")

    fail_keeper = _make_content(db, FolderPath="/fail_keeper.mp3")
    fail_dup_a = _make_content(db, FolderPath="/fail_shared.mp3", FileNameL="a.mp3", FileNameS="a.mp3")
    fail_dup_b = _make_content(db, FolderPath="/fail_shared.mp3", FileNameL="b.mp3", FileNameS="b.mp3")
    db.session.flush()

    good_dup_file = tmp_path / "good_dup.mp3"
    good_dup_file.write_bytes(b"x")
    fail_shared_file = tmp_path / "fail_shared.mp3"
    fail_shared_file.write_bytes(b"x")

    # Patch FolderPath to point at real on-disk files so the later file-move
    # step (which happens after the DB step this test cares about) has
    # something to work with.
    good_dup.FolderPath = str(good_dup_file)
    fail_dup_a.FolderPath = str(fail_shared_file)
    fail_dup_b.FolderPath = str(fail_shared_file)
    db.session.flush()

    call_count = {"n": 0}
    real_rethread = pruner._rethread_associations

    def flaky_rethread(row, keeper_path, db_arg, emit):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Fails on the *second* row processed for the shared-path group
            # (i.e. after the first row's rethread already ran).
            raise RuntimeError("simulated mid-path failure")
        return real_rethread(row, keeper_path, db_arg, emit)

    monkeypatch.setattr(pruner, "_rethread_associations", flaky_rethread)

    keeper_map = {
        str(good_dup_file): good_keeper.FolderPath,
        str(fail_shared_file): fail_keeper.FolderPath,
    }

    result = pruner.prune_files(
        [str(good_dup_file), str(fail_shared_file)],
        db,
        log=None,
        keeper_map=keeper_map,
    )

    # The unrelated, earlier, fully-successful path must have committed.
    assert db.get_content(FolderPath=str(good_dup_file)).all() == []

    # Both rows sharing the failed path must still exist -- the savepoint
    # rolled back that path's changes in full, not a half-deleted state.
    remaining = db.get_content(FolderPath=str(fail_shared_file)).all()
    assert len(remaining) == 2

    # Only the genuinely successful path counts toward db_removed.
    assert result["db_removed"] == 1
    assert any("fail_shared.mp3" in e or "simulated mid-path failure" in e for e in result["errors"])
