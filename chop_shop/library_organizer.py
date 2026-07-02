"""
fablegear / library_organizer.py

Consolidates a music library into a canonical folder structure:

  <target>/
  ├── <Artist>/
  │   └── <Album>/
  │       └── track.mp3           (Artist / Album / Track)
  │   └── track.mp3               (Artist / Track — when no album tag)
  ├── Orphaned Tracks/
  │   └── <YYYY>/
  │       └── track.mp3           (no artist tag)
  └── Live Sets & Mixes/
      └── <YYYY>/
          └── mix.mp3             (duration >= threshold, default 15 min)

Rules
-----
- Joint releases keep their combined artist string as the folder name
  (e.g. "Daft Punk & Basement Jaxx" → one folder).
- For artist folder naming, TPE2 (album artist) is preferred over
  TPE1 (track artist) to avoid "Artist feat. X" folder proliferation.
- Files without an album tag sit directly in the artist folder.
- If a destination file already exists with the same size → skip (duplicate).
- If it exists with a different size → rename with _1, _2, … suffix.
- After all moves, empty directories are pruned bottom-up from source.
"""

import json
import logging
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

MIX_FOLDER        = "Live Sets & Mixes"
ORPHAN_FOLDER     = "Orphaned Tracks"
MIX_THRESHOLD_SEC = 900.0   # 15 minutes

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')
_MULTI_SPACE  = re.compile(r' {2,}')

# Camelot / Open-Key prefix  e.g. "10A - ", "10A 9A - ", "2B 3B - "
# Matches one or more  <1-2 digits><A|B>  groups followed by a dash separator.
_KEY_PREFIX = re.compile(r'^(?:\d{1,2}[ABab]\s+)*\d{1,2}[ABab]\s*[-–]\s*')


# ─── Result type ──────────────────────────────────────────────────────────────

@dataclass
class MoveResult:
    src:    Path
    dest:   Path | None
    action: str    # "moved" | "conflict_renamed" | "skipped" | "error" | "dry_run"
    reason: str = ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sanitize_folder(name: str, max_len: int = 100) -> str:
    """Strip filesystem-unsafe characters and return a clean folder name."""
    name = _UNSAFE_CHARS.sub(" ", name)
    name = _MULTI_SPACE.sub(" ", name).strip().strip(".")
    return (name[:max_len] if name else "Unknown")


# A single path component is capped at 255 bytes on macOS/APFS and exFAT. Stay
# well under that so the organizer can still append a "_NN" conflict suffix.
_MAX_NAME_BYTES = 200

# Matches a stem that is one phrase repeated back-to-back — e.g. the corruption
# mode where a name is its own data copied several times: "Title Title Title",
# "TitleTitle", "Title_Title". `.+?` is non-greedy so it captures the smallest
# repeating unit.
_TANDEM_REPEAT = re.compile(r'^(.+?)(?:[\s._\-]*\1)+$')


def _cap_bytes(stem: str, max_bytes: int) -> str:
    """Truncate so the UTF-8 encoding fits in max_bytes, on a character boundary."""
    encoded = stem.encode("utf-8")
    if len(encoded) <= max_bytes:
        return stem
    return encoded[:max_bytes].decode("utf-8", "ignore").rstrip(" ._-")


def _sanitize_filename(name: str) -> str:
    """
    Clean a destination *file* name so pathological names move successfully
    instead of failing with ENAMETOOLONG. Unsafe characters are always stripped;
    the more aggressive repairs (collapsing repeated data, hard truncation) only
    kick in when the name actually exceeds the filesystem's per-component limit,
    so ordinary names — including legitimate repeated titles like
    "New York New York" — pass through untouched. The extension is preserved.
    """
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    stem = _UNSAFE_CHARS.sub(" ", stem)
    stem = _MULTI_SPACE.sub(" ", stem).strip()

    budget = max(_MAX_NAME_BYTES - len(suffix.encode("utf-8")) - 4, 1)
    if len(stem.encode("utf-8")) > budget:
        # Over the component limit. First try collapsing a name that is the same
        # data repeated back-to-back (cuts the corruption away cleanly); then
        # hard-truncate by bytes if it is still too long.
        m = _TANDEM_REPEAT.match(stem)
        if m and len(m.group(1).strip()) >= 4:
            stem = m.group(1).strip()
        stem = _cap_bytes(stem, budget)

    return (stem + suffix) if stem else ("untitled" + suffix)


