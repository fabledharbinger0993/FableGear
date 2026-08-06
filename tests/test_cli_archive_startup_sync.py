"""
Regression guard: the CLI must hydrate from the archive before running.

The defect this guards: app.py (the GUI) calls
fablegear_database.archive_sync.startup_sync_check() at startup, restoring
the local working copy (~/.fablegear/fablegear.db) from the archive if it's
missing or corrupt. cli.py is a separate entry point and never did this --
every command that touches FableGearDatabase() opened the local path directly
with create=True and no archive awareness at all.

On a machine where the local working copy is missing (fresh install, cleared
cache, new machine) but the archive drive holds the real, healthy library,
that silently created a brand-new EMPTY database and every CLI command ran
against zero data -- no error, no warning, nothing distinguishing "empty
library" from "your real library just failed to load".

These tests run cli.py as a REAL subprocess with HOME pointed at a scratch
directory. That is deliberate, not just thorough: several archive_sync
functions default their `local_path` parameter to DEFAULT_DB_PATH, which
Python binds at function-definition time (module import), not at call time.
Reassigning the module attribute in-process after import does not change
already-defined functions' defaults -- only a fresh interpreter (a real
subprocess) with HOME set before any FableGear module is imported gives a
faithful "fresh machine" state.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=REPO_ROOT,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        capture_output=True, text=True, timeout=60,
    )


def _seed_fake_machine(tmp_path: Path) -> tuple[Path, Path]:
    """Build a real ~/.fablegear config + a real, populated archive drive
    under a scratch HOME, using the actual FableGear code (not hand-built
    fixtures) so this exercises the real schema and the real archive_sync
    write path. Returns (home_dir, archive_root)."""
    home = tmp_path / "fakehome"
    archive_drive = tmp_path / "drive"
    (home / ".fablegear").mkdir(parents=True)
    (archive_drive).mkdir(parents=True)

    cfg = {
        "local_db": "/nonexistent/master.db",
        "device_db": "/nonexistent/master.db",
        "music_root": str(archive_drive / "Music"),
        "backup_dir": str(archive_drive / "FableGear Archive" / "Savepoints"),
        "archive_mode": "auto",
        "custom_archive_dir": "",
        "snapshot_cadence": "monthly",
        "snapshot_include_master_db": False,
        "target_lufs": -8.0,
        "lufs_tolerance": 0.5,
        "excluded_dirs": [],
        "acoustid_api_key": "",
        "mode": "rural",
    }
    (home / ".fablegear" / "config.json").write_text(json.dumps(cfg))

    # Build the "real library" and push it to the archive, all via a
    # subprocess under this same fake HOME so nothing touches the real
    # ~/.fablegear or leaks module-level path constants from this process.
    setup_script = (
        "import sqlite3, sys; sys.path.insert(0, '.');"
        "from fablegear_database.database import FableGearDatabase;"
        "db = FableGearDatabase();"
        "conn = sqlite3.connect(db.config.db_path);"
        "conn.execute(\"INSERT INTO fg_playlist (name) VALUES ('My Real Gig Crates')\");"
        "conn.commit(); conn.close();"
        "from fablegear_database.archive_sync import sync_db_to_archive;"
        "r = sync_db_to_archive();"
        "assert r.ok and r.action == 'synced', r"
    )
    setup = subprocess.run(
        [sys.executable, "-c", setup_script],
        cwd=REPO_ROOT, env={"HOME": str(home)},
        capture_output=True, text=True, timeout=60,
    )
    assert setup.returncode == 0, f"fixture setup failed:\n{setup.stdout}\n{setup.stderr}"

    archive_root = archive_drive / "FableGear Archive"
    assert (archive_root / "Database" / "fablegear.db").exists(), "archive was not seeded"

    # The fresh-machine precondition: local working copy absent.
    for p in (home / ".fablegear").glob("fablegear.db*"):
        p.unlink()
    assert not (home / ".fablegear" / "fablegear.db").exists()

    return home, archive_root


def test_cli_hydrates_missing_local_db_from_archive(tmp_path):
    home, _archive_root = _seed_fake_machine(tmp_path)  # returned for parity with the other test; unused here

    result = _run_cli("playlist", "list", home=home)

    assert result.returncode == 0, result.stderr
    assert "My Real Gig Crates" in result.stderr, (
        "the CLI must see the archive's real data on a fresh machine, not an "
        f"empty database.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # And the local working copy should now exist, hydrated from the archive --
    # not a fresh empty schema.
    local_db = home / ".fablegear" / "fablegear.db"
    assert local_db.exists()
    conn = sqlite3.connect(f"file:{local_db}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute("SELECT name FROM fg_playlist")]
    conn.close()
    assert names == ["My Real Gig Crates"]


def test_cli_leaves_a_healthy_local_db_alone(tmp_path):
    """The flip side of the same guard: hydration must never clobber a
    healthy local working copy, even when the archive also has data --
    "local wins" is the documented rule (archive_first_architecture.md §3.2)."""
    home, _archive_root = _seed_fake_machine(tmp_path)  # unused; see helper docstring

    # Give this machine its OWN healthy local library, distinct from the
    # archive's, by running a command that touches FableGearDatabase() once
    # (recreates a fresh, valid local db) then tagging it with a marker row.
    conn_setup = (
        "import sqlite3, sys; sys.path.insert(0, '.');"
        "from fablegear_database.database import FableGearDatabase;"
        "db = FableGearDatabase();"
        "conn = sqlite3.connect(db.config.db_path);"
        "conn.execute(\"INSERT INTO fg_playlist (name) VALUES ('This Machines Own Crates')\");"
        "conn.commit(); conn.close()"
    )
    setup = subprocess.run(
        [sys.executable, "-c", conn_setup],
        cwd=REPO_ROOT, env={"HOME": str(home)},
        capture_output=True, text=True, timeout=60,
    )
    assert setup.returncode == 0, setup.stderr

    result = _run_cli("playlist", "list", home=home)

    assert result.returncode == 0, result.stderr
    assert "This Machines Own Crates" in result.stderr
    assert "My Real Gig Crates" not in result.stderr, (
        "a healthy local working copy must never be overwritten by the "
        "archive, even though the archive also has data"
    )
