"""
Tests for the Organize tool's choosable grouping schemes (``--by``) and the
tag-cleaning engine behind them.

The organizer used to be hardwired to Artist / Album / Track. These pin the
generalized behavior:

1. ``parse_scheme`` accepts slash-nested keys, defaults to artist/album, and
   rejects unknown keys loudly.
2. ``tag_cleaning`` turns junk tag values (URLs, ℗/© prefixes, Camelot keys in
   the artist field, ``unknown`` sentinels) into ``None`` so they never become
   a folder.
3. ``_canonical_dest`` builds the right path per scheme, routes a track with no
   value for the *primary* key to Orphaned Tracks, and collapses a missing
   *secondary* level instead of inventing an "Unknown" folder.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "chop_shop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import library_organizer as L
from tag_cleaning import clean_album, clean_artist, clean_label, clean_value


def _track(**kw):
    base = dict(
        path=Path("/src/Kenny Dope - Boogie.mp3"),
        title="Boogie", artist="Kenny Dope", album="The EP",
        genre="House", label="Defected", year=2019,
        file_type="MP3", duration_seconds=320.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _dest(scheme, **kw):
    t = _track(**kw)
    target = Path("/lib")
    return str(L._canonical_dest(t.path, target, t, 900.0,
                                 scheme=L.parse_scheme(scheme)).relative_to(target))


# ── parse_scheme ────────────────────────────────────────────────────────────

def test_parse_scheme_default_is_artist_album():
    assert L.parse_scheme(None) == ("artist", "album")
    assert L.parse_scheme("") == ("artist", "album")


def test_parse_scheme_splits_and_lowercases():
    assert L.parse_scheme("Label/Artist") == ("label", "artist")
    assert L.parse_scheme(["genre", "artist"]) == ("genre", "artist")


def test_parse_scheme_rejects_unknown_key():
    with pytest.raises(ValueError):
        L.parse_scheme("label/bogus")


# ── tag_cleaning ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["beatport.com", "foo.blogspot.com", "www.x", "unknown", "n/a", "0", ""])
def test_clean_label_drops_junk(raw):
    assert clean_label(raw) is None


def test_clean_label_strips_copyright_year():
    assert clean_label("℗ 2020 Strut") == "Strut"
    assert clean_label("© 1999 Nervous Records") == "Nervous Records"


def test_clean_label_merge_map_is_opt_in():
    mm = {"tequila trax": "TQL", "tql": "TQL"}
    assert clean_label("Tequila Trax", merge_map=mm) == "TQL"
    assert clean_label("Tequila Trax") == "Tequila Trax"  # no map → untouched


def test_clean_artist_rejects_camelot_key():
    assert clean_artist("8A") is None
    assert clean_artist("12B") is None
    assert clean_artist("Kenny Dope") == "Kenny Dope"


def test_clean_album_passthrough_and_junk():
    assert clean_album("The EP") == "The EP"
    assert clean_album("unknown album") is None


def test_clean_value_dispatch():
    assert clean_value("label", "beatport.com") is None
    assert clean_value("genre", "House") == "House"


# ── _canonical_dest across schemes ──────────────────────────────────────────

def test_default_scheme_unchanged():
    assert _dest(None) == "Kenny Dope/The EP/Kenny Dope - Boogie.mp3"


def test_by_label():
    assert _dest("label") == "Defected/Kenny Dope - Boogie.mp3"


def test_by_label_artist():
    assert _dest("label/artist") == "Defected/Kenny Dope/Kenny Dope - Boogie.mp3"


def test_by_filetype():
    assert _dest("filetype") == "MP3/Kenny Dope - Boogie.mp3"


def test_by_year_artist():
    assert _dest("year/artist") == "2019/Kenny Dope/Kenny Dope - Boogie.mp3"


def test_primary_key_missing_routes_to_orphans():
    # No label tag and label is the primary key → Orphaned Tracks.
    out = _dest("label", label=None)
    assert out.startswith("Orphaned Tracks/")


def test_secondary_missing_level_collapses():
    # label present, artist unresolvable → target/Label/track (no "Unknown" dir).
    out = _dest("label/artist", artist="8A", label="Strut",
                path=Path("/src/8A.mp3"))
    assert out == "Strut/8A.mp3"


def test_long_mix_shortcuts_to_mix_folder_regardless_of_scheme():
    out = _dest("label", duration_seconds=3600.0)
    assert out.startswith(f"{L.MIX_FOLDER}/")


# ── --by playlist (copy mirror) ─────────────────────────────────────────────

def test_parse_scheme_accepts_playlist_standalone():
    assert L.parse_scheme("playlist") == ("playlist",)


def test_parse_scheme_rejects_playlist_combined():
    with pytest.raises(ValueError):
        L.parse_scheme("playlist/artist")


class _FakeArchive:
    def __init__(self, playlists, songs):
        self._playlists = playlists
        self._songs = songs  # {playlist_id: [file_path, ...]}

    def list_playlists(self):
        return self._playlists

    def get_playlist_songs(self, pid):
        return [SimpleNamespace(file_path=p) for p in self._songs.get(pid, [])]


def test_playlist_mirror_copies_one_to_many_and_scopes_to_sources(tmp_path):
    src = tmp_path / "lib"; src.mkdir()
    out = tmp_path / "elsewhere"; out.mkdir()
    tgt = tmp_path / "out"; tgt.mkdir()
    a = src / "a.mp3"; a.write_bytes(b"aaaa")
    b = src / "b.mp3"; b.write_bytes(b"bbbb")
    c = out / "c.mp3"; c.write_bytes(b"cccc")  # outside SRC → excluded

    archive = _FakeArchive(
        playlists=[
            {"id": 1, "name": "House", "type": "playlist"},
            {"id": 2, "name": "Deep/Vibes", "type": "playlist"},  # slash sanitized
            {"id": 9, "name": "Folder", "type": "folder"},        # folders skipped
        ],
        songs={1: [str(a), str(b)], 2: [str(a), str(c)]},
    )

    L.organize_library([src], tgt, archive=archive, dry_run=False, scheme="playlist")
    made = sorted(str(p.relative_to(tgt)) for p in tgt.rglob("*.mp3"))

    assert made == ["Deep Vibes/a.mp3", "House/a.mp3", "House/b.mp3"]
    # a.mp3 lands in two crates (one-to-many copy); c.mp3 (outside SRC) is absent.
    assert a.is_file() and b.is_file()  # sources intact — copy, not move


def test_playlist_mirror_requires_archive(tmp_path):
    src = tmp_path / "lib"; src.mkdir()
    (src / "x.mp3").write_bytes(b"x")
    with pytest.raises(ValueError):
        L.organize_library([src], tmp_path / "out", archive=None,
                            dry_run=True, scheme="playlist")
