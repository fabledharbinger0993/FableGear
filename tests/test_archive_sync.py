"""
Tests for fablegear_database.archive_sync (docs/archive_first_architecture.md
§3 DB home + sync layer).

Covers: hydrate/sync round-trip, offline (drive-not-mounted) startup,
atomic-rename "eject safety" (a corrupt/partial temp file never becomes
"latest"), and the local-corrupt -> savepoint conflict path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _make_db(path: Path, *, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO t (v) VALUES (?)", (f"row{i}",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def archive_env(tmp_path, monkeypatch):
    """Point config.ARCHIVE_ROOT/ARCHIVE_ENABLED/SAVEPOINTS_DIR at a tmp
    drive, and reload archive_sync so its module-level imports pick up the
    patched config on every call (it imports config lazily inside each
    function, so no reload is actually required — kept for clarity)."""
    import config

    archive_root = tmp_path / "Drive" / "FableGear Archive"
    monkeypatch.setattr(config, "ARCHIVE_ENABLED", True)
    monkeypatch.setattr(config, "ARCHIVE_ROOT", archive_root)
    monkeypatch.setattr(config, "SAVEPOINTS_DIR", archive_root / "Savepoints")
    # The "drive" is just tmp_path/Drive; create it so archive_drive_mounted()
    # (which checks ARCHIVE_ROOT.parent.exists()) sees it as mounted.
    (tmp_path / "Drive").mkdir(parents=True, exist_ok=True)
    return {"root": archive_root, "local": tmp_path / "local" / "fablegear.db"}


def test_archive_drive_mounted_false_when_disabled(tmp_path, monkeypatch):
    import config
    from fablegear_database import archive_sync

    monkeypatch.setattr(config, "ARCHIVE_ENABLED", False)
    assert archive_sync.archive_drive_mounted() is False
    assert archive_sync.archive_db_path() is None


def test_archive_drive_mounted_false_when_unmounted(tmp_path, monkeypatch):
    import config
    from fablegear_database import archive_sync

    missing_root = tmp_path / "not_mounted" / "FableGear Archive"
    monkeypatch.setattr(config, "ARCHIVE_ENABLED", True)
    monkeypatch.setattr(config, "ARCHIVE_ROOT", missing_root)
    assert archive_sync.archive_drive_mounted() is False


def test_integrity_check_missing_and_empty_and_valid(tmp_path):
    from fablegear_database import archive_sync

    missing = tmp_path / "missing.db"
    assert archive_sync.integrity_check(missing) is False

    empty = tmp_path / "empty.db"
    empty.touch()
    assert archive_sync.integrity_check(empty) is False

    valid = tmp_path / "valid.db"
    _make_db(valid)
    assert archive_sync.integrity_check(valid) is True


def test_sync_skips_when_drive_not_mounted(tmp_path, monkeypatch):
    import config
    from fablegear_database import archive_sync

    monkeypatch.setattr(config, "ARCHIVE_ENABLED", True)
    monkeypatch.setattr(config, "ARCHIVE_ROOT", tmp_path / "gone" / "FableGear Archive")
    local = tmp_path / "local" / "fablegear.db"
    _make_db(local)

    result = archive_sync.sync_db_to_archive(local_path=local)
    assert result.ok is True
    assert result.action == "skipped"


def test_sync_writes_archive_copy_with_checksum_meta(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    _make_db(local)

    result = archive_sync.sync_db_to_archive(local_path=local)
    assert result.ok is True
    assert result.action == "synced"

    a_path = archive_sync.archive_db_path()
    assert a_path.exists()
    assert archive_sync.integrity_check(a_path) is True

    meta = archive_sync._read_meta(archive_sync.archive_db_meta_path())
    assert meta["checksum_sha256"] == archive_sync._checksum(local)
    assert not archive_sync.archive_db_prev_path().exists()  # nothing to rotate yet


def test_sync_rotates_previous_generation_add_only(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    _make_db(local, rows=1)
    first = archive_sync.sync_db_to_archive(local_path=local)
    assert first.action == "synced"
    first_checksum = first.detail["checksum"]

    _make_db(local, rows=5)  # mutate local, then sync again
    second = archive_sync.sync_db_to_archive(local_path=local)
    assert second.action == "synced"

    prev_path = archive_sync.archive_db_prev_path()
    a_path = archive_sync.archive_db_path()
    assert prev_path.exists()
    assert archive_sync._checksum(prev_path) == first_checksum
    assert archive_sync._checksum(a_path) == second.detail["checksum"]
    assert first_checksum != second.detail["checksum"]


def test_sync_aborts_on_corrupt_local_leaves_archive_untouched(archive_env):
    """Eject-safety / 'never promote corruption' guarantee: if the live local
    DB fails integrity_check, sync must abort *before* touching the archive
    at all — a prior good archive copy is left exactly as it was."""
    from fablegear_database import archive_sync

    local = archive_env["local"]
    _make_db(local, rows=2)
    good = archive_sync.sync_db_to_archive(local_path=local)
    assert good.action == "synced"
    good_checksum = good.detail["checksum"]

    local.write_bytes(b"not a sqlite file")  # corrupt the working copy in place
    result = archive_sync.sync_db_to_archive(local_path=local)

    assert result.ok is False
    assert result.action == "error"
    a_path = archive_sync.archive_db_path()
    assert archive_sync._checksum(a_path) == good_checksum  # untouched
    assert not archive_sync.archive_db_prev_path().exists()  # no rotation happened


def test_hydrate_skips_when_local_healthy(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    _make_db(local)
    result = archive_sync.hydrate_working_copy_from_archive(local_path=local)
    assert result.ok is True
    assert result.action == "skipped"
    assert result.reason == "local working copy is healthy"


def test_hydrate_restores_from_archive_when_local_missing(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    seed = archive_env["local"].parent / "seed.db"
    _make_db(seed, rows=7)
    archive_sync.sync_db_to_archive(local_path=seed)
    assert not local.exists()

    result = archive_sync.hydrate_working_copy_from_archive(local_path=local)
    assert result.ok is True
    assert result.action == "hydrated"
    assert local.exists()
    assert archive_sync._checksum(local) == archive_sync._checksum(seed)


def test_hydrate_savepoints_corrupt_local_before_restoring(archive_env):
    """Conflict path: a damaged local file is never deleted outright — it's
    moved aside to a timestamped Savepoint, then a good archive copy takes
    its place."""
    import config
    from fablegear_database import archive_sync

    local = archive_env["local"]
    seed = archive_env["local"].parent / "seed.db"
    _make_db(seed, rows=4)
    archive_sync.sync_db_to_archive(local_path=seed)

    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"corrupted garbage")

    result = archive_sync.hydrate_working_copy_from_archive(local_path=local)
    assert result.ok is True
    assert result.action == "hydrated"
    assert archive_sync.integrity_check(local) is True

    savepoint = Path(result.detail["savepoint"])
    assert savepoint.exists()
    assert savepoint.read_bytes() == b"corrupted garbage"
    assert savepoint.parent == config.SAVEPOINTS_DIR


def test_hydrate_gives_up_gracefully_when_both_copies_bad(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"bad local")
    # No archive copy exists at all yet.

    result = archive_sync.hydrate_working_copy_from_archive(local_path=local)
    assert result.ok is False
    assert result.action == "skipped"
    # Nothing destructive happened — local file (bad as it is) is untouched.
    assert local.read_bytes() == b"bad local"


def test_seed_archive_from_existing_local_only_when_archive_empty(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    _make_db(local, rows=9)

    first = archive_sync.seed_archive_from_existing_local(local_path=local)
    assert first.action == "seeded"
    assert archive_sync.archive_db_path().exists()

    # A second call must not re-seed / overwrite — archive already has a copy.
    _make_db(local, rows=1)  # mutate local so a re-seed would be detectable
    second = archive_sync.seed_archive_from_existing_local(local_path=local)
    assert second.action == "skipped"
    assert second.reason == "archive already has a Database/fablegear.db"


def test_startup_sync_check_seeds_on_first_run(archive_env):
    from fablegear_database import archive_sync

    local = archive_env["local"]
    _make_db(local, rows=2)

    result = archive_sync.startup_sync_check(local_path=local)
    assert result.action == "seeded"
    assert archive_sync.archive_db_path().exists()


def test_startup_sync_check_hydrates_before_seeding(archive_env):
    """If local is missing/corrupt *and* the archive already has a good
    copy, startup should hydrate — not attempt to seed (there's nothing
    healthy locally to seed from anyway)."""
    from fablegear_database import archive_sync

    local = archive_env["local"]
    seed = archive_env["local"].parent / "seed.db"
    _make_db(seed, rows=6)
    archive_sync.sync_db_to_archive(local_path=seed)
    assert not local.exists()

    result = archive_sync.startup_sync_check(local_path=local)
    assert result.action == "hydrated"
    assert local.exists()


def test_startup_sync_check_noop_when_drive_unmounted(tmp_path, monkeypatch):
    import config
    from fablegear_database import archive_sync

    monkeypatch.setattr(config, "ARCHIVE_ENABLED", True)
    monkeypatch.setattr(config, "ARCHIVE_ROOT", tmp_path / "gone" / "FableGear Archive")
    local = tmp_path / "local" / "fablegear.db"
    _make_db(local)

    result = archive_sync.startup_sync_check(local_path=local)
    assert result.ok is True
    assert result.action == "skipped"
