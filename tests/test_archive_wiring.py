"""
Regression guard: tool ↔ Archive wiring must not rot silently.

Run from the repo root:
    python3 -m pytest tests/test_archive_wiring.py -v

The Archive (fg_processing_log / fg_content) is an optional side-effect of
every Chop Shop tool — `archive=None` and the tool still "works", it just
stops contributing to the shared memory. Nothing else fails when a caller
drops the parameter, which is exactly how the persisted report archive
disconnected in the past. These tests make that failure loud:

1. Signature contract — every tool entry point accepts `archive`.
2. Caller wiring (AST) — every call to a tool entry point in cli.py and
   routes_tools.py passes an explicit `archive=` keyword.
3. Functional — cheap tools run against a real temp-path FableGearDatabase
   append a row to fg_processing_log even for an empty scan.
"""

import ast
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

from fablegear_database.database import FableGearDatabase
from fablegear_database.schema import DatabaseConfig

# Tool entry points that MUST stay archive-aware. Module name → function name.
TOOL_ENTRY_POINTS = {
    "dead_file_scanner":  "scan_dead_files",
    "duplicate_detector": "scan_duplicates",
    "library_organizer":  "organize_library",
    "novelty_scanner":    "scan_novel",
    "pruner":             "prune_files",
    "relocator":          "relocate_directory",
    "renamer":            "rename_directory",
}

# Files whose calls into the tools must pass archive= explicitly.
CALLER_FILES = [
    REPO_ROOT / "cli.py",
    REPO_ROOT / "routes_tools.py",
]


# ── 1. Signature contract ─────────────────────────────────────────────────────

@pytest.mark.parametrize("module_name,func_name", sorted(TOOL_ENTRY_POINTS.items()))
def test_tool_entry_point_accepts_archive(module_name, func_name):
    module = __import__(module_name)
    fn = getattr(module, func_name)
    params = inspect.signature(fn).parameters
    assert "archive" in params, (
        f"{module_name}.{func_name} no longer accepts archive= — "
        "the tool can no longer contribute to fg_processing_log."
    )


# ── 2. Caller wiring ──────────────────────────────────────────────────────────

def _tool_calls_in(path: Path):
    """Yield (func_name, lineno, keyword_names) for every call to a tool
    entry point in the given source file."""
    tree = ast.parse(path.read_text(), str(path))
    tool_names = set(TOOL_ENTRY_POINTS.values())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None
        )
        if name in tool_names:
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            yield name, node.lineno, kwargs


@pytest.mark.parametrize("caller", CALLER_FILES, ids=lambda p: p.name)
def test_callers_pass_archive(caller):
    unwired = [
        f"{caller.name}:{lineno} calls {name}() without archive="
        for name, lineno, kwargs in _tool_calls_in(caller)
        if "archive" not in kwargs
    ]
    assert not unwired, (
        "Tool call sites dropped the archive connection — their runs will "
        "leave no record in fg_processing_log:\n  " + "\n  ".join(unwired)
    )


def test_callers_actually_call_tools():
    """Meta-check: the AST scan must keep seeing the call sites at all,
    otherwise test_callers_pass_archive could pass vacuously after a rename."""
    total = sum(len(list(_tool_calls_in(c))) for c in CALLER_FILES)
    assert total >= 8, (
        f"Only {total} tool call sites found across cli.py/routes_tools.py — "
        "either tools were renamed (update TOOL_ENTRY_POINTS) or callers moved "
        "(update CALLER_FILES)."
    )


# ── 3. Functional: tools append to fg_processing_log ─────────────────────────

@pytest.fixture
def archive(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))


def test_novelty_scan_logs_to_archive(archive, tmp_path):
    from novelty_scanner import scan_novel

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    before = archive.count_operations("novelty_scan")
    scan_novel([src], dst, dry_run=True, archive=archive)
    assert archive.count_operations("novelty_scan") == before + 1, (
        "scan_novel ran but appended nothing to fg_processing_log"
    )


def test_dead_file_scan_logs_to_archive(archive, tmp_path):
    from dead_file_scanner import scan_dead_files

    root = tmp_path / "library"
    root.mkdir()

    before = archive.count_operations("dead_file_scan")
    scan_dead_files([root], db_paths=[], archive=archive)
    assert archive.count_operations("dead_file_scan") == before + 1, (
        "scan_dead_files ran but appended nothing to fg_processing_log"
    )


# ── Music-only contract ───────────────────────────────────────────────────────

