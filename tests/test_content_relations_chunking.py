"""
Regression: get_content_with_relations() must survive libraries larger than
SQLite's host-parameter limit.

The original implementation built one ``IN (?,?,...)`` clause sized to the
entire fetched track set for the fg_cue and fg_beatgrid lookups (and the
explicit ``content_ids`` path did the same for the fg_content fetch itself).
SQLITE_MAX_VARIABLE_NUMBER is a compile-time knob — classically 999 — so a
library with more than ~999 tracks crashed with
``sqlite3.OperationalError: too many SQL variables``.

TRACK_COUNT is set to 1500, which is above the classic 999 limit and spans
three 500-row chunks, exercising the loop boundary in both code paths without
making the fixture slow in CI.

Run from the repo root:
    python3 -m pytest tests/test_content_relations_chunking.py -v
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.database import FableGearDatabase
from fablegear_database.schema import DatabaseConfig

# Above the classic 999-variable limit so unchunked code raises
# OperationalError on the most common SQLite builds. 1500 rows also
# span three 500-row chunks, exercising the loop boundary without
# making the fixture slow in CI.
TRACK_COUNT = 1_500


@pytest.fixture(scope="module")
def big_db(tmp_path_factory):
    """A database holding TRACK_COUNT tracks, each with a known number of
    cues ((i % 3) + 1) and beatgrid markers ((i % 2) + 1).

    Setup uses raw SQL in a single transaction purely for fixture speed —
    the code under test is get_content_with_relations(), not the inserts.
    Module-scoped: building 1500 rows once is enough for both tests.
    """
    db_path = tmp_path_factory.mktemp("chunking") / "fablegear.db"
    db = FableGearDatabase(DatabaseConfig(db_path=db_path))

    with db.transaction() as conn:
        cursor = conn.cursor()
        for i in range(TRACK_COUNT):
            cursor.execute(
                """
                INSERT INTO fg_content (file_path, file_name, file_size, title, artist)
                VALUES (?, ?, ?, ?, ?)
                """,
                (f"/music/track_{i:05d}.aiff", f"track_{i:05d}.aiff", 1024,
                 f"Track {i}", "Synthetic Artist"),
            )
            content_id = cursor.lastrowid
            for c in range(i % 3 + 1):
                cursor.execute(
                    """
                    INSERT INTO fg_cue (content_id, kind, slot, in_msec)
                    VALUES (?, 1, ?, ?)
                    """,
                    (content_id, c, (c + 1) * 1000),
                )
            for b in range(i % 2 + 1):
                cursor.execute(
                    """
                    INSERT INTO fg_beatgrid (content_id, beat_number, time_msec, bpm)
                    VALUES (?, ?, ?, 128.0)
                    """,
                    (content_id, b, b * 469),
                )

    return db


def _assert_relations_correct(tracks):
    assert len(tracks) == TRACK_COUNT
    for t in tracks:
        i = int(t.file_name.removeprefix("track_").removesuffix(".aiff"))
        assert len(t.cues) == i % 3 + 1, (
            f"{t.file_name}: expected {i % 3 + 1} cues, got {len(t.cues)}"
        )
        assert len(t.beatgrid) == i % 2 + 1, (
            f"{t.file_name}: expected {i % 2 + 1} beatgrid rows, got {len(t.beatgrid)}"
        )
        # Chunking must not break the per-track ORDER BY contracts.
        in_msecs = [c.in_msec for c in t.cues]
        assert in_msecs == sorted(in_msecs)
        beat_nums = [g.beat_number for g in t.beatgrid]
        assert beat_nums == sorted(beat_nums)


def test_all_records_path_survives_param_limit(big_db):
    """content_ids=None: the cue/beatgrid IN-clauses must be chunked."""
    tracks = big_db.get_content_with_relations(content_ids=None)
    _assert_relations_correct(tracks)


def test_explicit_ids_path_survives_param_limit(big_db):
    """An explicit ID list longer than the variable limit: the fg_content
    fetch itself must be chunked too, not just the relation lookups."""
    all_ids = [t.id for t in big_db.get_all_content(limit=TRACK_COUNT)]
    assert len(all_ids) == TRACK_COUNT
    tracks = big_db.get_content_with_relations(content_ids=all_ids)
    _assert_relations_correct(tracks)
