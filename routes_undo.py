"""
routes_undo.py — Undo Wizard API

Flask Blueprint providing read-only endpoints for browsing job history,
DB savepoints, and pruned-file trash folders so the UI can offer one-click
undo / restore actions, plus write endpoints that perform the actual restores.
"""

import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, request

bp = Blueprint("undo", __name__)
log = logging.getLogger(__name__)


def _config():
    from config import BACKUP_DIR, SAVEPOINTS_DIR  # noqa: PLC0415
    return BACKUP_DIR, SAVEPOINTS_DIR


# ── Timeline (job history) ───────────────────────────────────────────────────

@bp.route("/api/undo/timeline")
def undo_timeline():
    from job_dispatcher import get_history  # noqa: PLC0415

    limit = request.args.get("limit", 50, type=int)
    tool = request.args.get("tool") or None
    state = request.args.get("state") or None
    jobs = get_history(limit=limit, tool=tool, state=state)
    return jsonify(jobs)


@bp.route("/api/undo/job/<job_id>")
def undo_job_detail(job_id: str):
    from job_dispatcher import get_output  # noqa: PLC0415

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
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d_%H%M%S")
            display = dt.strftime("%b %d, %Y  %I:%M %p")
        except ValueError:
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
    from db_connection import _backup_db, rekordbox_is_running  # noqa: PLC0415

    body = request.get_json(silent=True) or {}
    savepoint_path = body.get("path", "").strip()
    if not savepoint_path:
        return jsonify({"error": "Missing 'path'"}), 400

    sp = Path(savepoint_path)
    if not sp.exists() or not sp.name.startswith("master.backup_"):
        return jsonify({"error": "Invalid savepoint"}), 400

    if rekordbox_is_running():
        return jsonify({"error": "Rekordbox is running — close it first"}), 409

    try:
        from config import DJMT_DB  # noqa: PLC0415
    except Exception:
        return jsonify({"error": "FableGear not configured"}), 500

    try:
        _backup_db(DJMT_DB)
    except Exception as exc:
        return jsonify({"error": f"Could not backup current DB: {exc}"}), 500

    try:
        shutil.copy2(str(sp), str(DJMT_DB))
    except OSError as exc:
        return jsonify({"error": f"Restore failed: {exc}"}), 500

    log.info("Restored savepoint %s → %s", sp.name, DJMT_DB)
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


@bp.route("/api/undo/trash/<folder_name>/files")
def undo_trash_files(folder_name: str):
    if not folder_name.startswith("FableGear_Pruned_"):
        return jsonify({"error": "Invalid folder"}), 400
    trash_dir = Path.home() / ".Trash" / folder_name
    if not trash_dir.is_dir():
        return jsonify({"error": "Folder not found"}), 404
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

    if not folder_name.startswith("FableGear_Pruned_"):
        return jsonify({"error": "Invalid folder"}), 400
    trash_dir = Path.home() / ".Trash" / folder_name
    if not trash_dir.is_dir():
        return jsonify({"error": "Folder not found"}), 404

    if not dest:
        try:
            from config import MUSIC_ROOT  # noqa: PLC0415
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
