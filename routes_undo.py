"""
routes_undo.py — Undo Wizard API

Flask Blueprint providing read-only endpoints for browsing job history,
DB savepoints, and pruned-file trash folders so the UI can offer one-click
undo / restore actions, plus write endpoints that perform the actual restores.
"""

import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

bp = Blueprint("undo", __name__)
log = logging.getLogger(__name__)


def _config():
    from config import BACKUP_DIR, SAVEPOINTS_DIR
    return BACKUP_DIR, SAVEPOINTS_DIR


def _is_within(child: Path, parent: Path) -> bool:
    """True if child resolves to a path inside parent. Blocks path traversal
    (../) from request-supplied path fragments reaching outside an allowed root."""
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


# ── Timeline (job history) ───────────────────────────────────────────────────

@bp.route("/api/undo/timeline")
def undo_timeline():
    from job_dispatcher import get_history

    limit = request.args.get("limit", 50, type=int)
    tool = request.args.get("tool") or None
    state = request.args.get("state") or None
    jobs = get_history(limit=limit, tool=tool, state=state)
    return jsonify(jobs)


@bp.route("/api/undo/job/<job_id>")
def undo_job_detail(job_id: str):
    from job_dispatcher import get_output

    detail = get_output(job_id, max_chars=200_000)
    if detail is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(detail)


# ── Savepoints (DB backups) ──────────────────────────────────────────────────

def _list_savepoints(directory: Path, limit: int = 100):
    if not directory.exists():
        return []
    candidates = sorted(directory.glob("master.backup_*.db"), reverse=True)
    results = []
    for p in candidates[:limit]:
        stat = p.stat()
        name = p.name
        ts_part = name.replace("master.backup_", "").replace(".db", "")
        # New backups carry microseconds (db_connection.backup_timestamp) so
        # two taken within the same second can't collide on filename;
        # existing backups on disk predate that and have none. Try both.
        for fmt in ("%Y%m%d_%H%M%S_%f", "%Y%m%d_%H%M%S"):
            try:
                dt = datetime.strptime(ts_part, fmt)
                display = dt.strftime("%b %d, %Y  %I:%M %p")
                break
            except ValueError:
                continue
        else:
            display = ts_part
        results.append({
            "filename": name,
            "path": str(p),
            "timestamp": ts_part,
            "display_time": display,
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 1),
        })
    return results


@bp.route("/api/undo/savepoints")
def undo_savepoints():
    backup_dir, savepoints_dir = _config()
    limit = request.args.get("limit", 100, type=int)
    points = _list_savepoints(backup_dir, limit)
    if str(backup_dir) != str(savepoints_dir):
        points.extend(_list_savepoints(savepoints_dir, limit))
        points.sort(key=lambda x: x["timestamp"], reverse=True)
        points = points[:limit]
    return jsonify(points)


