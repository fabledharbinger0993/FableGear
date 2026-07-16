"""
Tests for relocate_directory's only_missing scoping (default behavior).

Context: on 2026-07-14 a live relocate run against a 244k-track library
repointed 37,796 FolderPath values when only ~6,990 tracks were actually
missing their files — 99.8% of the changes touched healthy tracks whose
files were present on disk the whole time, because relocate_directory
selected candidates by string-prefix match on old_root alone. The DB had
to be restored from the pre-run savepoint.

These tests pin the fix: by default (only_missing=True), rows whose
FolderPath still resolves to a real file are never candidates — never
matched, never rewritten, never journaled. only_missing=False restores
the old include-everything behavior for deliberate mid-migration use.
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import tables as rb_tables
from sqlalchemy import create_engine

from relocator import relocate_directory


@pytest.fixture
def db(tmp_path):
    """A real, unencrypted pyrekordbox-schema database (unlock=False test path)."""
    db_path = tmp_path / "master.db"
    engine = create_engine(f"sqlite:///{db_path}")
    # Relax the ORM's inferred NOT NULLs to match real-world master.db data
    # shapes (see tests/test_pruner_associations.py for the full rationale).
    for table in rb_tables.Base.metadata.tables.values():
        for column in table.columns:
            if not column.primary_key:
                column.nullable = True
    rb_tables.Base.metadata.create_all(engine)
    engine.dispose()

    handle = Rekordbox6Database(path=str(db_path), unlock=False)
    # pyrekordbox tracks a USN (update sequence number) on every Djmd* write
    # via the agentRegistry 'localUpdateCount' row — real databases always
    # have it; a from-scratch schema doesn't.
    # Every DateTime column must be non-None: pyrekordbox's custom DateTime
    # decorator calls astimezone() on bind with no None guard.
    now = datetime.now(timezone.utc)
    handle.session.add(
        rb_tables.AgentRegistry(
            registry_id="localUpdateCount", int_1=1,
            date_1=now, date_2=now, created_at=now, updated_at=now,
        )
    )
    handle.session.flush()
    yield handle
    handle.close()


def _add_track(db, folder_path: str):
    content = rb_tables.DjmdContent(
        ID=str(uuid.uuid4()),
        FolderPath=folder_path,
        FileNameL=Path(folder_path).name,
        FileNameS=Path(folder_path).name,
        Title=Path(folder_path).stem,
        # update_content_path unconditionally calls read_anlz_files, which does
        # AnalysisDataPath.strip() and then lists the dir — real Rekordbox rows
        # always populate this. Give each row an existing, empty ANLZ dir.
        AnalysisDataPath=f"/PIONEER/USBANLZ/{uuid.uuid4()}/ANLZ0000.DAT",
    )
    anlz_dir = db.share_directory / Path(content.AnalysisDataPath.strip("/")).parent
    anlz_dir.mkdir(parents=True, exist_ok=True)
    db.session.add(content)
    db.session.flush()
    return content


def _make_library(tmp_path):
    """Two roots: old_root with one healthy file, new_root holding the moved file."""
    old_root = tmp_path / "old_drive"
    new_root = tmp_path / "new_drive"
    (old_root / "albums").mkdir(parents=True)
    (new_root / "albums").mkdir(parents=True)

    healthy = old_root / "albums" / "healthy_track.mp3"
    healthy.write_bytes(b"HEALTHY" * 1000)

    # The "moved" file exists only under new_root at the same relative path.
    moved_new = new_root / "albums" / "moved_track.mp3"
    moved_new.write_bytes(b"MOVED" * 1000)

    return old_root, new_root, healthy, moved_new


def test_healthy_tracks_are_never_touched_by_default(db, tmp_path):
    old_root, new_root, healthy, moved_new = _make_library(tmp_path)
    healthy_row = _add_track(db, str(healthy))
    missing_row = _add_track(db, str(old_root / "albums" / "moved_track.mp3"))

    results = relocate_directory(old_root, new_root, db)

    # Only the broken row is a candidate — the healthy row must not even
    # appear in the results, let alone be rewritten.
    assert len(results) == 1
    assert results[0].content_id == str(missing_row.ID)
    assert results[0].success
    assert results[0].new_path == str(moved_new)
    assert healthy_row.FolderPath == str(healthy)


def test_all_healthy_means_no_op(db, tmp_path):
    old_root, new_root, healthy, _ = _make_library(tmp_path)
    healthy_row = _add_track(db, str(healthy))

    results = relocate_directory(old_root, new_root, db)

    assert results == []
    assert healthy_row.FolderPath == str(healthy)


def test_include_existing_restores_old_behavior(db, tmp_path):
    old_root, new_root, healthy, _ = _make_library(tmp_path)
    # Put an identical copy of the healthy file at the same relative path
    # under new_root — the mid-migration scenario (both copies present).
    mirrored = new_root / "albums" / "healthy_track.mp3"
    mirrored.write_bytes(healthy.read_bytes())
    healthy_row = _add_track(db, str(healthy))

    results = relocate_directory(old_root, new_root, db, only_missing=False)

    assert len(results) == 1
    assert results[0].success
    assert healthy_row.FolderPath == str(mirrored)


def test_fuzzy_cannot_reach_healthy_tracks_by_default(db, tmp_path):
    """The 2026-07-14 failure mode: a healthy track whose stem loosely
    resembles a different file on the new drive must not be fuzzy-matched,
    because it is filtered out before matching even runs."""
    old_root, new_root, healthy, _ = _make_library(tmp_path)
    # A near-miss stem on the new drive that fuzzy (cutoff 0.90) would accept.
    lookalike = new_root / "albums" / "healthy_track1.mp3"
    lookalike.write_bytes(b"DIFFERENT CONTENT" * 1000)
    healthy_row = _add_track(db, str(healthy))

    results = relocate_directory(old_root, new_root, db)

    assert results == []
    assert healthy_row.FolderPath == str(healthy)
