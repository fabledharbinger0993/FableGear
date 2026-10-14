"""
Re-Name, folder step: give a library's folders clean, readable names.

File names are cleaned by `renamer`; nothing cleaned the folders that hold them, so names like
"Terence Trent D’Arby", "TG037 TG037 TG037 Aback TG037" or "Artist_-_Title_[WEB]" stayed as
they came. This step plans a cleaner name for every folder under a root and, when asked to
apply it, moves the files across.

Two phases, so a preview and a real run can never disagree:
  plan_folder_cleanup(root)  -> FolderPlan   (reads the disk, changes nothing)
  apply_folder_cleanup(plan, ...)            (moves files, relinks the databases)

Rules (all deliberately conservative -- a name is only changed when the cleaned form is clearly
the same name minus noise):
  * typographic punctuation becomes plain ASCII (same table as file names)
  * underscores become spaces; characters a file system dislikes are removed
  * a word or phrase repeated three or more times collapses to one copy
  * a trailing "[WEB]", "(320kbps)", "[FLAC]" style release tag is dropped
  * two folders that clean to the same name (ignoring case) are merged, never overwritten
Names that are merely odd (truncated, wrong, in another language) are left for a person.

Applying works file by file, like Re-Name: move, update Rekordbox when it is open for writing
(and undo the move if that fails), relink the FableGear database, journal the move so Undo >
Operations can put it back. Folders left empty are removed; one that still holds anything
other than OS junk is kept.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from chop_shop.tag_cleaning import collapse_repeats, normalize_punctuation
from config import SKIP_DIRS

log = logging.getLogger(__name__)

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_UNDERSCORES = re.compile(r"_+")
_SPACES = re.compile(r"\s{2,}")
_EDGE_JUNK = re.compile(r"^[\s.\-_~]+|[\s.\-_~]+$")
# A trailing release tag in brackets: "[WEB]", "(320kbps)", "[FLAC]", "(16-44)".
_RELEASE_TAG = re.compile(
    r"\s*[\[(]\s*(?:web|cd|vinyl|flac|mp3|wav|aiff?|v0|v2|\d{2,3}\s*k(?:bps)?|\d{2}\s*-\s*\d{2,3}(?:\.\d)?|"
    r"\d{2,3}\s*bit|lossless|320|256|192)\s*[\])]\s*$",
    re.IGNORECASE,
)
_MIN_WORDS_TO_COLLAPSE = 5
_DIR_JUNK = {
    ".DS_Store", "Thumbs.db", "desktop.ini", ".localized",
    ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
}


def clean_folder_name(name: str) -> str:
    """The cleaned form of one folder name, or `name` unchanged when it has nothing to clean."""
    cleaned = normalize_punctuation(name)
    cleaned = _UNSAFE.sub(" ", cleaned)
    cleaned = _UNDERSCORES.sub(" ", cleaned)
    cleaned = _SPACES.sub(" ", cleaned).strip()
    while True:
        stripped = _RELEASE_TAG.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    # Short names are left alone: "Hot Hot Hot" is a real title, a 6-word run of one code is not.
    if len(cleaned.split()) >= _MIN_WORDS_TO_COLLAPSE:
        cleaned = collapse_repeats(cleaned)
    cleaned = _EDGE_JUNK.sub("", cleaned)
    cleaned = _SPACES.sub(" ", cleaned).strip()
    return cleaned or name


@dataclass
class FileMove:
    src: Path
    dest: Path


@dataclass
class FolderChange:
    old: Path
    new: Path
    kind: str  # "rename" | "merge"


@dataclass
class FolderPlan:
    root: Path
    changes: list[FolderChange] = field(default_factory=list)
    moves: list[FileMove] = field(default_factory=list)
    conflicts: list[tuple[Path, Path]] = field(default_factory=list)  # (file, why-it-stays: existing dest)
    unchanged: int = 0

    @property
    def merges(self) -> int:
        return sum(1 for c in self.changes if c.kind == "merge")


def _subdirs(path: Path) -> list[Path]:
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return []
    return [Path(e.path) for e in entries
            if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith(".")]


def _files(path: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in sorted(os.scandir(path), key=lambda e: e.name.lower())
                if e.is_file(follow_symlinks=False) and e.name not in _DIR_JUNK and not e.name.startswith("._")]
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return []


def plan_folder_cleanup(root: Path) -> FolderPlan:
    """Work out, without touching the disk, which folders get a new name and where every file
    in them goes. The root itself is never renamed."""
    plan = FolderPlan(root=root)
    # destination folder (case-folded) -> the folder that claimed it, so merges are explicit
    claimed: dict[str, Path] = {}
    # destination file path (case-folded) -> source, so two files never land on one name
    taken: dict[str, Path] = {}

    def visit(src_dir: Path, dest_dir: Path) -> None:
        for f in _files(src_dir):
            if f.parent == dest_dir:
                continue
            dest = dest_dir / f.name
            key = str(dest).casefold()
            if key in taken or dest.exists():
                plan.conflicts.append((f, dest))
                continue
            taken[key] = f
            plan.moves.append(FileMove(f, dest))
        # Folders whose name is already clean go first, so a messy twin merges INTO the clean
        # folder rather than the other way round.
        for child in sorted(_subdirs(src_dir), key=lambda c: clean_folder_name(c.name) != c.name):
            new_name = clean_folder_name(child.name)
            target = dest_dir / new_name
            key = str(target).casefold()
            if new_name == child.name and key not in claimed:
                plan.unchanged += 1
                claimed[key] = child
                visit(child, target)
                continue
            if key in claimed and claimed[key] != child:
                plan.changes.append(FolderChange(child, target, "merge"))
            elif new_name != child.name:
                plan.changes.append(FolderChange(child, target, "rename"))
            claimed.setdefault(key, child)
            visit(child, target)

    visit(root, root)
    return plan


def apply_folder_cleanup(
    plan: FolderPlan,
    *,
    archive=None,
    db=None,
    on_progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Carry out a plan. Returns counts: moved, errors (list), removed_dirs, conflicts."""
    # Bare names, like cli.py: these modules are loaded with chop_shop/ on sys.path.
    from library_organizer import _prune_emptied_dirs
    from renamer import _sync_db_path_or_revert

    emit = on_progress or (lambda _m: None)
    moved = 0
    errors: list[str] = []
    emptied: set[Path] = set()

    for move in plan.moves:
        if should_cancel and should_cancel():
            emit("Cancelled; folders already changed stay changed (see Undo > Operations).")
            break
        try:
            move.dest.parent.mkdir(parents=True, exist_ok=True)
            if move.dest.exists():
                errors.append(f"{move.src.name}: target already exists, left in place")
                continue
            shutil.move(str(move.src), str(move.dest))
        except OSError as exc:
            errors.append(f"{move.src.name}: {exc}")
            continue
        try:
            _sync_db_path_or_revert(move.src, move.dest, db)
        except Exception as exc:
            errors.append(f"{move.src.name}: Rekordbox update failed, move undone ({exc})")
            continue
        emptied.add(move.src.parent)
        moved += 1
        if archive is not None:
            try:
                rec = archive.get_content_by_path(str(move.src))
                if rec and rec.id is not None:
                    archive.relink_content(rec.id, str(move.dest))
                archive.log_operation(
                    "rename", str(move.dest), status="ok",
                    metadata={"from": str(move.src), "action": "folder_cleanup"},
                )
            except Exception as exc:
                log.warning("Archive update failed for folder move %s: %s", move.src, exc)
        if moved % 200 == 0:
            emit(f"  {moved} files moved…")

    before = {p for p in emptied if p.exists()}
    _prune_emptied_dirs(plan.root, emptied)
    removed_dirs = sum(1 for p in before if not p.exists())
    return {"moved": moved, "errors": errors, "removed_dirs": removed_dirs, "conflicts": len(plan.conflicts)}