def _collapse_repeats(text: str) -> str:
    """
    Collapse an artist string that is one phrase repeated back-to-back down to a
    single copy — handles exact repeats and a dangling partial final repeat:
      "the timewriter the timewriter the timewriter the" -> "the timewriter"

    Requires 3+ repeats so legitimate doubles (e.g. "Duran Duran") are preserved.
    """
    tokens = text.split()
    n = len(tokens)
    for unit_len in range(1, (n // 3) + 1):
        unit = tokens[:unit_len]
        reps = 0
        while tokens[reps * unit_len:(reps + 1) * unit_len] == unit:
            reps += 1
        remainder = tokens[reps * unit_len:]
        if reps >= 3 and remainder == unit[:len(remainder)]:
            return " ".join(unit)
    return text


def _normalize_artist(name: str) -> str:
    """
    Strip RekordBox / Camelot key prefixes that sometimes get written into
    artist tags, e.g. "10A 9A - Kenny Dope" → "Kenny Dope", and collapse
    repeated-data artist strings (a corruption mode) down to one copy.

    Applies the strip in a loop to handle doubled prefixes like
    "12A 11A - 12A 11A - Brother 2 Brother".
    """
    while True:
        stripped = _KEY_PREFIX.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    name = _collapse_repeats(name)
    return name or "Unknown"


def _folder_artist(path: Path) -> str | None:
    """
    Return the best artist string for folder naming.
    Prefers TPE2 (album artist) over TPE1 (track artist) so that
    'Artist feat. Guest' tracks land in the primary artist's folder.
    Falls back gracefully if tags can't be read.
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        try:
            audio = MutagenFile(str(path), easy=False)
        except Exception:
            # MPEG sync failure on MP3 — try reading just the ID3 header
            if not str(path).lower().endswith('.mp3'):
                return None
            try:
                tags = ID3(str(path))
            except Exception:
                return None
            # fall through to tag extraction below using the ID3 object as tags
            for frame_id in ("TPE2", "TPE1"):
                frame = tags.get(frame_id)
                if frame is not None:
                    text = getattr(frame, "text", None)
                    val = str(text[0]).strip() if text else str(frame).strip()
                    if val:
                        return _normalize_artist(val)
            return None

        if audio is None or audio.tags is None:
            return None
        tags = audio.tags

        # ID3-style (MP3, AIFF, WAV)
        for frame_id in ("TPE2", "TPE1"):
            frame = tags.get(frame_id)
            if frame is not None:
                text = getattr(frame, "text", None)
                val = str(text[0]).strip() if text else str(frame).strip()
                if val:
                    return _normalize_artist(val)

        # Vorbis-style (FLAC, OGG)
        for key in ("albumartist", "album_artist", "artist"):
            val = tags.get(key)
            if val:
                s = str(val[0]).strip() if isinstance(val, list) else str(val).strip()
                if s:
                    return _normalize_artist(s)
    except Exception as exc:
        log.warning("Tag extraction failed for %s: %s", path, exc)
    return None


def _year_str(path: Path, tagged_year: int | None) -> str:
    """Return a 4-digit year string from tag or fall back to file mtime year."""
    if tagged_year:
        return str(tagged_year)
    try:
        return str(time.localtime(path.stat().st_mtime).tm_year)
    except OSError:
        return "Unknown Year"


def _canonical_dest(
    src: Path,
    target: Path,
    track,
    threshold: float,
) -> Path:
    """Compute the canonical destination path for a track (no I/O performed)."""
    year  = _year_str(src, track.year)
    fname = _sanitize_filename(src.name)

    # Long-form content (mixes, live sets, radio shows)
    if track.duration_seconds is not None and track.duration_seconds >= threshold:
        return target / MIX_FOLDER / year / fname

    # Resolve artist for folder naming (normalize away any key prefixes)
    raw_artist = _folder_artist(src) or track.artist
    artist = _normalize_artist(raw_artist) if raw_artist else None

    # No artist from tags — try filename-based extraction as last resort.
    # Handles "Artist - Title.mp3" and strips Pioneer _PN suffixes.
    if not artist or not artist.strip():
        stem = re.sub(r'_PN\s*\d*$', '', src.stem, flags=re.IGNORECASE).strip()
        stem = re.sub(r'^\d+[\s.\-]+', '', stem).strip()
        if ' - ' in stem:
            candidate = stem.split(' - ', 1)[0].strip()
            artist = _normalize_artist(candidate) if candidate else None

    if not artist or not artist.strip():
        return target / ORPHAN_FOLDER / year / fname

    # Normal: Artist / Album / Track  or  Artist / Track
    artist_dir = _sanitize_folder(artist)
    if track.album and track.album.strip():
        return target / artist_dir / _sanitize_folder(track.album) / fname
    return target / artist_dir / fname


def _resolve_dest(src: Path, dest: Path) -> tuple[Path | None, str]:
    """
    Returns (final_dest, action).

    action is one of:
      "moved"            — destination is free
      "skipped"          — same-size file exists (likely duplicate)
      "conflict_renamed" — different file exists; numbered suffix applied
      "error"            — could not find a free slot (extremely unlikely)
    """
    if not dest.exists():
        return dest, "moved"

    # Same size → treat as duplicate, skip
    try:
        if dest.stat().st_size == src.stat().st_size:
            return None, "skipped"
    except OSError:
        pass

    # Different file — find a numbered rename slot
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 100):
        candidate = dest.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate, "conflict_renamed"

    return None, "error"


# ─── Main organizer ───────────────────────────────────────────────────────────

def _forbidden_source_reason(source: Path) -> str | None:
    """Return why *source* is unsafe to organize from, or None if it's fine.

    Whole external drives (/Volumes/<name>) are legitimate DJ sources.
    System roots, the user profile itself, and app-data trees are not:
    music lives in folders, not at the top of an OS.
    """
    try:
        src = source.expanduser().resolve(strict=False)
    except OSError:
        return "path cannot be resolved"
    home = Path.home().resolve(strict=False)

    if src == Path("/"):
        return "this is the filesystem root"
    if src == home:
        return ("this is your entire home folder — it contains app data, dev "
                "projects, and system containers, not just music. Point the "
                "organizer at a music folder instead.")
    if src in (Path("/Users"), Path("/System"), Path("/Applications"),
               Path("/Library"), Path("/private"), Path("/Volumes")):
        return "this is an operating-system area, not a music folder"
    if src == home / "Library" or (home / "Library") in src.parents:
        return "~/Library holds application data — never music to organize"
    if home in src.parents and src.name == "Library":
        return "Library folders hold application data"
    return None


def organize_library(
    sources: "Path | list[Path]",
    target: Path,
    *,
    mode: str = "assimilate",
    dry_run: bool = True,
    max_workers: int = 1,
    mix_threshold_sec: float = MIX_THRESHOLD_SEC,
    archive=None,
) -> list[MoveResult]:
    """
    Scan one or more source directories, compute the canonical destination for
    every audio file, and move or copy files into the Artist / Album / Track
    hierarchy under *target*.

    Parameters
    ----------
    sources : Path | list[Path]
        One directory or a list of directories to scan.  All are scanned in a
        single pass and their files are merged before processing begins.
    target : Path
        Root of the organised library (e.g. /path/to/music).
    mode : str
        ``"assimilate"`` (default) — **move** files to target, delete confirmed
        source duplicates, and prune empty source directories afterwards.
        Use this to fully consolidate a library in place.

        ``"integrate"`` — **copy** files to target without touching the source
        at all.  Nothing is deleted or pruned from any source directory.  Use
        this when you want to pull music off a second drive without altering it.
    dry_run : bool
        If True (default), compute and report planned changes without touching
        the filesystem.  Run with dry_run=True first to preview.
    max_workers : int
        Parallel I/O workers for the move/copy phase (default 1 = sequential).
    mix_threshold_sec : float
        Tracks at or above this duration (seconds) are routed to
        Live Sets & Mixes instead of the normal Artist / Album tree.
        Default 900 = 15 minutes.
    """
    from scanner import scan_directory

    source_list: list[Path] = [sources] if isinstance(sources, Path) else list(sources)

    # ── Source guardrails ─────────────────────────────────────────────────
    # Assimilate mode moves files out of and prunes folders under EVERY
    # source root. A source like "/", "/Users", or the home folder makes the
    # organizer crawl app containers, dev checkouts, and OS internals — it
    # must only ever be pointed at music locations. Refuse loudly.
    for s in source_list:
        err = _forbidden_source_reason(Path(s))
        if err:
            raise ValueError(f"Refusing to organize from {s}: {err}")

    tracks: list = []
    for s in source_list:
        tracks.extend(list(scan_directory(s)))
    total  = len(tracks)
    results: list[MoveResult] = []

    if total == 0:
        log.info("No audio files found under %s", source_list)
        return results

    log.info(
        "Organizing %d files  sources=%s  target=%s  mode=%s  dry_run=%s  workers=%d",
        total, [str(s) for s in source_list], target, mode, dry_run, max_workers,
    )

    done = moved = skipped = conflicts = errors = 0

    def _emit() -> None:
        print(
            "FABLEGEAR_PROGRESS: " + json.dumps({
                "done":      done,
                "total":     total,
                "remaining": total - done,
                "moved":     moved,
                "skipped":   skipped,
                "conflicts": conflicts,
                "errors":    errors,
            }),
            flush=True,
        )

    def _process(track) -> MoveResult:
        dest = _canonical_dest(track.path, target, track, mix_threshold_sec)

        # Already in the right place (in-place reorganisation with correct structure)
        if track.path.resolve() == dest.resolve():
            return MoveResult(src=track.path, dest=dest,
                              action="skipped", reason="already in place")

        if dry_run:
            rel = dest.relative_to(target) if dest.is_relative_to(target) else dest
            return MoveResult(src=track.path, dest=dest,
                              action="dry_run", reason=str(rel))

        # Ensure destination directory exists
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return MoveResult(src=track.path, dest=dest,
                              action="error", reason=f"mkdir failed: {e}")

        final, action = _resolve_dest(track.path, dest)

        if final is None:
            if action == "skipped":
                if mode == "assimilate":
                    # Identical copy confirmed at canonical destination.
                    # Remove the source so the source tree can be pruned cleanly.
                    try:
                        track.path.unlink()
                        return MoveResult(src=track.path, dest=dest,
                                          action="skipped", reason="duplicate removed from source")
                    except Exception as e:
                        return MoveResult(src=track.path, dest=dest,
                                          action="error", reason=f"unlink failed: {e}")
                else:
                    # integrate mode — source is never touched
                    return MoveResult(src=track.path, dest=dest,
                                      action="skipped", reason="duplicate at destination — source kept")
            return MoveResult(src=track.path, dest=dest,
                              action="error", reason="no rename slot found")

        try:
            if mode == "integrate":
                shutil.copy2(str(track.path), str(final))
            else:
                shutil.move(str(track.path), str(final))
            return MoveResult(src=track.path, dest=final, action=action)
        except Exception as e:
            return MoveResult(src=track.path, dest=final,
                              action="error", reason=str(e))

    def _tally(r: MoveResult) -> None:
        nonlocal done, moved, skipped, conflicts, errors
        done += 1
        if r.action in ("moved", "dry_run"):
            moved += 1
        elif r.action == "conflict_renamed":
            moved += 1
            conflicts += 1
        elif r.action == "skipped":
            skipped += 1
        elif r.action == "error":
            errors += 1
        # Journal the mutation the moment it lands — an interrupted run must
        # still leave a complete record of every move it made. (_tally runs on
        # the result-collection thread, so archive writes are serialized.)
        if archive is not None and not dry_run and r.dest \
                and r.action in ("moved", "conflict_renamed"):
            try:
                rec = archive.get_content_by_path(str(r.src))
                if rec and rec.id is not None:
                    archive.relink_content(rec.id, str(r.dest))
                archive.log_operation(
                    "organize", str(r.dest), status="ok",
                    metadata={"from": str(r.src), "action": r.action, "mode": mode},
                )
            except Exception as exc:
                log.warning("Archive update failed for organize %s: %s", r.src, exc)

    _emit()

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_process, track): track for track in tracks}
            for future in as_completed(futures):
                try:
                    r = future.result()
                except Exception as exc:
                    r = MoveResult(src=futures[future].path, dest=None,
                                   action="error", reason=str(exc))
                results.append(r)
                _tally(r)
                _emit()
    else:
        for i, track in enumerate(tracks):
            r = _process(track)
            log.info("[%d/%d] %-16s %s", i + 1, total, r.action.upper(), track.path.name)
            results.append(r)
            _tally(r)
            _emit()

    # Prune ONLY the directories this run emptied (folders we moved or
    # deleted files out of, plus their ancestors) — assimilate mode only.
    # Pre-existing empty directories elsewhere under the source are none of
    # our business. integrate mode never modifies the source.
    if not dry_run and mode == "assimilate":
        emptied: set = set()
        for r in results:
            if r.action in ("moved", "conflict_renamed", "skipped") and r.src:
                emptied.add(Path(r.src).parent)
        for s in source_list:
            _prune_emptied_dirs(s, emptied)

    if archive is not None and not dry_run:
        if moved:
            archive.log_operation(
                "organize_batch",
                metadata={
                    "sources": [str(s) for s in source_list],
                    "target": str(target),
                    "mode": mode,
                    "moved": moved,
                    "skipped": skipped,
                    "conflicts": conflicts,
                    "errors": errors,
                },
            )

    return results


# OS-generated metadata that should not keep an otherwise-empty source folder
# alive. These accumulate on macOS and on exFAT drives (e.g. a Samsung SSD) and
# are exactly why "empty" folders survive a move.
_DIR_JUNK = {
    ".DS_Store", "Thumbs.db", "desktop.ini", ".localized",
    ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
}


def _is_dir_junk(entry: Path) -> bool:
    return entry.name in _DIR_JUNK or entry.name.startswith("._")


def _prune_emptied_dirs(root: Path, emptied_dirs: set) -> None:
    """
    Remove source directories this run emptied, bottom-up.

    Only directories we actually removed files FROM (and, as they empty out,
    their ancestors up to — never including — the source root) are candidates.
    A pre-existing empty folder anywhere else under the source is left exactly
    as we found it: the organizer's cleanup follows its own footprints, it
    does not sweep the neighbourhood.

    A candidate counts as empty when the only things left in it are
    OS-metadata junk (.DS_Store, AppleDouble ._* files, Thumbs.db, …); that
    junk is deleted so the now-truly-empty folder can be pruned. Folders that
    still hold real files (cover art, docs, stray audio) are left untouched.
    """
    root = root.resolve(strict=False)

    # Deepest paths first so children empty out before their parents are tried.
    candidates = sorted(
        {d.resolve(strict=False) for d in emptied_dirs},
        key=lambda p: len(p.parts),
        reverse=True,
    )
    seen: set = set()
    while candidates:
        p = candidates.pop(0)
        if p in seen:
            continue
        seen.add(p)
        if p == root or root not in p.parents:
            continue  # never the root itself, never anything outside it
        try:
            entries = list(p.iterdir())
            if any(not _is_dir_junk(e) for e in entries):
                continue  # real content remains — leave the folder alone
            for junk in entries:
                try:
                    junk.unlink()
                except OSError as exc:
                    log.warning("Could not remove junk file %s: %s", junk, exc)
            p.rmdir()
            log.info("Pruned emptied dir: %s", p)
            # The parent may now be empty because of us — consider it next.
            if p.parent not in seen:
                candidates.append(p.parent)
        except OSError as exc:
            log.warning("Could not remove emptied dir %s: %s", p, exc)
