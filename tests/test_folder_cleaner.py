"""Re-Name folder step: planning, merging, and applying folder-name cleanup (tmp dirs only)."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("", "chop_shop"):
    path = str(REPO_ROOT / sub) if sub else str(REPO_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)

from chop_shop import folder_cleaner as fc


@pytest.mark.parametrize("messy, clean", [
    ("Terence Trent D’Arby", "Terence Trent D'Arby"),
    ("The Beatles 1962–1970, Part 1", "The Beatles 1962-1970, Part 1"),
    ("Artist_-_Title_[WEB]", "Artist - Title"),
    ("Album Name (320kbps)", "Album Name"),
    ("TG037 TG037 TG037 TG037 Aback TG037", "TG037 Aback"),
    ("The Bucketheads 2 -", "The Bucketheads 2"),
    ("  .Spaced   Out.  ", "Spaced Out"),
    ("AC:DC *Live*", "AC DC Live"),
])
def test_clean_folder_name(messy, clean):
    assert fc.clean_folder_name(messy) == clean


@pytest.mark.parametrize("name", ["Hot Hot Hot", "Duran Duran", "Daft Punk", "New York New York"])
def test_real_names_with_repeated_words_are_left_alone(name):
    assert fc.clean_folder_name(name) == name


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_plan_changes_nothing_on_disk(tmp_path):
    _touch(tmp_path / "D’Arby" / "a.mp3")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    plan = fc.plan_folder_cleanup(tmp_path)

    assert [(c.old.name, c.new.name, c.kind) for c in plan.changes] == [("D’Arby", "D'Arby", "rename")]
    assert [m.dest for m in plan.moves] == [tmp_path / "D'Arby" / "a.mp3"]
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_messy_twin_merges_into_the_clean_folder_whichever_sorts_first(tmp_path):
    _touch(tmp_path / "D’Arby" / "one.mp3")
    _touch(tmp_path / "D'Arby" / "two.mp3")

    plan = fc.plan_folder_cleanup(tmp_path)

    assert [(c.old.name, c.kind) for c in plan.changes] == [("D’Arby", "merge")]
    assert [m.dest for m in plan.moves] == [tmp_path / "D'Arby" / "one.mp3"]


def test_same_file_name_in_both_twins_is_a_conflict_not_an_overwrite(tmp_path):
    _touch(tmp_path / "D’Arby" / "same.mp3", b"from the messy folder")
    _touch(tmp_path / "D'Arby" / "same.mp3", b"already here")

    plan = fc.plan_folder_cleanup(tmp_path)
    result = fc.apply_folder_cleanup(plan)

    assert plan.moves == [] and len(plan.conflicts) == 1
    assert result["moved"] == 0
    assert (tmp_path / "D'Arby" / "same.mp3").read_bytes() == b"already here"
    assert (tmp_path / "D’Arby" / "same.mp3").read_bytes() == b"from the messy folder"


def test_nested_folders_follow_their_renamed_parent(tmp_path):
    _touch(tmp_path / "Artist_One" / "Album_[FLAC]" / "t.flac")

    plan = fc.plan_folder_cleanup(tmp_path)
    result = fc.apply_folder_cleanup(plan)

    assert result["moved"] == 1 and not result["errors"]
    assert (tmp_path / "Artist One" / "Album" / "t.flac").exists()
    assert not (tmp_path / "Artist_One").exists()


def test_cover_art_and_other_files_move_with_the_music(tmp_path):
    _touch(tmp_path / "A_B" / "t.mp3")
    _touch(tmp_path / "A_B" / "cover.jpg")
    _touch(tmp_path / "A_B" / ".DS_Store")

    fc.apply_folder_cleanup(fc.plan_folder_cleanup(tmp_path))

    assert (tmp_path / "A B" / "cover.jpg").exists()
    assert (tmp_path / "A B" / "t.mp3").exists()
    assert not (tmp_path / "A_B").exists()  # only OS junk was left, so it is tidied away


def test_a_folder_that_still_holds_files_is_not_removed(tmp_path):
    _touch(tmp_path / "A_B" / "t.mp3")
    _touch(tmp_path / "A B" / "t.mp3")  # conflicting name: stays put

    plan = fc.plan_folder_cleanup(tmp_path)
    fc.apply_folder_cleanup(plan)

    assert (tmp_path / "A_B" / "t.mp3").exists()


def test_root_and_clean_folders_are_untouched(tmp_path):
    _touch(tmp_path / "Clean Name" / "t.mp3")

    plan = fc.plan_folder_cleanup(tmp_path)

    assert plan.changes == [] and plan.moves == [] and plan.unchanged == 1


class _FakeArchive:
    def __init__(self):
        self.relinked, self.logged = [], []

    def get_content_by_path(self, path):
        class Rec:
            id = 7
        return Rec() if path.endswith("t.mp3") else None

    def relink_content(self, rec_id, new_path):
        self.relinked.append((rec_id, new_path))

    def log_operation(self, op, path, status="ok", metadata=None, **_kw):
        self.logged.append((op, path, metadata))


def test_apply_relinks_and_journals_each_move_as_a_rename(tmp_path):
    src = _touch(tmp_path / "A_B" / "t.mp3")
    archive = _FakeArchive()

    fc.apply_folder_cleanup(fc.plan_folder_cleanup(tmp_path), archive=archive)

    dest = tmp_path / "A B" / "t.mp3"
    assert archive.relinked == [(7, str(dest))]
    assert archive.logged == [("rename", str(dest), {"from": str(src), "action": "folder_cleanup"})]


def test_equivalent_folders_merge_into_the_exact_folder_that_claimed_the_name(tmp_path):
    """On a case-sensitive disk "D'ARBY" and "D’Arby" must not spawn a third "D'Arby"."""
    _touch(tmp_path / "D'ARBY" / "a.mp3")
    _touch(tmp_path / "D’Arby" / "b.mp3")

    plan = fc.plan_folder_cleanup(tmp_path)

    assert {m.dest.parent for m in plan.moves} <= {tmp_path / "D'ARBY"}
    assert not any(c.new == tmp_path / "D'Arby" for c in plan.changes)


class _FakeRekordbox:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def get_content(self, **_kw):
        class Rows:
            def all(self):
                return []
        return Rows()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_every_move_is_committed_to_rekordbox(tmp_path):
    _touch(tmp_path / "A_B" / "one.mp3")
    _touch(tmp_path / "A_B" / "two.mp3")
    db = _FakeRekordbox()

    result = fc.apply_folder_cleanup(fc.plan_folder_cleanup(tmp_path), db=db)

    assert result["moved"] == 2 and db.commits == 2


def test_a_failed_rekordbox_commit_puts_the_file_back(tmp_path):
    src = _touch(tmp_path / "A_B" / "one.mp3")
    db = _FakeRekordbox()

    def boom():
        raise RuntimeError("disk full")
    db.commit = boom  # type: ignore[method-assign]

    result = fc.apply_folder_cleanup(fc.plan_folder_cleanup(tmp_path), db=db)

    assert result["moved"] == 0 and len(result["errors"]) == 1
    assert src.exists() and db.rollbacks == 1