def test_relocate_with_archive_logs_operations(archive, tmp_path):
    """Exercise relocate_directory's archive block with a real call.

    This test calls relocate_directory with archive= to trigger the archive
    block. Before the fix, accessing r.old_path in the archive block raises
    AttributeError (RelocationResult has original_path, not old_path), causing
    the function to crash after every successful relocation. The except handler
    then tries to access r.old_path again, causing a second AttributeError that
    propagates out.
    """
    from relocator import relocate_directory
    from pathlib import Path

    # Set up minimal Rekordbox db mock
    class MockQuery:
        def all(self):
            # Return one mock content row that is expected to match via exact
            # strategy (the corresponding file is created in new_root below).
            # This exercises the archive block without needing full filesystem setup.
            class MockContent:
                ID = 1
                FolderPath = str(old_root / "never_found.mp3")
            return [MockContent()]

    class MockDb:
        def get_content(self):
            return MockQuery()
        def commit(self):
            pass
        def rollback(self):
            pass
        def update_content_path(self, row, new_path, check_path=True):
            # Allow the relocation to succeed so the archive block runs
            pass

    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()

    # Create a fake audio file in new_root so the exact match can find it
    (new_root / "never_found.mp3").write_bytes(b"fake audio")

    db = MockDb()

    # Before the fix, this raises AttributeError on the archive block.
    # After the fix, it completes without error and logs to the archive.
    before = archive.count_operations("relocate")
    try:
        results = relocate_directory(old_root, new_root, db, archive=archive)
        # Success — no AttributeError raised
        assert isinstance(results, list), "relocate_directory should return a list"
        # Verify the archive write that this test is named for
        assert archive.count_operations("relocate") >= before + 1, (
            "relocate_directory ran but appended nothing to fg_processing_log"
        )
    except AttributeError as e:
        if "old_path" in str(e):
            pytest.fail(
                f"relocate_directory raised AttributeError accessing r.old_path: {e}\n"
                "This is the F-01 bug — RelocationResult.original_path was accessed as r.old_path"
            )
        raise


def test_relocate_filters_sibling_directories(tmp_path):
    """Verify that prefix matching only includes true descendants, not siblings.

    Regression test for F-04: if old_root is /Volumes/Music/Rock, tracks under
    /Volumes/Music/Rockabilly should NOT be included. Before the fix, the
    prefix match used str(old_root).startswith(...) without a path separator,
    causing "Rock" to match "Rockabilly".
    """
    from relocator import relocate_directory

    # Set up minimal Rekordbox db mock with TWO content rows
    rock_path = tmp_path / "Rock" / "track.mp3"
    rockabilly_path = tmp_path / "Rockabilly" / "other.mp3"
    old_root = tmp_path / "Rock"

    class MockQuery:
        def all(self):
            # Return two mock content rows: one under Rock (should match),
            # one under Rockabilly (should NOT match due to sibling name collision)
            class MockContent1:
                ID = 1
                FolderPath = str(rock_path)
            class MockContent2:
                ID = 2
                FolderPath = str(rockabilly_path)
            return [MockContent1(), MockContent2()]

    class MockDb:
        def get_content(self):
            return MockQuery()
        def commit(self):
            pass
        def rollback(self):
            pass
        def update_content_path(self, row, new_path, check_path=True):
            pass

    # Create directories and new_root
    old_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "Rockabilly").mkdir(parents=True, exist_ok=True)
    new_root = tmp_path / "NewMusic"
    new_root.mkdir(parents=True, exist_ok=True)

    # Create dummy files in new_root so exact match doesn't fail
    (new_root / "track.mp3").write_bytes(b"fake audio")
    (new_root / "other.mp3").write_bytes(b"fake audio")

    db = MockDb()
    results = relocate_directory(old_root, new_root, db)

    # After the fix, only the Rock entry (ID=1) should be in results.
    # Before the fix, both would be included (Rockabilly incorrectly matched).
    assert len(results) == 1, (
        f"Expected 1 affected track (Rock), but got {len(results)}. "
        "Before the fix: Rockabilly was incorrectly included due to "
        "prefix matching without path separator (Rock matches Rock*)."
    )
    assert results[0].content_id == "1", (
        f"Expected result with content_id='1' (Rock track), got content_id={results[0].content_id}"
    )


def test_audio_extensions_contain_no_video_containers():
    """FableGear touches music, nothing else. Every file-touching tool scans by
    config.AUDIO_EXTENSIONS — video containers must never sneak back in."""
    import config

    video = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm"}
    leaked = video & set(config.AUDIO_EXTENSIONS)
    assert not leaked, (
        f"Video containers {sorted(leaked)} are in AUDIO_EXTENSIONS — the "
        "organizer/renamer/converter would move video files into the music tree."
    )
