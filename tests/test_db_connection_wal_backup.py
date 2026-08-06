"""
Regression guard: master.db backups must capture WAL-only data.

The defect this guards: master.db is opened in WAL mode. A committed
transaction can sit in the -wal sidecar, un-checkpointed into the main file,
for as long as the process holding it stays open — and if that process is
killed rather than cleanly closed (a crash, a force-quit), the -wal/-shm can
survive on disk indefinitely. _backup_db() copied only the main .db file, so
a backup taken while a real -wal held real committed data captured a database
missing that data entirely — reproduced directly: a bare copy of the main
file opened standalone as "no such table", not stale data, no schema at all.

_backup_db() now copies every sidecar that exists alongside the main file,
and verifies the main file's backup size against the source.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def wal_db_with_uncheckpointed_data(tmp_path):
    """A real WAL-mode sqlite db where a whole table exists only in the -wal
    sidecar — an unclean exit (no close(), no checkpoint), matching what a
    killed Rekordbox process leaves on disk."""
    db_path = tmp_path / "master.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('uncheckpointed-row')")
    conn.commit()
    del conn  # unclean exit: no close(), no checkpoint -- WAL/SHM remain

    assert (tmp_path / "master.db-wal").exists(), "fixture must actually produce a WAL file"
    return db_path


def test_backup_captures_data_that_only_lives_in_the_wal(wal_db_with_uncheckpointed_data, tmp_path, monkeypatch):
    import db_connection

    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(db_connection, "_get_config", lambda: (backup_dir, None, None))

    result = db_connection._backup_db(wal_db_with_uncheckpointed_data)

    # The sidecar must have been copied alongside the main backup file.
    assert Path(str(result) + "-wal").exists(), "backup must include the -wal sidecar"

    conn = sqlite3.connect(f"file:{result}?mode=ro", uri=True)
    rows = [r[0] for r in conn.execute("SELECT k FROM t")]
    conn.close()
    assert rows == ["uncheckpointed-row"], (
        "the backup must reflect the database's real content, including data "
        "that was only ever committed to the WAL"
    )


def test_backup_of_a_clean_checkpointed_db_has_no_stale_sidecars(tmp_path, monkeypatch):
    """A cleanly-closed db (WAL already checkpointed and removed by SQLite)
    must not somehow gain sidecar files in its backup."""
    import db_connection

    db_path = tmp_path / "master.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('clean-row')")
    conn.commit()
    conn.close()  # clean close -- SQLite auto-checkpoints, removes -wal/-shm
    assert not (tmp_path / "master.db-wal").exists(), "fixture precondition"

    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(db_connection, "_get_config", lambda: (backup_dir, None, None))

    result = db_connection._backup_db(db_path)
    assert not Path(str(result) + "-wal").exists()
    assert not Path(str(result) + "-shm").exists()


def test_backup_raises_on_truncated_copy(tmp_path, monkeypatch):
    """A backup whose main file doesn't match the source size in bytes must
    raise rather than silently return a truncated 'backup'."""
    import db_connection

    db_path = tmp_path / "master.db"
    db_path.write_bytes(b"x" * 10_000)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(db_connection, "_get_config", lambda: (backup_dir, None, None))

    def _truncating_copy2(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes()[:100])  # simulate a short copy

    monkeypatch.setattr(shutil, "copy2", _truncating_copy2)

    with pytest.raises(RuntimeError, match=r"[Ss]ize mismatch"):
        db_connection._backup_db(db_path)


def test_two_backups_of_the_same_source_never_collide_on_filename(tmp_path, monkeypatch):
    """Regression: _backup_db's timestamp used second-level precision only,
    so two backups of the same source taken within the same wall-clock
    second produced the SAME filename -- the second shutil.copy2 silently
    overwrote the first, destroying whatever restore point it represented.
    Realistic trigger: a manual savepoint immediately followed by an
    automatic one (or vice versa), or two quick write_db() calls."""
    import db_connection

    db_path = tmp_path / "master.db"
    db_path.write_bytes(b"first-state")
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(db_connection, "_get_config", lambda: (backup_dir, None, None))

    first = db_connection._backup_db(db_path)

    db_path.write_bytes(b"second-state-moments-later")
    second = db_connection._backup_db(db_path)

    assert first != second, "two backups taken moments apart must not share a filename"
    assert first.exists(), "the first backup must survive a second backup of the same source"
    assert first.read_bytes() == b"first-state"
    assert second.read_bytes() == b"second-state-moments-later"