@bp.route("/api/undo/savepoint/restore", methods=["POST"])
def undo_restore_savepoint():
    from db_connection import _backup_db, rekordbox_is_running

    body = request.get_json(silent=True) or {}
    savepoint_path = body.get("path", "").strip()
    if not savepoint_path:
        return jsonify({"error": "Missing 'path'"}), 400

    sp = Path(savepoint_path)
    # Validate the basename shape AND that the file actually lives inside a
    # known backup/savepoint directory — a request must not be able to copy an
    # arbitrary file named "master.backup_*" from anywhere over the live DB.
    backup_dir, savepoints_dir = _config()
    if (
        not sp.name.startswith("master.backup_")
        or not sp.exists()
        or not (_is_within(sp, backup_dir) or _is_within(sp, savepoints_dir))
    ):
        return jsonify({"error": "Invalid savepoint"}), 400

    if rekordbox_is_running():
        return jsonify({"error": "Rekordbox is running — close it first"}), 409

    try:
        from config import DEVICE_DB
    except Exception:
        return jsonify({"error": "FableGear not configured"}), 500

    try:
        _backup_db(DEVICE_DB)
    except Exception as exc:
        return jsonify({"error": f"Could not backup current DB: {exc}"}), 500

    try:
        # Atomic restore: copy to a temp file in the target directory, fsync,
        # then replace the DB path in one operation.
        target_dir = Path(DEVICE_DB).parent
        with tempfile.NamedTemporaryFile(
            prefix=".fablegear_restore_",
            suffix=".db",
            dir=str(target_dir),
            delete=False,
        ) as tmpf:
            tmp_path = Path(tmpf.name)
        try:
            shutil.copy2(str(sp), str(tmp_path))
            with open(tmp_path, "rb") as verify_f:
                os.fsync(verify_f.fileno())
            tmp_path.replace(Path(DEVICE_DB))
        finally:
            tmp_path.unlink(missing_ok=True)

        # master.db is WAL-mode. A -wal/-shm left over from the state we are
        # restoring AWAY FROM holds transactions newer than this savepoint —
        # SQLite does not detect that mismatch and will silently replay them
        # on the next open, undoing the restore. (Reproduced directly: a
        # restore over a live -wal brought the pre-restore data straight
        # back, with no error of any kind.) Every sidecar at the target must
        # therefore end this restore in one of exactly two states: replaced
        # with the matching sidecar from THIS savepoint, or removed entirely
        # — never left over from the state being restored away from.
        for suffix in ("-wal", "-shm"):
            sp_sidecar = sp.parent / (sp.name + suffix)
            live_sidecar = Path(DEVICE_DB).parent / (Path(DEVICE_DB).name + suffix)
            if sp_sidecar.is_file():
                with tempfile.NamedTemporaryFile(
                    prefix=".fablegear_restore_", suffix=suffix,
                    dir=str(target_dir), delete=False,
                ) as tmpf:
                    tmp_sidecar = Path(tmpf.name)
                try:
                    shutil.copy2(str(sp_sidecar), str(tmp_sidecar))
                    with open(tmp_sidecar, "rb") as verify_f:
                        os.fsync(verify_f.fileno())
                    tmp_sidecar.replace(live_sidecar)
                finally:
                    tmp_sidecar.unlink(missing_ok=True)
            else:
                live_sidecar.unlink(missing_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Restore failed: {exc}"}), 500

    log.info("Restored savepoint %s → %s", sp.name, DEVICE_DB)
    return jsonify({"ok": True, "restored": sp.name})


# ── Trash folders (pruned files) ─────────────────────────────────────────────

def _list_trash_folders(limit: int = 50):
    trash_dir = Path.home() / ".Trash"
    if not trash_dir.exists():
        return []
    results = []
    for entry in sorted(trash_dir.iterdir(), reverse=True):
        if not entry.is_dir() or not entry.name.startswith("FableGear_Pruned_"):
            continue
        ts_part = entry.name.replace("FableGear_Pruned_", "")
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d_%H%M%S")
            display = dt.strftime("%b %d, %Y  %I:%M %p")
        except ValueError:
            display = ts_part
        file_count = sum(1 for _ in entry.rglob("*") if _.is_file())
        results.append({
            "name": entry.name,
            "path": str(entry),
            "timestamp": ts_part,
            "display_time": display,
            "file_count": file_count,
        })
        if len(results) >= limit:
            break
    return results


@bp.route("/api/undo/trash")
def undo_trash():
    limit = request.args.get("limit", 50, type=int)
    folders = _list_trash_folders(limit)
    return jsonify(folders)


# routes_undo.py

@bp.route("/api/undo/trash/<folder_name>/files")
def undo_trash_files(folder_name: str):
    # Sanitize: ensure no path separators and enforce prefix
    folder_name = os.path.basename(folder_name)
    if not folder_name.startswith("FableGear_Pruned_"):
        return jsonify({"error": "Invalid folder"}), 400

    trash_dir = Path.home() / ".Trash" / folder_name
    if not trash_dir.is_dir():
        return jsonify({"error": "Folder not found"}), 404
    # ... rest of your logic
    files = []
    for f in sorted(trash_dir.rglob("*")):
        if not f.is_file():
            continue
        files.append({
            "name": f.name,
            "path": str(f),
            "relative": str(f.relative_to(trash_dir)),
            "size_bytes": f.stat().st_size,
        })
    return jsonify({"folder": folder_name, "files": files})


@bp.route("/api/undo/trash/restore", methods=["POST"])
def undo_trash_restore():
    body = request.get_json(silent=True) or {}
    folder_name = body.get("folder", "").strip()
    dest = body.get("destination", "").strip()

    # Strip any path components a request may have smuggled in — the folder is
    # a single name directly under ~/.Trash, never a traversable path. (The
    # sibling read endpoint undo_trash_files already does this; the restore
    # endpoint, which MOVES files, must too.)
    folder_name = os.path.basename(folder_name)
    if not folder_name.startswith("FableGear_Pruned_"):
        return jsonify({"error": "Invalid folder"}), 400
    trash_root = Path.home() / ".Trash"
    trash_dir = trash_root / folder_name
    if not _is_within(trash_dir, trash_root) or not trash_dir.is_dir():
        return jsonify({"error": "Folder not found"}), 404

    if not dest:
        try:
            from config import MUSIC_ROOT
            dest = str(MUSIC_ROOT)
        except Exception:
            return jsonify({"error": "No destination and MUSIC_ROOT not configured"}), 400

    dest_path = Path(dest)
    if not dest_path.is_dir():
        return jsonify({"error": f"Destination does not exist: {dest}"}), 400

    restored = 0
    errors = []
    for f in trash_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(trash_dir)
        target = dest_path / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(target))
            restored += 1
        except OSError as exc:
            errors.append(f"{rel}: {exc}")

    log.info("Restored %d files from %s → %s", restored, folder_name, dest)
    return jsonify({"ok": True, "restored": restored, "errors": errors})


