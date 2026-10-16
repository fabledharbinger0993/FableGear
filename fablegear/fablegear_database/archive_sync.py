"""
fablegear_database.archive_sync — archive-first DB home + sync layer.

Implements docs/archive_first_architecture.md §3: the FableGear Archive (on
the DJ drive) holds the authoritative ``fablegear.db``; the app always runs
against a local working copy at ``~/.fablegear/fablegear.db`` (fast, safe WAL
on the internal disk) and syncs it back to the archive at the lifecycle
points described in the design doc.

Guiding rule from the doc: a healthy live DB is never automatically
overwritten. The archive is an add-only backup and durable home; the local
working copy is the write target. Restore-from-archive only happens for a
missing/corrupt local copy.

This module is feature-gated on ``config.ARCHIVE_ENABLED`` and the archive
drive being mounted — every function degrades to a no-op (returning a status
dict, never raising) when the drive isn't present, so a missing/unmounted
drive never blocks startup or normal use.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from .database import DEFAULT_DB_PATH

log = logging.getLogger(__name__)

_ARCHIVE_DB_SUBDIR = "Database"
_ARCHIVE_DB_NAME = "fablegear.db"
_ARCHIVE_DB_PREV_NAME = "fablegear.prev.db"
_ARCHIVE_DB_META_NAME = "fablegear.db.meta.json"


@dataclass
class SyncResult:
    ok: bool
    action: str  # "synced" | "hydrated" | "skipped" | "seeded" | "error"
    reason: str = ""
    detail: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Paths ───────────────────────────────────────────────────────────────────

def _archive_root() -> Path | None:
    """Return config.ARCHIVE_ROOT, or None if the archive is disabled.

    Imported lazily (matches the rest of the codebase's pattern for reading
    ``config`` inside functions) so importing this module never requires a
    configured environment.
    """
    from config import ARCHIVE_ENABLED, ARCHIVE_ROOT
    if not ARCHIVE_ENABLED:
        return None
    return ARCHIVE_ROOT


def archive_db_dir() -> Path | None:
    """Return ``<ARCHIVE_ROOT>/Database``, or None if archive is disabled."""
    root = _archive_root()
    if root is None:
        return None
    return root / _ARCHIVE_DB_SUBDIR


def archive_db_path() -> Path | None:
    """Return the authoritative archive copy's path, or None if disabled."""
    d = archive_db_dir()
    return None if d is None else d / _ARCHIVE_DB_NAME


def archive_db_prev_path() -> Path | None:
    d = archive_db_dir()
    return None if d is None else d / _ARCHIVE_DB_PREV_NAME


def archive_db_meta_path() -> Path | None:
    d = archive_db_dir()
    return None if d is None else d / _ARCHIVE_DB_META_NAME


def archive_drive_mounted() -> bool:
    """Best-effort check that the archive's drive is currently reachable.

    A removable drive that isn't mounted simply makes its root path not
    exist — this is deliberately loose (no statvfs/mount-table inspection)
    to match how the rest of the codebase treats "is the drive there".
    """
    root = _archive_root()
    if root is None:
        return False
    try:
        return root.parent.exists()
    except OSError:
        return False


# ── Integrity ───────────────────────────────────────────────────────────────

