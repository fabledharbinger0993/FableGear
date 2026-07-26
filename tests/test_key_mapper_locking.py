"""
Tests for the DjmdKey get-or-create critical section in key_mapper.py
(audit findings F-05, F-13).

The bug: `_get_or_create_key_row` released `_key_id_cache_lock` after the
in-memory cache check, then did the DB lookup (`db.get_key(...).first()`)
and the create/flush path OUTSIDE any lock. Two threads racing on the same
ScaleName (e.g. parallel SSE import streams sharing a session) could both
miss the cache, both miss the DB row, and both create the same ScaleName
row — a unique-constraint error or duplicate rows with different IDs
(F-05). Separately, `_next_seq` computed max(Seq)+1 with no locking at all,
so two racing creators could compute the same Seq (F-13).

The fix makes the ENTIRE sequence — cache check, DB lookup, Seq
computation, row create, flush, cache populate — one atomic critical
section under `_key_id_cache_lock`.

Run from the repo root:
    python3 -m pytest tests/test_key_mapper_locking.py -v
"""

import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import key_mapper


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_key_cache():
    """The module-level _key_id_cache is process-global — reset around
    every test so tests can't leak state into each other."""
    key_mapper.clear_cache()
    yield
    key_mapper.clear_cache()


# ── Fake DB helpers ──────────────────────────────────────────────────────

class _FakeQuery:
    """Mimics pyrekordbox's chainable query object (.first() / .all())."""

    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _SequentialFakeDB:
    """
    Fake DB for the idempotence test: get_key(...) reflects whatever has
    actually been add()-ed so far — i.e. it returns None on the first
    lookup (row doesn't exist yet) and the created row on any lookup after
    that, exactly like a real DB would.
    """

    def __init__(self):
        self.rows: list = []
        self.added: list = []
        self._next_id = 2000

    def get_key(self, ScaleName=None):
        if ScaleName is None:
            return _FakeQuery(list(self.rows))
        return _FakeQuery([r for r in self.rows if r.ScaleName == ScaleName])

    def generate_unused_id(self, table_cls):
        self._next_id += 1
        return str(self._next_id)

    def add(self, row):
        self.added.append(row)
        self.rows.append(row)

    def flush(self):
        pass


class _RacingFakeDB:
    """
    Fake DB for the contention test.

    get_key(...) always reports "not found," regardless of what has been
    added — this reproduces the TOCTOU window the bug relies on: without a
    lock spanning lookup-through-create, two threads can each complete
    their "does it exist?" check before either has committed its new row,
    so both legitimately see "missing" from the DB's point of view.

    add() sleeps briefly before recording the row. time.sleep() releases
    the GIL, which deterministically gives a second, un-synchronized
    thread a chance to interleave and also reach add() — reproducing the
    race without relying on timing luck. With the fix's lock held across
    the whole critical section, no second thread can ever reach add() at
    all: it blocks at the top of the section and then finds the cache
    already populated.
    """

    def __init__(self, add_sleep: float = 0.05):
        self.added: list = []
        self._add_sleep = add_sleep
        self._id_lock = threading.Lock()
        self._next_id = 3000

    def get_key(self, ScaleName=None):
        return _FakeQuery([])  # always "not found"

    def generate_unused_id(self, table_cls):
        with self._id_lock:
            self._next_id += 1
            return str(self._next_id)

    def add(self, row):
        time.sleep(self._add_sleep)
        self.added.append(row)

    def flush(self):
        pass


# ── 1. Idempotence ─────────────────────────────────────────────────────────

def test_get_or_create_is_idempotent():
    """
    Calling _get_or_create_key_row twice for the same scale name must
    return the same ID and create exactly one row. "Bbm" is a canonical
    Rekordbox ScaleName (Camelot 3A; see config.CAMELOT_TO_RB) used here
    purely as a representative valid scale name.
    """
    db = _SequentialFakeDB()
    scale_name = "Bbm"
    assert scale_name in key_mapper.CANONICAL_SCALE_NAMES

    first_id = key_mapper._get_or_create_key_row(scale_name, db)
    second_id = key_mapper._get_or_create_key_row(scale_name, db)

    assert first_id == second_id
    assert len(db.added) == 1, (
        f"expected exactly one DjmdKey row created, got {len(db.added)}"
    )


# ── 2. Serialized create under contention (F-05, F-13) ─────────────────────

def test_concurrent_create_is_serialized_by_lock():
    """
    Two threads racing _get_or_create_key_row for the same scale name, both
    seeing "row does not exist" from the DB, must still only create ONE
    row and must agree on the same resulting ID.

    Deterministic without sleep-based racing luck: _RacingFakeDB.add()
    sleeps, which yields the GIL. Under the OLD (buggy) code — where the
    lock was released before the DB lookup/create — this reliably produces
    2 added rows (RED). Under the FIXED code, the lock spans the whole
    critical section, so the second thread blocks until the first commits
    and populates the cache, then short-circuits on the cache hit without
    ever reaching add() again (GREEN): exactly 1 added row.
    """
    db = _RacingFakeDB(add_sleep=0.05)
    scale_name = "Bbm"
    assert scale_name in key_mapper.CANONICAL_SCALE_NAMES

    results = [None, None]

    def worker(i):
        results[i] = key_mapper._get_or_create_key_row(scale_name, db)

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive(), "worker thread(s) did not finish"
    assert len(db.added) == 1, (
        f"expected exactly one DjmdKey row created under contention, "
        f"got {len(db.added)} — duplicate ScaleName rows / Seq collision (F-05, F-13)"
    )
    assert results[0] == results[1] == db.added[0].ID