# ── Tool operations undo (fg_processing_log) ─────────────────────────────────
# Every mutating tool journals per-file operations to the FableGear archive
# as they happen. These endpoints turn that journal into a safe, manual undo:
# list sessions → preview exactly what would move → revert.
#
# Revertible operation types and what "revert" means:
#   organize / rename — file was MOVED file_path ← metadata.from:
#                       move it back (only when the destination still exists
#                       and the original slot is free).
#   novelty_copy      — file was COPIED to file_path (source untouched):
#                       remove the copy into a recovery folder in the Archive.
#   relocate          — Rekordbox DB FolderPath was updated old→new:
#                       update it back new→old when the original file path
#                       still exists on disk (the typical "wrong root" fix).
# prune is deliberately absent — pruned files restore via the Trash tab.
# convert is listed read-only: the original was re-encoded and cannot be
# recreated by moving files around.

_REVERTIBLE = {"organize", "rename", "novelty_copy", "relocate"}
_SESSION_GAP_SEC = 15 * 60


def _fg_archive_db():
    from fablegear_database.database import FableGearDatabase
    return FableGearDatabase()


def _op_rows(conn, first_id: int, last_id: int, op_type: str) -> list:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, operation_type, file_path, completed_at, metadata
        FROM fg_processing_log
        WHERE id BETWEEN ? AND ? AND operation_type = ?
        ORDER BY id
        """,
        (first_id, last_id, op_type),
    )
    return cur.fetchall()


@bp.route("/api/undo/operations")
def undo_operations():
    """List recent tool-operation sessions from the archive journal.

    Consecutive rows of the same operation type with < 15 min between them
    form one session (one tool run, or a tight cluster of runs)."""
    limit = min(request.args.get("limit", 8000, type=int), 20000)
    try:
        db = _fg_archive_db()
        with db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, operation_type, file_path, completed_at, metadata
                FROM fg_processing_log
                WHERE operation_type IN ('organize', 'rename', 'novelty_copy', 'convert', 'relocate')
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            )
            rows = list(reversed(cur.fetchall()))
    except Exception as exc:
        return jsonify({"error": f"archive unavailable: {exc}"}), 500

    sessions: list[dict] = []
    for row_id, op_type, file_path, completed_at, _metadata in rows:
        try:
            when = datetime.fromisoformat(completed_at) if completed_at else None
        except ValueError:
            when = None
        cur_sess = sessions[-1] if sessions else None
        new_session = (
            cur_sess is None
            or cur_sess["type"] != op_type
            or (when and cur_sess.get("_last_dt")
                and (when - cur_sess["_last_dt"]).total_seconds() > _SESSION_GAP_SEC)
        )
        if new_session:
            sessions.append({
                "type": op_type,
                "first_id": row_id,
                "last_id": row_id,
                "started": completed_at,
                "ended": completed_at,
                "count": 1,
                "sample": [Path(file_path).name if file_path else "?"],
                "revertible": op_type in _REVERTIBLE,
                "_last_dt": when,
            })
        else:
            cur_sess["last_id"] = row_id
            cur_sess["ended"] = completed_at
            cur_sess["count"] += 1
            cur_sess["_last_dt"] = when or cur_sess["_last_dt"]
            if len(cur_sess["sample"]) < 3 and file_path:
                cur_sess["sample"].append(Path(file_path).name)

    for s in sessions:
        s.pop("_last_dt", None)
    sessions.reverse()  # newest first
    return jsonify({"sessions": sessions[:100]})


def _build_revert_plan(op_type: str, first_id: int, last_id: int) -> dict:
    """Compute, per journal row, whether and how it can be reverted."""
    import json as _json

    if op_type not in _REVERTIBLE:
        return {"error": f"'{op_type}' operations cannot be reverted by moving files"}

    db = _fg_archive_db()
    with db.connection() as conn:
        rows = _op_rows(conn, first_id, last_id, op_type)

    plan = {"op_type": op_type, "first_id": first_id, "last_id": last_id,
            "items": [], "revertible": 0, "blocked": 0}
    for row_id, _t, dest_str, _at, metadata in rows:
        try:
            meta = _json.loads(metadata) if metadata else {}
        except ValueError:
            meta = {}
        dest = Path(dest_str) if dest_str else None
        src = Path(meta["from"]) if meta.get("from") else None

        item = {"id": row_id, "current": dest_str, "returns_to": str(src) if src else None}
        if op_type == "novelty_copy":
            # Copy-undo: remove the copy (source was never touched).
            if dest and dest.exists():
                item["action"] = "remove_copy"
                item["ok"] = True
            else:
                item["action"] = "skip"
                item["ok"] = False
                item["reason"] = "copy no longer exists"
        elif op_type == "relocate":
            # Relocate-undo: update the Rekordbox DB FolderPath back from
            # current (new) to original (old).  Only meaningful when the
            # original file actually exists at the old path — that's the
            # common "wrong new_root" scenario the undo is designed for.
            if not dest or not src:
                item["action"] = "skip"
                item["ok"] = False
                item["reason"] = "journal row is missing a path"
            elif not src.exists():
                item["action"] = "skip"
                item["ok"] = False
                item["reason"] = "original path no longer exists on disk"
            else:
                item["action"] = "db_revert"
                item["ok"] = True
        else:
            if not dest or not src:
                item["action"] = "skip"
                item["ok"] = False
                item["reason"] = "journal row is missing a path"
            elif not dest.exists():
                item["action"] = "skip"
                item["ok"] = False
                item["reason"] = "file is no longer at the organized location"
            elif src.exists():
                item["action"] = "skip"
                item["ok"] = False
                item["reason"] = "original location is occupied"
            else:
                item["action"] = "move_back"
                item["ok"] = True
        plan["items"].append(item)
        plan["revertible" if item["ok"] else "blocked"] += 1
    return plan


@bp.route("/api/undo/operations/preview", methods=["POST"])
def undo_operations_preview():
    data = request.get_json(silent=True) or {}
    op_type = str(data.get("type", "")).strip()
    first_id = data.get("first_id")
    last_id = data.get("last_id")
    if not op_type or first_id is None or last_id is None:
        return jsonify({"error": "type, first_id and last_id are required"}), 400
    try:
        plan = _build_revert_plan(op_type, int(first_id), int(last_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if "error" in plan:
        return jsonify(plan), 400
    return jsonify(plan)


def _safe_absolute_path(path_str: str) -> Path | None:
    """Return a normalised absolute Path, or None if path_str is not absolute.

    Paths passed to filesystem operations come from the FableGear archive
    journal, which is written server-side. This helper normalises them and
    rejects any non-absolute value so relative or traversal sequences in
    journal data cannot affect paths outside the intended directories.
    """
    if not path_str:
        return None
    p = Path(os.path.normpath(path_str))
    return p if p.is_absolute() else None



@bp.route("/api/undo/operations/revert", methods=["POST"])
def undo_operations_revert():
    """Execute a revert plan. Recomputes the plan server-side so the state
    checked is the state acted on, and journals every reversal."""
    data = request.get_json(silent=True) or {}
    op_type = str(data.get("type", "")).strip()
    first_id = data.get("first_id")
    last_id = data.get("last_id")
    if not op_type or first_id is None or last_id is None:
        return jsonify({"error": "type, first_id and last_id are required"}), 400

    try:
        plan = _build_revert_plan(op_type, int(first_id), int(last_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if "error" in plan:
        return jsonify(plan), 400

    try:
        db = _fg_archive_db()
    except Exception as exc:
        return jsonify({"error": f"archive unavailable: {exc}"}), 500

    recovery_dir = None
    reverted = 0
    errors: list[str] = []

    # For relocate reverts we need a Rekordbox write session.  Open it once
    # for the whole batch so all DB updates land in one transaction.
    rb_db_ctx = None
    rb_db = None
    rb_folder_index: dict[str, Any] = {}  # FolderPath → content row; O(n) build, O(1) lookup
    if op_type == "relocate" and any(i.get("ok") for i in plan["items"]):
        from db_connection import rekordbox_is_running, write_db
        if rekordbox_is_running():
            return jsonify({"error": "Rekordbox is running — close it before reverting a relocate"}), 409
        try:
            rb_db_ctx = write_db()
            rb_db = rb_db_ctx.__enter__()
            rb_folder_index = {
                c.FolderPath: c
                for c in rb_db.get_content().all()
                if c.FolderPath
            }
        except Exception as exc:
            log.error("Could not open Rekordbox DB for relocate revert: %s", exc)
            if rb_db_ctx is not None:
                try:
                    rb_db_ctx.__exit__(None, None, None)
                except Exception as exc2:
                    log.warning("Cleanup of failed Rekordbox write session also failed: %s", exc2)
            return jsonify({"error": "Could not open Rekordbox DB — check server logs"}), 500

    try:
        for item in plan["items"]:
            if not item.get("ok"):
                continue
            # Paths come from the FableGear archive journal (written server-side,
            # never from raw HTTP input).  _safe_absolute_path rejects anything
            # that isn't an absolute, normalised path.
            current = _safe_absolute_path(item.get("current") or "")
            if current is None:
                errors.append("skipped: journal entry has a non-absolute path")
                continue
            # Keep the journal path as-is; resolving symlinks can change the string
            # and break exact-path lookups in both the Rekordbox DB and the Archive.
            try:
                if item["action"] == "move_back":
                    target = _safe_absolute_path(item.get("returns_to") or "")
                    if target is None:
                        errors.append(f"{current.name}: target path is not absolute — skipping")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current), str(target))
                    new_path = target
                elif item["action"] == "db_revert":
                    # Relocate undo: update the Rekordbox DB FolderPath back from
                    # current (new) to original (old).  The file stays on disk —
                    # only the DB pointer changes.
                    old_path = _safe_absolute_path(item.get("returns_to") or "")
                    if old_path is None:
                        errors.append(f"{current.name}: original path is not absolute — skipping")
                        continue
                    content_row = rb_folder_index.get(str(current))
                    if content_row is None:
                        errors.append(f"{current.name}: no DB row found at this path")
                        continue
                    rb_db.update_content_path(content_row, old_path, check_path=True)
                    new_path = old_path
                else:  # remove_copy — never destroy: park it in the Archive
                    if recovery_dir is None:
                        from config import ARCHIVE_ROOT
                        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                        recovery_dir = Path(str(ARCHIVE_ROOT)) / "Undone Copies" / stamp
                        recovery_dir.mkdir(parents=True, exist_ok=True)
                    new_path = recovery_dir / current.name
                    if new_path.exists():
                        new_path = recovery_dir / f"{current.stem}__{item['id']}{current.suffix}"
                    shutil.move(str(current), str(new_path))
                reverted += 1
                try:
                    # relocate reverts update the RB DB directly via
                    # update_content_path — no archive relink needed there.
                    if op_type != "relocate":
                        rec = db.get_content_by_path(str(current))
                        if rec and rec.id is not None:
                            db.relink_content(rec.id, str(new_path))
                    db.log_operation(
                        f"undo_{op_type}", str(new_path), status="ok",
                        metadata={"from": str(current), "journal_id": item["id"]},
                    )
                except Exception as exc:
                    log.warning("Archive update failed for undo of %s: %s", current, exc)
            except OSError as exc:
                log.warning("OSError during undo of %s: %s", current, exc)
                errors.append(f"{current.name}: filesystem error — check server logs")
            except Exception as exc:
                log.warning("Unexpected error during undo of %s: %s", current, exc)
                errors.append(f"{current.name}: unexpected error — check server logs")

        if rb_db is not None:
            try:
                rb_db.commit()
            except Exception as exc:
                log.error("Rekordbox commit failed during relocate revert: %s", exc)
                errors.append("Rekordbox commit failed — check server logs")
                rb_db.rollback()
    finally:
        if rb_db_ctx is not None:
            try:
                rb_db_ctx.__exit__(None, None, None)
            except Exception as exc:
                log.warning("Cleanup of Rekordbox write session after revert also failed: %s", exc)

    return jsonify({
        "ok": True,
        "reverted": reverted,
        "blocked": plan["blocked"],
        "errors": errors,
        "recovery_dir": str(recovery_dir) if recovery_dir else None,
    })


# ── Database Transactions Undo ───────────────────────────────────────────────

@bp.route("/api/undo/database/history")
def undo_database_history():
    try:
        from fablegear_database.undo import DatabaseUndoManager
        fg_db = _fg_archive_db()
        undo_mgr = DatabaseUndoManager(fg_db)
        recent = undo_mgr.history.get_recent_transactions(limit=50)
        # return reversed list so the most recent is first
        return jsonify([t.to_dict() for t in reversed(recent)])
    except Exception as exc:
        log.exception("Failed to fetch database transaction history")
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/undo/database/revert", methods=["POST"])
def undo_database_revert():
    body = request.get_json(silent=True) or {}
    transaction_id = body.get("transaction_id")
    if not transaction_id:
        return jsonify({"error": "Missing 'transaction_id'"}), 400

    try:
        from fablegear_database.undo import DatabaseUndoManager
        fg_db = _fg_archive_db()
        undo_mgr = DatabaseUndoManager(fg_db)
        transaction = undo_mgr.history.get_transaction(transaction_id)
        if transaction is not None and not transaction.affected_records:
            return jsonify({
                "error": (
                    f"'{transaction.operation_type}' transactions don't carry "
                    "per-record history and can't be reverted this way."
                ),
            }), 400
        success = undo_mgr.history.undo_transaction(transaction_id)
        if success:
            return jsonify({"ok": True})
        else:
            return jsonify({"error": "Revert failed or transaction not found"}), 400
    except Exception as exc:
        log.exception("Failed to revert database transaction")
        return jsonify({"error": str(exc)}), 500