def integrity_check(path: Path) -> bool:
    """Run ``PRAGMA quick_check`` against *path*. False on any failure,
    including a missing/zero-byte/unopenable file — never raises."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("archive_sync: integrity_check failed for %s: %s", path, exc)
        return False


def _checksum(path: Path) -> str:
    h = sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_meta(meta_path: Path) -> dict:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_meta(meta_path: Path, payload: dict) -> None:
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(meta_path)


def _app_version() -> str:
    try:
        from _version import __version__
        return __version__
    except Exception:
        return "unknown"


def _savepoint_aside(path: Path, *, reason: str) -> Path | None:
    """Move an existing (possibly damaged) local DB aside to a timestamped
    Savepoint instead of deleting it. Returns the savepoint path, or None if
    there was nothing to move or the move failed."""
    if not path.exists():
        return None
    try:
        from config import SAVEPOINTS_DIR
        SAVEPOINTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        dest = SAVEPOINTS_DIR / f"fablegear.local.{reason}.{stamp}.db"
        shutil.move(str(path), str(dest))
        log.info("archive_sync: moved local DB aside to savepoint %s (%s)", dest, reason)
        return dest
    except OSError as exc:
        log.warning("archive_sync: could not savepoint local DB aside: %s", exc)
        return None


# ── Core operations ─────────────────────────────────────────────────────────

def hydrate_working_copy_from_archive(*, local_path: Path = DEFAULT_DB_PATH) -> SyncResult:
    """
    Startup check (doc §3.2, "Startup / open"):

      * local missing or fails integrity check + archive has a good copy
        -> restore from archive (any existing/damaged local is savepointed
        first, never deleted).
      * local healthy -> left alone; archive is never read over a healthy
        live DB, even if it looks newer.
      * drive not mounted -> local used as-is, working-offline.

    Never raises. Safe to call on every startup.
    """
    if not archive_drive_mounted():
        return SyncResult(ok=True, action="skipped", reason="archive drive not mounted")

    a_path = archive_db_path()
    if a_path is None:
        return SyncResult(ok=True, action="skipped", reason="archive disabled")

    local_ok = integrity_check(local_path)
    if local_ok:
        return SyncResult(ok=True, action="skipped", reason="local working copy is healthy")

    if not integrity_check(a_path):
        return SyncResult(
            ok=False, action="skipped",
            reason="local missing/corrupt and archive copy is missing/corrupt too",
        )

    savepoint = _savepoint_aside(
        local_path, reason=("missing" if not local_path.exists() else "corrupt"),
    )
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = local_path.with_name(local_path.name + ".hydrate.tmp")
        shutil.copy2(a_path, tmp)
        if not integrity_check(tmp):
            tmp.unlink(missing_ok=True)
            return SyncResult(
                ok=False, action="error",
                reason="copied archive DB failed integrity check post-copy",
            )
        tmp.replace(local_path)
    except OSError as exc:
        return SyncResult(ok=False, action="error", reason=str(exc))

    log.info("archive_sync: hydrated local working copy from archive (%s)", a_path)
    return SyncResult(
        ok=True, action="hydrated",
        reason="restored local working copy from archive",
        detail={"savepoint": str(savepoint) if savepoint else None, "source": str(a_path)},
    )


def sync_db_to_archive(*, local_path: Path = DEFAULT_DB_PATH, history_keep: int = 2) -> SyncResult:
    """
    Checkpoint / clean-shutdown sync (doc §3.2, "Checkpoint / clean shutdown
    / after each tool run"):

      1. Verify the source (integrity_check on the live local DB). A corrupt
         source aborts the backup — existing good archive copies are left
         untouched.
      2. Write to a temp file on the archive filesystem, verify its checksum
         matches the source.
      3. Rotate: fablegear.db -> fablegear.prev.db (add-only — the previous
         generation is only replaced *after* the new one is fully, atomically
         in place as "latest").
      4. Atomically rename temp -> Database/fablegear.db, write meta.json.

    Never raises.
    """
    if not archive_drive_mounted():
        return SyncResult(ok=True, action="skipped", reason="archive drive not mounted")

    db_dir = archive_db_dir()
    a_path = archive_db_path()
    prev_path = archive_db_prev_path()
    meta_path = archive_db_meta_path()
    if db_dir is None or a_path is None:
        return SyncResult(ok=True, action="skipped", reason="archive disabled")

    if not local_path.exists():
        return SyncResult(ok=True, action="skipped", reason="no local working copy to sync yet")

    if not integrity_check(local_path):
        log.warning(
            "archive_sync: local working copy failed integrity_check — "
            "aborting sync, existing archive backups left untouched",
        )
        return SyncResult(ok=False, action="error", reason="local working copy failed integrity check")

    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        tmp = db_dir / f".{_ARCHIVE_DB_NAME}.tmp"
        shutil.copy2(local_path, tmp)
        with open(tmp, "rb") as f:
            import os as _os
            _os.fsync(f.fileno())

        source_checksum = _checksum(local_path)
        if _checksum(tmp) != source_checksum:
            tmp.unlink(missing_ok=True)
            return SyncResult(ok=False, action="error", reason="checksum mismatch after copy to archive")

        # Was there already a "latest"? If so it becomes fablegear.prev.db —
        # add-only rotation, never below the two-generation guarantee.
        if a_path.exists():
            prev_path.unlink(missing_ok=True)
            a_path.replace(prev_path)

        tmp.replace(a_path)
        _write_meta(meta_path, {
            "synced_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "app_version": _app_version(),
            "checksum_sha256": source_checksum,
            "size_bytes": local_path.stat().st_size,
        })
    except OSError as exc:
        return SyncResult(ok=False, action="error", reason=str(exc))

    log.info("archive_sync: synced local working copy -> %s", a_path)
    return SyncResult(
        ok=True, action="synced",
        reason="local working copy backed up to archive",
        detail={"destination": str(a_path), "checksum": source_checksum},
    )


def seed_archive_from_existing_local(*, local_path: Path = DEFAULT_DB_PATH) -> SyncResult:
    """
    First-run migration (doc §7, stage 4): if this is a pre-archive-first
    install — ``~/.fablegear/fablegear.db`` already exists and holds real
    data, but the archive has no ``Database/fablegear.db`` yet — seed the
    archive from the existing local copy. The local file becomes, from this
    point on, the working copy of a freshly-seeded archive home.

    A thin, explicitly-named wrapper around sync_db_to_archive(): the
    underlying operation is identical (verify local -> write archive), the
    only difference is the caller's intent (bootstrap vs. steady-state
    checkpoint), which is preserved in the returned action for logging/UI.
    """
    if not archive_drive_mounted():
        return SyncResult(ok=True, action="skipped", reason="archive drive not mounted")
    a_path = archive_db_path()
    if a_path is None:
        return SyncResult(ok=True, action="skipped", reason="archive disabled")
    if a_path.exists():
        return SyncResult(ok=True, action="skipped", reason="archive already has a Database/fablegear.db")
    if not local_path.exists():
        return SyncResult(ok=True, action="skipped", reason="no existing local DB to migrate")

    result = sync_db_to_archive(local_path=local_path)
    if result.ok and result.action == "synced":
        return SyncResult(
            ok=True, action="seeded",
            reason="first-run migration: seeded archive from existing local library DB",
            detail=result.detail,
        )
    return result


def startup_sync_check(*, local_path: Path = DEFAULT_DB_PATH) -> SyncResult:
    """
    Single entry point wired into app startup (doc §3.2 + §7 stage 4, in
    order):

      1. hydrate_working_copy_from_archive() — restores local only if it's
         missing/corrupt and the archive has a good copy.
      2. seed_archive_from_existing_local() — if the archive has no
         Database/fablegear.db yet but a healthy local one exists, seed the
         archive from it (first run of the archive-first build).

    Both steps are no-ops (action="skipped") when the drive isn't mounted or
    the archive is disabled, so this is always safe to call unconditionally
    at startup.
    """
    hydrate_result = hydrate_working_copy_from_archive(local_path=local_path)
    if hydrate_result.action == "hydrated":
        return hydrate_result
    seed_result = seed_archive_from_existing_local(local_path=local_path)
    if seed_result.action == "seeded":
        return seed_result
    return hydrate_result if not hydrate_result.ok else seed_result
