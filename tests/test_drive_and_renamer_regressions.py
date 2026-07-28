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


def test_generate_filename_includes_genuine_album(monkeypatch):
    renamer = _load_renamer_module(monkeypatch)
    assert renamer._generate_filename(
        "Daft Punk", "Around the World", ".mp3", album="Homework"
    ) == "Daft Punk - Homework - Around the World.mp3"


def test_generate_filename_drops_album_that_prefixes_title(monkeypatch):
    """A single sold as its own 'album' — album is a leading run of the title —
    must not render the doubled 'Snow Day - Snow Day (Rain Day Remix)'."""
    renamer = _load_renamer_module(monkeypatch)
    assert renamer._generate_filename(
        "Justin Martin", "Snow Day (Rain Day Remix)", ".mp3", album="Snow Day"
    ) == "Justin Martin - Snow Day (Rain Day Remix).mp3"


def test_generate_filename_drops_album_when_title_prefixes_it(monkeypatch):
    """Redundancy is symmetric: album 'Snow Day EP' against title 'Snow Day'."""
    renamer = _load_renamer_module(monkeypatch)
    assert renamer._generate_filename(
        "Artist", "Snow Day", ".mp3", album="Snow Day EP"
    ) == "Artist - Snow Day.mp3"


def test_generate_filename_drops_album_equal_to_artist(monkeypatch):
    renamer = _load_renamer_module(monkeypatch)
    assert renamer._generate_filename(
        "Artist", "Track", ".mp3", album="Artist"
    ) == "Artist - Track.mp3"


def test_generate_filename_keeps_album_sharing_only_partial_prefix(monkeypatch):
    """A split single ('I Like the Way / Stepping Out') shares a leading phrase
    with the title but adds real info — neither is a full prefix of the other,
    so the album stays."""
    renamer = _load_renamer_module(monkeypatch)
    out = renamer._generate_filename(
        "Kaskade", "I Like The Way (Extended Mix)", ".mp3",
        album="I Like the Way Stepping Out",
    )
    assert out == "Kaskade - I Like the Way Stepping Out - I Like The Way (Extended Mix).mp3"


# Distinct words (no repeats) so _normalize_artist_text's dedup pass — which
# collapses a string that's the same phrase repeated — never fires and
# shortens these back down before the length fix even gets exercised.
_UNIQUE_WORDS = [
    "Midnight", "Session", "Harbor", "Echoes", "Static", "Velvet", "Horizon", "Lucid",
    "Cascade", "Ember", "Solstice", "Nomad", "Aurora", "Fracture", "Wander", "Hollow",
    "Crimson", "Zenith", "Mirage", "Cipher", "Drift", "Lantern", "Tremor", "Glass",
    "Vapor", "Ridge", "Pulse", "Amber", "Chrome", "Sable", "Meridian", "Voyage",
    "Thicket", "Quartz", "Lumen", "Delta", "Ashen", "Onyx", "Willow", "Basin",
]


def test_generate_filename_caps_combined_length_under_filesystem_limit(monkeypatch):
    renamer = _load_renamer_module(monkeypatch)
    # Realistic worst case: a label stuffs catalog numbers, remix credits,
    # and session notes into the title tag. Both fields alone exceed the
    # 255-byte filesystem component limit once combined.
    artist = " ".join(_UNIQUE_WORDS[:20])
    title = " ".join(_UNIQUE_WORDS[20:] + _UNIQUE_WORDS[:20])

    name = renamer._generate_filename(artist, title, ".mp3")

    assert len(name.encode("utf-8")) <= 255
    assert name.endswith(".mp3")
    assert " - " in name


def test_generate_filename_handles_multibyte_text_without_corrupting_chars(monkeypatch):
    renamer = _load_renamer_module(monkeypatch)
    artist = "アーティスト " * 60  # multi-byte UTF-8 text, well over the byte budget
    title = "トラックタイトル " * 60

    name = renamer._generate_filename(artist, title, ".mp3")

    assert len(name.encode("utf-8")) <= 255
    # Truncation must not land mid-codepoint — a corrupted tail would raise here.
    name.encode("utf-8").decode("utf-8")


def test_rename_one_shortens_pathologically_long_metadata(monkeypatch, tmp_path):
    renamer = _load_renamer_module(monkeypatch)
    path = tmp_path / "original.mp3"
    path.write_bytes(b"")
    long_artist = " ".join(_UNIQUE_WORDS[:20])
    long_title = " ".join(_UNIQUE_WORDS[20:] + _UNIQUE_WORDS[:20])
    monkeypatch.setattr(
        renamer, "extract_metadata",
        lambda p: SimpleNamespace(artist=long_artist, title=long_title),
    )

    result = renamer._rename_one(path, dry_run=False)

    assert result.action == "renamed"
    assert len(result.new_path.name.encode("utf-8")) <= 255
    assert result.new_path.exists()


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
