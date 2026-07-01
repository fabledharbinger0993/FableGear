"""
Regression guard for archive persistence wiring.

These tests run each Chop Shop tool through its real CLI caller and fail if the
path does not append at least one row to fg_processing_log.

Scope covered (via cli.py command handlers):
- dead_file_scanner
- relocator
- duplicate_detector
- pruner
- library_organizer
- novelty_scanner
- renamer

The tool internals are stubbed to keep tests deterministic and fast; the
contract under test is caller -> tool archive wiring and persistence logging.
"""

from __future__ import annotations

import contextlib
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli
from fablegear_database.database import FableGearDatabase
from fablegear_database.schema import DatabaseConfig


class _DummyDb:
    def commit(self):
        return None

    def rollback(self):
        return None


@contextlib.contextmanager
def _dummy_db_ctx(_path):
    yield _DummyDb()


def _install_fake_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs) -> None:
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)


def _archive_count(archive: FableGearDatabase) -> int:
    return archive.count_operations()


def _assert_archive_incremented(archive: FableGearDatabase, before: int) -> None:
    after = _archive_count(archive)
    assert after > before, "expected at least one fg_processing_log row to be appended"


@pytest.fixture
def wired_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FableGearDatabase:
    archive = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))
    monkeypatch.setattr(cli, "_archive", lambda: archive)
    monkeypatch.setattr(cli, "LOCAL_DB", tmp_path / "local.db")
    return archive


def test_cli_dead_files_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    src.mkdir()

    def _scan_dead_files(_roots, db_paths=None, progress_cb=None, archive=None):
        assert archive is not None
        archive.log_operation("dead_file_scan", metadata={"caller": "cmd_dead_files"})
        return SimpleNamespace(
            total_scanned=0,
            dead_count=0,
            dead_files=[],
            db_paths_used=[],
            summary=lambda: "dead file summary",
        )

    _install_fake_module(monkeypatch, "dead_file_scanner", scan_dead_files=_scan_dead_files)

    before = _archive_count(wired_archive)
    cli.cmd_dead_files(Namespace(path=str(src), also_scan=[]))
    _assert_archive_incremented(wired_archive, before)


def test_cli_relocate_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    new_root = tmp_path / "new_root"
    new_root.mkdir()

    def _relocate_directory(_old_root, _new_root, _db, archive=None):
        assert archive is not None
        archive.log_operation("relocate_batch", metadata={"caller": "cmd_relocate"})
        return [SimpleNamespace(strategy="exact", success=True)]

    _install_fake_module(monkeypatch, "relocator", relocate_directory=_relocate_directory)
    _install_fake_module(monkeypatch, "db_connection", write_db=_dummy_db_ctx)

    before = _archive_count(wired_archive)
    cli.cmd_relocate(Namespace(old_root=str(tmp_path / "old_root"), new_root=str(new_root)))
    _assert_archive_incremented(wired_archive, before)


def test_cli_duplicates_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "dupes.csv"

    def _scan_duplicates(_root, max_workers=1, match_mode="exact", fuzzy_threshold=0.85, archive=None, **_kwargs):
        assert archive is not None
        archive.log_operation("duplicate_scan", metadata={"caller": "cmd_duplicates"})
        return SimpleNamespace(groups=[], unique_in_trash=[])

    _install_fake_module(
        monkeypatch,
        "duplicate_detector",
        scan_duplicates=_scan_duplicates,
        write_csv_report=lambda *_a, **_k: None,
        write_trash_rescue_report=lambda *_a, **_k: None,
    )

    before = _archive_count(wired_archive)
    cli.cmd_duplicates(
        Namespace(
            path=[str(src)],
            output=str(out),
            workers=1,
            match_mode="exact",
            fuzzy_threshold=0.85,
        )
    )
    _assert_archive_incremented(wired_archive, before)


