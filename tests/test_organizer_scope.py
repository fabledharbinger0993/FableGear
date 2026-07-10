"""
Tests for the organizer's blast-radius contract, added after a live run with
source = the user's home folder moved rekordbox/Pioneer app resources into
the music library and pruned empty directories inside Apple Photos' app
container (~/Library/Containers/com.apple.photos.ImageConversionService).

Three rules pinned here:
1. Forbidden sources — the organizer refuses to run from "/", the home
   folder, ~/Library, or OS areas. Music lives in folders, not at the top
   of an operating system.
2. Prune-own-footprints — only directories the run itself emptied (and
   their newly-empty ancestors) may be removed. A pre-existing empty
   directory anywhere else under the source survives untouched.
3. Journal-as-you-go — every move is written to fg_processing_log the
   moment it happens, so an interrupted run still leaves a complete record.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "chop_shop") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "chop_shop"))

import library_organizer
from library_organizer import _forbidden_source_reason, _prune_emptied_dirs, organize_library


# ── 1. Forbidden sources ──────────────────────────────────────────────────────
# "HOME/" prefixes resolve at RUN time (another test module reassigns $HOME
# mid-suite; capturing Path.home() at collection would race it).

@pytest.mark.parametrize("bad", [
    "/",
    "HOME",
    "/Users",
    "/System",
    "/Applications",
    "/Library",
    "/Volumes",
    "HOME/Library",
    "HOME/Library/Containers/com.apple.photos",
])
def test_forbidden_sources_are_refused(bad):
    path = Path(str(bad).replace("HOME", str(Path.home())))
    assert _forbidden_source_reason(path) is not None, f"{path} must be refused"


@pytest.mark.parametrize("ok", [
    "/Volumes/CAMAGIG",
    "/Volumes/Passport/DJMT_Library",
    "HOME/Music/DJ Library",
    "HOME/Downloads/new tracks",
])
def test_legitimate_sources_are_allowed(ok):
    path = Path(str(ok).replace("HOME", str(Path.home())))
    assert _forbidden_source_reason(path) is None, f"{path} must be allowed"


def test_organize_library_raises_on_forbidden_source(tmp_path):
    with pytest.raises(ValueError, match="Refusing to organize"):
        organize_library([Path.home()], tmp_path / "target")


# ── 2. Prune only what the run emptied ────────────────────────────────────────

def test_prune_leaves_preexisting_empty_dirs_alone(tmp_path):
    root = tmp_path / "src"
    emptied = root / "album"           # the run moved tracks out of here
    bystander = root / "app_scratch"   # was already empty — not our footprint
    nested_bystander = root / "photos" / "tmp" / "ABC123"
    emptied.mkdir(parents=True)
    bystander.mkdir(parents=True)
    nested_bystander.mkdir(parents=True)

    _prune_emptied_dirs(root, {emptied})

    assert not emptied.exists(), "the dir we emptied should be pruned"
    assert bystander.exists(), "pre-existing empty dirs must survive"
    assert nested_bystander.exists(), "nested pre-existing empty dirs must survive"
    assert root.exists(), "the source root itself is never removed"


def test_prune_climbs_ancestors_it_empties_but_stops_at_content(tmp_path):
    root = tmp_path / "src"
    leaf = root / "artist" / "album"
    sibling = root / "artist2" / "album2"
    leaf.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (sibling / "cover.jpg").write_bytes(b"img")  # real content blocks pruning
    (leaf.parent / ".DS_Store").write_bytes(b"junk")  # junk does not

    _prune_emptied_dirs(root, {leaf, sibling})

    assert not leaf.exists()
    assert not leaf.parent.exists(), "ancestor emptied by us (junk only) is pruned"
    assert sibling.exists(), "folders still holding real files are kept"
    assert (sibling / "cover.jpg").exists()


def test_prune_never_escapes_the_source_root(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    _prune_emptied_dirs(root, {outside, tmp_path})

    assert outside.exists()
    assert tmp_path.exists()


# ── 3. Journal-as-you-go ──────────────────────────────────────────────────────

def test_moves_are_journaled_per_file_not_at_the_end(tmp_path, monkeypatch):
    """Each move must hit fg_processing_log as it happens: a crash after the
    move loop must not erase the record. We assert the log row exists by the
    time the NEXT file is processed."""
    from fablegear_database.database import FableGearDatabase
    from fablegear_database.schema import DatabaseConfig

    archive = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))
    src = tmp_path / "src" / "loose"
    src.mkdir(parents=True)
    target = tmp_path / "target"
    # Two files large enough to pass MIN_FILE_BYTES with an audio extension.
    (src / "one.mp3").write_bytes(b"x" * 64)
    (src / "two.mp3").write_bytes(b"x" * 64)

    counts_seen = []
    real_process_hook = library_organizer._canonical_dest

    def spy_dest(path, target_arg, track, thr):
        # Called once per file at the START of processing it — by the second
        # call, the first file's move must already be in the log.
        counts_seen.append(archive.count_operations("organize"))
        return real_process_hook(path, target_arg, track, thr)

    monkeypatch.setattr(library_organizer, "_canonical_dest", spy_dest)

    results = organize_library([src.parent], target, dry_run=False,
                               mode="integrate", archive=archive)

    moved = [r for r in results if r.action in ("moved", "conflict_renamed")]
    assert len(moved) == 2
    assert archive.count_operations("organize") == 2
    # Sequential mode processes one file fully before starting the next:
    # the second file's spy call must see the first move already journaled.
    assert counts_seen[0] == 0
    assert counts_seen[-1] >= 1, "first move was not journaled before the run ended"


# ── The guard is EVERY tool's guard ──────────────────────────────────────────
# Same rails, all scanning entry points: pointing any tool at the home folder
# (or worse) must refuse before a single file is read.

def _entry_calls(tmp_path):
    from novelty_scanner import scan_novel
    from dead_file_scanner import scan_dead_files
    from duplicate_detector import scan_duplicates
    from renamer import rename_directory
    home = Path.home()
    return {
        "organize_library": lambda: organize_library([home], tmp_path / "t"),
        "scan_novel": lambda: scan_novel([home], tmp_path / "dest"),
        "scan_dead_files": lambda: scan_dead_files([home], db_paths=[]),
        "scan_duplicates": lambda: scan_duplicates(home),
        "rename_directory": lambda: rename_directory(home),
    }


@pytest.mark.parametrize("tool", [
    "organize_library", "scan_novel", "scan_dead_files",
    "scan_duplicates", "rename_directory",
])
def test_every_scanning_tool_refuses_home_folder(tool, tmp_path):
    with pytest.raises(ValueError, match="Refusing to run|Refusing to organize"):
        _entry_calls(tmp_path)[tool]()


# ── Journal-as-you-go: novelty copies are recorded per file ──────────────────

def test_novelty_journals_each_copy(tmp_path):
    from fablegear_database.database import FableGearDatabase
    from fablegear_database.schema import DatabaseConfig
    from novelty_scanner import scan_novel

    archive = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))
    src = tmp_path / "downloads"
    dst = tmp_path / "library"
    src.mkdir()
    dst.mkdir()
    (src / "fresh track.mp3").write_bytes(b"x" * 64)

    result = scan_novel([src], dst, dry_run=False, match_mode="filename", archive=archive)

    copied = [t for t in result.novel if t.action == "copied"]
    assert len(copied) == 1
    assert archive.count_operations("novelty_copy") == 1, (
        "each copy must hit fg_processing_log the moment it lands"
    )
