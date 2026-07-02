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
