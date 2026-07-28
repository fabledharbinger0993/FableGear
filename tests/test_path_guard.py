import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chop_shop.path_guard import forbidden_browse_reason, forbidden_source_reason


def test_browse_allows_any_subfolder_outside_volumes_and_home(tmp_path):
    # Regression: browsing used to be hard-restricted to /Volumes or $HOME,
    # rejecting perfectly valid music folders anywhere else (e.g. a second
    # internal drive, a shared folder, an unusual mount point).
    somewhere_else = tmp_path / "not_under_volumes_or_home" / "DJ Music"
    somewhere_else.mkdir(parents=True)
    assert forbidden_browse_reason(somewhere_else) is None


def test_browse_allows_home_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert forbidden_browse_reason(tmp_path) is None


def test_browse_blocks_os_internal_trees():
    assert forbidden_browse_reason(Path("/System")) is not None
    assert forbidden_browse_reason(Path("/")) is not None


def test_browse_blocks_library_data(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    lib = tmp_path / "Library" / "Application Support"
    lib.mkdir(parents=True)
    assert forbidden_browse_reason(lib) is not None


def test_run_still_blocks_home_itself_even_though_browse_allows_it(tmp_path, monkeypatch):
    # guard_sources()/forbidden_source_reason() stays strict for actual tool
    # execution — only the read-only browse check was over-restrictive.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert forbidden_browse_reason(tmp_path) is None
    assert forbidden_source_reason(tmp_path) is not None
