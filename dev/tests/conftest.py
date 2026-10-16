"""
Shared pytest configuration.

Tests that exercise the rekordbox write paths (relocate, bidirectional sync,
the DB fixture) go through pyrekordbox, which refuses to commit while the
Rekordbox desktop app is running:

    RuntimeError: Rekordbox is running. Please close Rekordbox before commiting changes.

That guard is correct in production but makes the test suite depend on whether
the developer happens to have Rekordbox open. Whether a write path *builds the
right change* is independent of the running-process check, so we neutralize the
check for the whole suite: every test runs as if Rekordbox is not running.

There are two independent guards to neutralize:
  * pyrekordbox's own, read through get_rekordbox_pid (db6.database imports it
    by name, so both the util and the imported reference are patched);
  * FableGear's, db_connection.rekordbox_is_running(), enforced by open_db on
    every write.
"""
import pytest


@pytest.fixture(autouse=True)
def _rekordbox_not_running(monkeypatch):
    import pyrekordbox.utils as _utils

    monkeypatch.setattr(_utils, "get_rekordbox_pid", lambda *a, **k: 0, raising=False)
    try:
        import pyrekordbox.db6.database as _db6
    except Exception:
        _db6 = None
    if _db6 is not None:
        monkeypatch.setattr(_db6, "get_rekordbox_pid", lambda *a, **k: 0, raising=False)

    # FableGear's own write guard (db_connection.open_db → rekordbox_is_running).
    try:
        import db_connection as _dbc
    except Exception:
        _dbc = None
    if _dbc is not None:
        monkeypatch.setattr(_dbc, "rekordbox_is_running", lambda *a, **k: False, raising=False)
