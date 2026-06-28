import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from user_config import archive_root_for_music_root, discover_music_roots


RENAMER_PATH = REPO_ROOT / "chop_shop" / "renamer.py"


def _load_renamer_module(monkeypatch):
    """Import renamer.py with tiny stub modules so regression tests stay dependency-light."""
    config = types.ModuleType("config")
    config.AUDIO_EXTENSIONS = {".mp3"}
    config.BATCH_SIZE = 25
    config.SKIP_DIRS = set()
    config.SKIP_PREFIXES = []

    scanner = types.ModuleType("scanner")
    scanner.extract_metadata = lambda path: SimpleNamespace(artist=None, title=None)

    learned = types.ModuleType("renamer_learned")

    mutagen = types.ModuleType("mutagen")
    mutagen.File = lambda *args, **kwargs: None

    mutagen_id3 = types.ModuleType("mutagen.id3")
    mutagen_id3.ID3 = object

    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setitem(sys.modules, "scanner", scanner)
    monkeypatch.setitem(sys.modules, "renamer_learned", learned)
    monkeypatch.setitem(sys.modules, "mutagen", mutagen)
    monkeypatch.setitem(sys.modules, "mutagen.id3", mutagen_id3)

    spec = importlib.util.spec_from_file_location("test_renamer_module", RENAMER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_get_prioritized_artist", lambda path: None)
    monkeypatch.setattr(module, "extract_metadata", lambda path: SimpleNamespace(artist=None, title=None))
    return module


def test_discover_music_roots_recommends_largest_library(tmp_path):
    small = tmp_path / "SmallLibrary"
    large = tmp_path / "LargeLibrary"
    (small / "A").mkdir(parents=True)
    (large / "Deep" / "Folder").mkdir(parents=True)

    for idx in range(5):
        (small / "A" / f"track-{idx}.mp3").write_bytes(b"")
    for idx in range(8):
        (large / "Deep" / "Folder" / f"track-{idx}.mp3").write_bytes(b"")

    discovered = discover_music_roots([small, large])

    assert [item["path"] for item in discovered] == [str(large), str(small)]
    assert discovered[0]["recommended_home"] is True
    assert discovered[0]["recommended_archive_root"] == str(archive_root_for_music_root(large))
    assert discovered[0]["recommended_backup_dir"].endswith("FableGear Archive/Savepoints")


def test_generate_filename_uses_hyphen_separator(monkeypatch):
    renamer = _load_renamer_module(monkeypatch)
    assert renamer._generate_filename("Artist", "Title", ".mp3") == "Artist - Title.mp3"


def test_extract_artist_title_reads_legacy_colon_separator(monkeypatch):
    renamer = _load_renamer_module(monkeypatch)
    artist, title, copy_suffix = renamer._extract_artist_title(
        Path("/tmp/Artist: Title.mp3"),
        SimpleNamespace(artist=None, title=None),
    )
    assert (artist, title, copy_suffix) == ("Artist", "Title", None)


def test_rename_one_is_idempotent_for_hyphen_names(monkeypatch, tmp_path):
    renamer = _load_renamer_module(monkeypatch)
    path = tmp_path / "Artist - Title.mp3"
    path.write_bytes(b"")

    result = renamer._rename_one(path, dry_run=True)

    assert result.action == "no_change"
    assert result.new_path == path


def test_rename_one_migrates_legacy_colon_name_once(monkeypatch, tmp_path):
    renamer = _load_renamer_module(monkeypatch)
    path = tmp_path / "Artist: Title.mp3"
    path.write_bytes(b"")

    result = renamer._rename_one(path, dry_run=True)

    assert result.action == "renamed"
    assert result.new_path.name == "Artist - Title.mp3"