def test_cli_prune_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    csv_path = tmp_path / "duplicate_report.csv"
    csv_path.write_text("placeholder\n", encoding="utf-8")

    keeper = SimpleNamespace(file_path=str(tmp_path / "keep.mp3"))
    remove = SimpleNamespace(file_path=str(tmp_path / "remove.mp3"))
    group = SimpleNamespace(remove_candidates=[remove], keep=keeper, keep_in_trash=False)

    def _load_report(_csv_path, _db):
        return [group]

    def _prune_files(_paths, _db, log=None, permanent=False, keeper_map=None, archive=None):
        assert archive is not None
        archive.log_operation("prune_batch", metadata={"caller": "cmd_prune"})
        return {
            "db_removed": 1,
            "files_moved": 1,
            "skipped": 0,
            "errors": [],
            "trash_dir": str(tmp_path / "trash"),
            "playlists_rethreaded": 0,
        }

    _install_fake_module(monkeypatch, "pruner", load_report=_load_report, prune_files=_prune_files)
    _install_fake_module(monkeypatch, "db_connection", read_db=_dummy_db_ctx, write_db=_dummy_db_ctx)

    before = _archive_count(wired_archive)
    cli.cmd_prune(Namespace(csv_path=str(csv_path), dry_run=False, permanent=False))
    _assert_archive_incremented(wired_archive, before)


def test_cli_organize_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    def _organize_library(_sources, _target, mode="assimilate", dry_run=True, max_workers=1, mix_threshold_sec=900, archive=None):
        assert archive is not None
        archive.log_operation("organize_batch", metadata={"caller": "cmd_organize"})
        return []

    _install_fake_module(monkeypatch, "library_organizer", organize_library=_organize_library)

    before = _archive_count(wired_archive)
    cli.cmd_organize(
        Namespace(
            source=str(source),
            also_scan=[],
            target=str(target),
            mode="assimilate",
            no_dry_run=True,
            workers=1,
            mix_threshold=15,
        )
    )
    _assert_archive_incremented(wired_archive, before)


def test_cli_novelty_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()

    def _scan_novel(_source, _dest, dry_run=True, max_workers=1, match_mode="fingerprint", archive=None):
        assert archive is not None
        archive.log_operation("novelty_scan", metadata={"caller": "cmd_novelty"})
        return SimpleNamespace(total_src=0, dest_index_size=0, novel=[], present=[], errors=[])

    _install_fake_module(monkeypatch, "novelty_scanner", scan_novel=_scan_novel)

    before = _archive_count(wired_archive)
    cli.cmd_novelty(
        Namespace(
            source=str(source),
            also_scan=[],
            dest=str(dest),
            no_dry_run=True,
            workers=1,
            match_mode="fingerprint",
        )
    )
    _assert_archive_incremented(wired_archive, before)


def test_cli_rename_logs_archive(wired_archive: FableGearDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    source.mkdir()

    def _rename_directory(_root, db=None, dry_run=True, max_workers=1, archive=None):
        assert archive is not None
        archive.log_operation("rename_batch", metadata={"caller": "cmd_rename"})
        return []

    _install_fake_module(monkeypatch, "renamer", rename_directory=_rename_directory)
    _install_fake_module(monkeypatch, "db_connection", write_db=_dummy_db_ctx)

    before = _archive_count(wired_archive)
    cli.cmd_rename(
        Namespace(
            path=str(source),
            also_scan=[],
            no_dry_run=True,
            workers=1,
        )
    )
    _assert_archive_incremented(wired_archive, before)


def test_cli_duplicates_fails_loud_when_archive_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    src.mkdir()

    _install_fake_module(
        monkeypatch,
        "duplicate_detector",
        scan_duplicates=lambda *_a, **_k: SimpleNamespace(groups=[], unique_in_trash=[]),
        write_csv_report=lambda *_a, **_k: None,
        write_trash_rescue_report=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(cli, "_archive", lambda: None)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_duplicates(
            Namespace(
                path=[str(src)],
                output=str(tmp_path / "dupes.csv"),
                workers=1,
                match_mode="exact",
                fuzzy_threshold=0.85,
            )
        )

    assert exc.value.code == 2


def test_cli_rename_fails_loud_when_archive_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    source.mkdir()

    _install_fake_module(monkeypatch, "renamer", rename_directory=lambda *_a, **_k: [])
    _install_fake_module(monkeypatch, "db_connection", write_db=_dummy_db_ctx)
    monkeypatch.setattr(cli, "_archive", lambda: None)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_rename(
            Namespace(
                path=str(source),
                also_scan=[],
                no_dry_run=True,
                workers=1,
            )
        )

    assert exc.value.code == 2
