"""
fablegear / chop_shop/dead_file_scanner.py  (canonical copy)

Finds audio files on disk that are not referenced in any available
Rekordbox database (local or device). The inverse of the DB audit's
missing-path check: files that exist on the filesystem but have no
FolderPath entry in any known database are called "dead" or untracked.

Public interface:
    scan_dead_files(roots, db_paths=None, progress_cb=None) -> DeadFileScanResult
"""

import logging
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config import AUDIO_EXTENSIONS, SKIP_DIRS, SKIP_PREFIXES

try:
    from path_guard import guard_sources as _guard_sources
except ImportError:  # imported via the chop_shop package
    from chop_shop.path_guard import guard_sources as _guard_sources

log = logging.getLogger(__name__)

_FS_CASE_INSENSITIVE: bool = platform.system() in ("Darwin", "Windows")


def _normalise(p: str) -> str:
    return p.lower() if _FS_CASE_INSENSITIVE else p


# ─── Result type ──────────────────────────────────────────────────────────────

@dataclass
class DeadFileScanResult:
    dead_files:     list[Path] = field(default_factory=list)
    total_scanned:  int = 0
    db_paths_used:  list[str] = field(default_factory=list)
    roots_checked:  list[str] = field(default_factory=list)

    @property
    def dead_count(self) -> int:
        return len(self.dead_files)

    def summary(self) -> str:
        lines = [
            "═" * 60,
            "  Dead File Scan — Untracked Audio Files",
            "═" * 60,
            "",
            f"  Roots scanned   : {len(self.roots_checked)}",
            f"  Files checked   : {self.total_scanned:,}",
            f"  Databases used  : {len(self.db_paths_used)}",
            f"  Untracked files : {self.dead_count:,}",
            "",
        ]
        if self.dead_count == 0:
            lines.append("  ✓ All audio files are referenced in at least one database.")
        else:
            lines.append(f"  These {self.dead_count:,} files are on disk but not in any database:")
            lines.append("")
            for p in self.dead_files:
                lines.append(f"    {p}")
        lines += ["", "═" * 60]
        return "\n".join(lines)


# ─── Database index builder ────────────────────────────────────────────────────

def _build_db_index(db_paths: list[Path]) -> set[str]:
    """
    Return a set of normalised FolderPath strings from all provided databases.
    Silently skips databases that don't exist or fail to open.
    """
    known: set[str] = set()
    for db_path in db_paths:
        if not db_path.exists():
            log.debug("Dead-file scan: DB not found, skipping: %s", db_path)
            continue
        try:
            from pyrekordbox import Rekordbox6Database
            db = Rekordbox6Database(str(db_path), unlock=True)
            for track in db.get_content().all():
                fp = getattr(track, "FolderPath", None)
                if fp:
                    known.add(_normalise(fp))
            log.debug("Dead-file scan: loaded %d paths from %s", len(known), db_path.name)
        except Exception as exc:
            # Fail loud: a silently skipped DB shrinks the known-paths set,
            # which misclassifies its tracks as "dead" — and dead-file results
            # can feed prune decisions. Better to abort the scan than to
            # report false positives against an unreadable database.
            raise RuntimeError(f"Dead-file scan: could not read DB {db_path}") from exc
    return known


# ─── Filesystem walk ──────────────────────────────────────────────────────────

def _walk_audio(root: Path) -> list[Path]:
    """Return all audio files under root, respecting skip rules."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for filename in filenames:
            if any(filename.startswith(p) for p in SKIP_PREFIXES):
                continue
            f = dirpath / filename
            if f.suffix.lower() in AUDIO_EXTENSIONS:
                files.append(f)
    return files


# ─── Public API ───────────────────────────────────────────────────────────────

def scan_dead_files(
    roots: list[Path],
    db_paths: list[Path] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    archive=None,
) -> DeadFileScanResult:
    """
    Scan roots for audio files not referenced in any of db_paths.

    Parameters
    ----------
    roots:        Directories to scan for audio files.
    db_paths:     Database files to check against. Defaults to LOCAL_DB + DJMT_DB.
    progress_cb:  Optional callback(scanned, total) called periodically.
    """
    _guard_sources(roots, "the dead-file scanner")
    if db_paths is None:
        from config import LOCAL_DB, DJMT_DB
        db_paths = [p for p in (LOCAL_DB, DJMT_DB) if p is not None]

    result = DeadFileScanResult(
        db_paths_used=[str(p) for p in db_paths if p.exists()],
        roots_checked=[str(r) for r in roots],
    )

    known = _build_db_index(db_paths)
    if not known:
        log.warning("Dead-file scan: no database paths available — all files will appear untracked")

    # Collect all audio files across all roots
    all_files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            log.warning("Dead-file scan: root is not a directory, skipping: %s", root)
            continue
        all_files.extend(_walk_audio(root))

    total = len(all_files)
    result.total_scanned = total
    log.info("Dead-file scan: %d audio files found across %d root(s)", total, len(roots))

    dead: list[Path] = []
    for i, f in enumerate(all_files, start=1):
        if _normalise(str(f)) not in known:
            dead.append(f)
        if progress_cb and i % 100 == 0:
            progress_cb(i, total)

    if progress_cb:
        progress_cb(total, total)

    result.dead_files = dead
    log.info("Dead-file scan complete: %d untracked / %d total", len(dead), total)

    if archive is not None:
        archive.log_operation(
            "dead_file_scan",
            metadata={
                "dead": len(dead),
                "total_scanned": total,
                "roots": [str(r) for r in roots],
            },
        )

    return result
