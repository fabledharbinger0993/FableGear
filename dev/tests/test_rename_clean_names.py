"""
Re-Name and Re-Organize must produce names a person would write.

Found by dry-running Re-Name on 102 files from the worst folders of a real library: the album
was being put in the file name ("The Cure - Join the Dots B-Sides and Rarities, 1978-2001 - 10 15
Saturday Night"), typographic quotes/dashes survived (splitting "Terence Trent D'Arby" and
"Terence Trent D’Arby" into two folders), and corrupt tags produced names like "TG039 Jamar
Hughes TG039 Jamar Hughes ... - Ghetto Fab". The cases below are the real ones.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2] / "fablegear"
for sub in ("", "chop_shop"):
    path = str(REPO_ROOT / sub) if sub else str(REPO_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)

from chop_shop import library_organizer as lo
from chop_shop import renamer
from chop_shop.tag_cleaning import collapse_repeats, normalize_punctuation

CURLY = "’"       # right single quote
EN_DASH = "–"
HYPHEN = "‐"      # the Unicode hyphen in "B‐52s"


def test_normalize_punctuation_makes_typography_plain_ascii():
    assert normalize_punctuation(f"Terence Trent D{CURLY}Arby") == "Terence Trent D'Arby"
    assert normalize_punctuation(f"The B{HYPHEN}52s") == "The B-52s"
    assert normalize_punctuation(f"1962{EN_DASH}1970") == "1962-1970"
    assert normalize_punctuation("Beginning Stages of…") == "Beginning Stages of..."
    assert normalize_punctuation("a b​c") == "a bc"   # nbsp -> space, zero-width dropped


def test_normalize_punctuation_leaves_real_letters_alone():
    assert normalize_punctuation("Sigur Rós – Ágætis byrjun") == "Sigur Rós - Ágætis byrjun"
    assert normalize_punctuation("Björk") == "Björk"


def test_one_artist_no_longer_becomes_two_folders():
    assert lo._sanitize_folder(f"Terence Trent D{CURLY}Arby") == lo._sanitize_folder("Terence Trent D'Arby")
    assert lo._sanitize_filename(f"The O{CURLY}Jays - Love Train.mp3") == "The O'Jays - Love Train.mp3"


def test_generated_filename_has_no_typographic_characters():
    name = renamer._generate_filename(f"The Chi{HYPHEN}Lites", f"Oh Girl{CURLY}s Song", ".mp3")
    assert name == "The Chi-Lites - Oh Girl's Song.mp3"


@pytest.mark.parametrize("artist, title, expected", [
    # artist tag ran on into the title and was cut off mid-word
    ("The Salsoul Orchestra - Ooh I",
     "07 07 - The Salsoul Orchestra - Ooh I Love It (Love Break) [Original Shep Pettibone 12' Remix]_PN",
     ("The Salsoul Orchestra", "Ooh I Love It (Love Break) [Original Shep Pettibone 12' Remix]")),
    # artist is the label code repeated, title repeats it with an underscore
    ("TG039 Jamar Hughes TG039 Jamar Hughes TG039 Jamar Hughes TG039 Jamar Hughes",
     "TG039_Jamar Hughes - Ghetto Fab", ("TG039 Jamar Hughes", "Ghetto Fab")),
    ("TG037 TG037 TG037 TG037 Aback TG037", "TG037 - Aback - Don't Stop",
     ("TG037 Aback", "Don't Stop")),
    # stray trailing separator on the artist
    ("The Bucketheads 2 - ", "I Wanna Know", ("The Bucketheads 2", "I Wanna Know")),
    # ordinary tags are left exactly as they were
    ("Terry Lee Brown Jr", "Plastic Passion", ("Terry Lee Brown Jr", "Plastic Passion")),
    ("Duran Duran", "Rio", ("Duran Duran", "Rio")),
    # disc-track prefix with no separator after it
    ("The Crashers", "2-07 Flight to Jamaica", ("The Crashers", "Flight to Jamaica")),
    # a title that merely starts with a number is not a track number
    ("Prince", "1999", ("Prince", "1999")),
    ("Nena", "99 Luftballons", ("Nena", "99 Luftballons")),
])
def test_corrupt_tags_are_repaired_and_clean_ones_untouched(artist, title, expected):
    assert renamer._tidy_artist_and_title(artist, title) == expected


def test_title_that_is_only_the_artist_is_not_emptied():
    assert renamer._tidy_artist_and_title("Love", "Love - Love")[1] == "Love"


def test_collapse_repeats_keeps_legitimate_doubles():
    assert collapse_repeats("Duran Duran") == "Duran Duran"
    assert collapse_repeats("the timewriter the timewriter the timewriter the") == "the timewriter"
    assert collapse_repeats("TG037 TG037 TG037 TG037 Aback TG037") == "TG037 Aback"


def _tagged_mp3(tmp_path: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "1._Action.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-q:a", "9",
         "-metadata", "artist=Terror Fabulous", "-metadata", "title=Action",
         "-metadata", "album=Dancehall Flavaz - Volume 1", str(out)],
        check=True, capture_output=True)
    return out


def test_album_stays_out_of_the_file_name_by_default(tmp_path):
    src = _tagged_mp3(tmp_path)
    result = renamer._rename_one(src, dry_run=True)
    assert result.new_path is not None
    assert result.new_path.name == "Terror Fabulous - Action.mp3"


def test_album_can_be_switched_on(tmp_path):
    src = _tagged_mp3(tmp_path)
    result = renamer._rename_one(src, dry_run=True, include_album=True)
    assert result.new_path is not None
    assert result.new_path.name == "Terror Fabulous - Dancehall Flavaz - Volume 1 - Action.mp3"


def test_cli_exposes_include_album_off_by_default():
    import cli
    parser = cli.build_parser()
    assert parser.parse_args(["rename", "/x"]).include_album is False
    assert parser.parse_args(["rename", "/x", "--include-album"]).include_album is True


def test_probe_previews_the_same_names_the_run_will_use(tmp_path):
    """The preflight probe has to propose album-less names by default and album names when the
    box is ticked, or applying a probe decision would override the run's naming."""
    _tagged_mp3(tmp_path)
    plain = renamer.probe_ambiguous(tmp_path, top_n=5, sample_size=10)
    with_album = renamer.probe_ambiguous(tmp_path, top_n=5, sample_size=10, include_album=True)
    names = {c.proposed_filename for c in plain}
    names_album = {c.proposed_filename for c in with_album}
    assert names == {"Terror Fabulous - Action.mp3"}
    assert names_album == {"Terror Fabulous - Dancehall Flavaz - Volume 1 - Action.mp3"}
