"""
rekordbox_safe_write — the safety envelope for writing to a live Rekordbox
``master.db``.

Writing to the live library is exactly what destroyed this collection once
(Rekordbox's duplicate tool deleting originals + duplicates). Every FableGear
tool that writes to a ``master.db`` must therefore:

1. refuse to run while Rekordbox is open (it holds the DB and would corrupt on
   concurrent writes),
2. take a **verified** backup of ``master.db`` (+ its ``-wal``/``-shm``
   sidecars) before the first write — verified means the copy's size matches
   the source, so a truncated backup can't masquerade as a safety net,
3. record a manifest so the write is undoable.

This module hoists that pattern out of the individual writers (push, import)
so it is written once, tested once, and reused by any future ``master.db``
writer. Use it as a context manager::

    with safe_master_write(target, tag="push",
                           manifest_dir=PUSH_MANIFESTS) as ctx:
        rep = do_the_write(ctx.target)
        ctx.record_manifest({"folder_name": name, "crates": rep.crates})

The context manager yields *after* the verified backup, so the body only runs
once the safety net is in place.
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LIVE_DB = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
BACKUP_ROOT = Path.home() / ".fablegear" / "rekordbox_master_backups"

# master.db carries two SQLite sidecars; both must be backed up alongside it.
_DB_SIDECARS = ("", "-wal", "-shm")


class SafeWriteError(RuntimeError):
    """Raised when the safety preconditions for a live write are not met."""


def rekordbox_running() -> bool:
    """True if the Rekordbox app is currently running (holds master.db).

    Delegates to db_connection.rekordbox_is_running() — the single
    canonical implementation (previously reimplemented separately here,
    in cli.py, and in db_connection.py itself, with inconsistent
    fail-safe behavior between them).
    """
    from db_connection import rekordbox_is_running
    return rekordbox_is_running()


def backup_master_db(target: Path, tag: str) -> Path:
    """Copy ``target`` (+ ``-wal``/``-shm``) into a fresh timestamped backup
    directory, verifying the main file copied at full size.

    Returns the backup directory. Raises :class:`SafeWriteError` if the primary
    file's backup size does not match the source (a truncated/short copy — e.g.
    a full disk — must never be mistaken for a good backup).
    """
    target = Path(target)
    bdir = BACKUP_ROOT / f"{datetime.now():%Y%m%d_%H%M%S}_{tag}"
    bdir.mkdir(parents=True, exist_ok=True)
    for suf in _DB_SIDECARS:
        src = Path(str(target) + suf)
        if not src.is_file():
            continue
        dst = bdir / src.name
        shutil.copy2(src, dst)
        if suf == "" and dst.stat().st_size != src.stat().st_size:
            raise SafeWriteError(
                f"Backup size mismatch for {src.name}: "
                f"{dst.stat().st_size} != {src.stat().st_size} — refusing to proceed."
            )
    return bdir


class SafeWriteContext:
    """Handle yielded by :func:`safe_master_write`. Carries the resolved target
    and the verified backup dir, and writes the undo manifest."""

    def __init__(self, target: Path, backup_dir: Path, manifest_dir: Path | None):
        self.target = target
        self.backup_dir = backup_dir
        self.manifest_dir = Path(manifest_dir) if manifest_dir else BACKUP_ROOT
        self.manifest_path: Path | None = None

    def record_manifest(self, data: dict) -> Path:
        """Write an undo manifest. ``target``/``backup``/``timestamp`` are filled
        in automatically if the caller didn't supply them. Returns its path."""
        payload = dict(data)
        payload.setdefault("target", str(self.target))
        payload.setdefault("backup", str(self.backup_dir))
        payload.setdefault("timestamp", datetime.now().isoformat())
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        path = self.manifest_dir / f"{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(payload, indent=2))
        self.manifest_path = path
        return path


@contextmanager
def safe_master_write(target: str | Path | None = None, *, tag: str = "write",
                      manifest_dir: str | Path | None = None,
                      require_closed: bool = True):
    """Context manager wrapping a live ``master.db`` write.

    On entry: verifies Rekordbox is closed (unless ``require_closed=False``),
    confirms the target exists, and takes a verified backup. Yields a
    :class:`SafeWriteContext`. Raises :class:`SafeWriteError` on any
    precondition failure (caller maps it to a CLI error).
    """
    tgt = Path(target) if target else LIVE_DB
    if require_closed and rekordbox_running():
        raise SafeWriteError("Rekordbox is running — close it before writing to master.db.")
    if not tgt.is_file():
        raise SafeWriteError(f"Target master.db not found: {tgt}")
    backup_dir = backup_master_db(tgt, tag)
    yield SafeWriteContext(tgt, backup_dir, manifest_dir)
