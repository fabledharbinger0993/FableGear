"""
routes_tools.py — ── The Butcher Shop ──

Flask Blueprint: all analysis, processing, and library-management tool routes
(process, pipeline, organize, convert, novelty, rename, duplicates, prune,
normalize preview, and scan cancel).
"""

import json
import os
import platform
import queue
import random as _random
import re as _re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

from helpers import (
    CLI_PATH,
    _sse_response,
    _sse_done,
    _sse_smart_skip_process,
    _stream_pipeline,
    _require_rb_closed,
    _get_library_root,
    _rb_is_running,
    _evict_old_jobs,
    _MAX_PRUNE_TOKENS,
    _MAX_PREVIEW_JOBS,
    mark_step_complete,
    list_running_managed_subprocesses,
    terminate_managed_subprocesses,
    api_error_from_exc,
)

bp = Blueprint("tools", __name__)


# ── External tool path resolution (Homebrew-safe) ─────────────────────────────

def _find_tool(name: str) -> str:
    """Resolve an external binary, falling back to Homebrew paths on macOS."""
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(candidate).is_file():
            return candidate
    return name  # last resort — surfaces a clear FileNotFoundError if absent


_FFMPEG  = _find_tool("ffmpeg")
_FFPROBE = _find_tool("ffprobe")


# ── Normalize preview state ───────────────────────────────────────────────────

_PREVIEW_TMP: Path = Path.home() / ".fablegear" / "previews"
_PREVIEW_TMP.mkdir(parents=True, exist_ok=True)

_PREVIEW_JOBS: dict[str, dict] = {}
_PREVIEW_LOCK: threading.Lock = threading.Lock()

_PREVIEW_AUDIO_EXTS = {
    ".aiff", ".aif", ".aifc", ".wav", ".flac", ".mp3",
    ".m4a", ".m4p", ".alac", ".ogg", ".opus",
}
_PREVIEW_MIN_DUR: int = 120     # track must be ≥ 2 min
_PREVIEW_MAX_SCAN: int = 40     # cap random sample for large folders
_PREVIEW_WINDOW: int = 20       # seconds of audio measured for LUFS


# ── Prune / duplicate report state ───────────────────────────────────────────

_prune_token_store: dict[str, dict] = {}
_report_cache: dict[str, dict] = {}

_PRUNE_TOKEN_TTL: int = 1800
_prune_token_lock: threading.Lock = threading.Lock()
_prune_state_lock: threading.Lock = threading.Lock()
_active_prune_workers: int = 0
_PRUNE_CANCEL_EVENT: threading.Event = threading.Event()

_PREVIEW_FILE_TTL_SEC: int = 86400
_PREVIEW_MAX_FILES: int = 400


def _default_duplicates_report_path() -> Path:
    try:
        from config import REPORTS_DIR  # noqa: PLC0415
        return REPORTS_DIR / "Duplicates" / "duplicate_report.csv"
    except Exception:
        return Path.home() / ".fablegear" / "Reports" / "Duplicates" / "duplicate_report.csv"


def _resolve_duplicates_report_path(csv_path_str: str) -> Path:
    if not csv_path_str:
        return _default_duplicates_report_path()
    return Path(csv_path_str).expanduser()


def _build_duplicate_groups(groups) -> list[dict]:
    return [
        {
            "group_id": g.group_id,
            "entries": [
                {
                    "action":         e.action,
                    "rank":           e.rank,
                    "file_path":      e.file_path,
                    "filename":       e.filename,
                    "file_size_mb":   round(e.file_size_mb, 2),
                    "bpm":            e.bpm,
                    "key":            e.key,
                    "format_ext":     e.format_ext,
                    "format_tier":    e.format_tier,
                    "exists_on_disk": e.exists_on_disk,
                    "in_db":          e.in_db,
                }
                for e in g.entries
            ],
        }
        for g in groups
    ]


def _load_duplicate_cache(csv_path: Path, *, include_db: bool = True) -> dict:
    cache_key = str(csv_path.resolve())
    csv_mtime = csv_path.stat().st_mtime

    cached = _report_cache.get(cache_key)
    if (
        cached is not None
        and cached.get("_mtime") == csv_mtime
        and (not include_db or cached.get("_db_enriched", False))
    ):
        return cached

    from pruner import load_report  # noqa: PLC0415

    db_warning = None
    if include_db:
        try:
            from db_connection import read_db  # noqa: PLC0415
            from config import DJMT_DB as _DB  # noqa: PLC0415
            with read_db(_DB) as db:
                groups = load_report(csv_path, db)
        except Exception as db_exc:
            groups = load_report(csv_path, None)
            db_warning = f"Rekordbox DB unavailable while loading duplicates: {db_exc}"
    else:
        groups = load_report(csv_path, None)

    all_groups = _build_duplicate_groups(groups)
    remove_entries = [
        entry
        for group in all_groups
        for entry in group["entries"]
        if entry["action"] == "REVIEW_REMOVE"
    ]

    cached = {
        "_mtime": csv_mtime,
        "_db_enriched": include_db,
        "groups": all_groups,
        "remove_paths": [entry["file_path"] for entry in remove_entries],
        "keep_paths": [
            entry["file_path"]
            for group in all_groups
            for entry in group["entries"]
            if entry["action"] == "KEEP"
        ],
        "total_remove_mb": round(sum(entry["file_size_mb"] for entry in remove_entries), 1),
        "db_warning": db_warning,
    }
    _report_cache[cache_key] = cached
    return cached


def _prune_workers_running() -> int:
    with _prune_state_lock:
        return _active_prune_workers


def _mark_prune_worker(delta: int) -> None:
    global _active_prune_workers
    with _prune_state_lock:
        _active_prune_workers = max(0, _active_prune_workers + delta)


def _cleanup_preview_tmp() -> None:
    try:
        clips = sorted(_PREVIEW_TMP.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return

    now = time.time()
    for clip in clips:
        try:
            if now - clip.stat().st_mtime > _PREVIEW_FILE_TTL_SEC:
                clip.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        clips = sorted(_PREVIEW_TMP.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in clips[_PREVIEW_MAX_FILES:]:
            stale.unlink(missing_ok=True)
    except Exception:
        pass


# ── Process (Tag Tracks) ──────────────────────────────────────────────────────

@bp.route("/api/run/process")
def api_process():
    paths = [p.strip() for p in request.args.getlist("path") if p.strip()]
    if not paths:
        return jsonify({"error": "path is required"}), 400

    no_bpm = request.args.get("no_bpm") == "1"
    no_key = request.args.get("no_key") == "1"
    no_normalize = request.args.get("no_normalize") == "1"
    force = request.args.get("force") == "1"
    force_bpm = request.args.get("force_bpm") == "1"
    force_key = request.args.get("force_key") == "1"
    enrich_tags = request.args.get("enrich_tags") == "1"
    smart_skip = request.args.get("smart_skip", "1") == "1"

    detect_bpm = not no_bpm
    detect_key = not no_key

    cmd = [sys.executable, str(CLI_PATH), "process", paths[0]]
    for extra in paths[1:]:
        cmd += ["--also-scan", extra]

    if no_bpm:
        cmd.append("--no-bpm")
    if no_key:
        cmd.append("--no-key")
    if no_normalize:
        cmd.append("--no-normalize")
    if force:
        cmd.append("--force")
    if force_bpm:
        cmd.append("--force-bpm")
    if force_key:
        cmd.append("--force-key")
    if enrich_tags:
        cmd.append("--enrich-tags")
    if request.args.get("dry_run") == "1":
        cmd.append("--dry-run")
    workers = request.args.get("workers", "").strip()
    if workers and workers.isdigit() and int(workers) > 1:
        cmd += ["--workers", workers]
    checkpoint_action = request.args.get("checkpoint_action", "").strip()
    if checkpoint_action in ("resume", "reset"):
        cmd += ["--checkpoint-action", checkpoint_action]

    if (
        smart_skip
        and not force and not force_bpm and not force_key
        and no_normalize
        and not enrich_tags
        and (detect_bpm or detect_key)
    ):
        roots = [Path(p) for p in paths]
        library_root = _get_library_root(request, "path")
        return _sse_smart_skip_process(
            base_cmd=cmd,
            roots=roots,
            detect_bpm=detect_bpm,
            detect_key=detect_key,
            library_root=library_root,
        )

    library_root = _get_library_root(request, "path")
    return _sse_response(cmd, library_root=library_root, step_name="process")


# ── Process retry (force re-tag a specific file list) ────────────────────────

@bp.route("/api/run/process-retry", methods=["POST"])
def api_process_retry():
    body = request.get_json(force=True, silent=True) or {}
    paths = [p.strip() for p in (body.get("paths") or []) if p.strip()]
    if not paths:
        return jsonify({"error": "paths list is required"}), 400

    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="fablegear_retry_",
        delete=False, encoding="utf-8",
    )
    tf.write("\n".join(paths))
    tf.close()

    placeholder_root = str(Path(paths[0]).parent)
    cmd = [
        sys.executable, str(CLI_PATH),
        "process", placeholder_root,
        "--no-normalize",
        "--force",
        "--paths-file", tf.name,
    ]
    if body.get("no_bpm"):
        cmd.append("--no-bpm")
    if body.get("no_key"):
        cmd.append("--no-key")

    library_root = str(Path(paths[0]).parent)
    return _sse_response(
        cmd,
        library_root=library_root,
        step_name="process",
        cleanup_paths=[Path(tf.name)],
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

@bp.route("/api/run/pipeline", methods=["POST"])
def api_pipeline():
    """
    Execute a user-defined sequence of steps.
    Body: {"dry_run": bool, "steps": [{"type": str, "config": {...}}, ...]}
    """
    body = request.get_json(force=True, silent=True) or {}
    dry_run = bool(body.get("dry_run", True))
    raw_steps = body.get("steps", [])

    if not raw_steps:
        return jsonify({"error": "steps list is required"}), 400

    _WRITE_STEP_TYPES = {"import", "link", "relocate", "prune", "rename"}
    if not dry_run and any(s.get("type") in _WRITE_STEP_TYPES for s in raw_steps):
        err = _require_rb_closed()
        if err:
            return err

    built: list[dict] = []

    for s in raw_steps:
        stype = s.get("type", "")
        cfg = s.get("config", {})
        name = s.get("name", stype)

        if stype == "organize":
            src_list = cfg.get("sources") or [cfg.get("source", "")]
            if isinstance(src_list, str):
                src_list = [src_list]
            cmd = [sys.executable, str(CLI_PATH), "organize",
                   src_list[0], cfg.get("target", "")]
            for extra in src_list[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if not dry_run:
                cmd.append("--no-dry-run")
            org_mode = cfg.get("mode", "assimilate")
            if org_mode == "integrate":
                cmd += ["--mode", "integrate"]
            if cfg.get("mix_threshold"):
                cmd += ["--mix-threshold", str(cfg["mix_threshold"])]
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]

        elif stype == "process":
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "process", paths[0]]
            for extra in paths[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if cfg.get("no_bpm"):       cmd.append("--no-bpm")
            if cfg.get("no_key"):       cmd.append("--no-key")
            if cfg.get("no_normalize"): cmd.append("--no-normalize")
            if cfg.get("force"):        cmd.append("--force")
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]
            if dry_run:                 cmd.append("--dry-run")

        elif stype == "normalize":
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "process", paths[0],
                   "--no-bpm", "--no-key"]
            for extra in paths[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]
            if dry_run:
                cmd.append("--dry-run")

        elif stype == "rename":
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "rename", paths[0]]
            for extra in paths[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if not dry_run:
                cmd.append("--no-dry-run")
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]

        elif stype == "duplicates":
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "duplicates"] + [p for p in paths if p]
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]
            if cfg.get("output"):
                cmd += ["--output", cfg["output"]]

        elif stype == "prune":
            cmd = [sys.executable, str(CLI_PATH), "prune"]
            if not dry_run:
                cmd.append("--no-dry-run")
            # CSV path: gated (one-step-per-request) mode sends it explicitly as
            # config.csv; AUTO mode omits it and _stream_pipeline appends the
            # report path captured from the preceding duplicates step.
            explicit_csv = str(cfg.get("csv", "")).strip()
            if explicit_csv:
                cmd.append(explicit_csv)
                built.append({"name": name, "cmd": cmd})
            else:
                built.append({"name": name, "cmd": cmd, "needs_csv": True})
            continue

        elif stype == "convert":
            if dry_run:
                # convert has no preview mode in the CLI — refusing beats
                # silently transcoding files during a "dry run".
                return jsonify({"error": "Step 'Convert Format' has no preview mode — "
                                         "remove it from this dry run, or run the pipeline live."}), 400
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "convert",
                   paths[0], cfg.get("format", "aiff")]
            for extra in paths[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]

        elif stype == "relocate":
            if dry_run:
                # relocate has no preview mode in the CLI — refusing beats
                # silently rewriting DB paths during a "dry run".
                return jsonify({"error": "Step 'Fix Broken Paths' (relocate) has no preview mode — "
                                         "remove it from this dry run, or run the pipeline live."}), 400
            cmd = [sys.executable, str(CLI_PATH), "relocate",
                   cfg.get("old_root", ""), cfg.get("new_root", "")]

        elif stype == "audit":
            cmd = [sys.executable, str(CLI_PATH), "audit"]
            _audit_paths = cfg.get("paths") or ([cfg["root"]] if cfg.get("root") else [])
            if isinstance(_audit_paths, str):
                _audit_paths = [_audit_paths]
            _audit_paths = [p for p in _audit_paths if p]
            if _audit_paths:
                cmd += ["--root", _audit_paths[0]]
                for _ap in _audit_paths[1:]:
                    cmd += ["--also-scan", _ap]

        elif stype == "import":
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "import", paths[0]]
            for extra in paths[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if dry_run:
                cmd.append("--dry-run")

        elif stype == "link":
            paths = cfg.get("paths") or [cfg.get("path", "")]
            if isinstance(paths, str):
                paths = [paths]
            cmd = [sys.executable, str(CLI_PATH), "link", paths[0]]
            for extra in paths[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            if dry_run:
                cmd.append("--dry-run")

        elif stype == "novelty":
            sources = cfg.get("sources") or [cfg.get("source", "")]
            if isinstance(sources, str):
                sources = [sources]
            cmd = [sys.executable, str(CLI_PATH), "novelty",
                   sources[0], cfg.get("dest", "")]
            for extra in sources[1:]:
                if extra:
                    cmd += ["--also-scan", extra]
            match_mode = str(cfg.get("match_mode", "fingerprint")).strip().lower()
            if match_mode in ("fingerprint", "filename"):
                cmd += ["--match-mode", match_mode]
            if not dry_run:
                cmd.append("--no-dry-run")
            if cfg.get("workers", 1) > 1:
                cmd += ["--workers", str(cfg["workers"])]

        else:
            return jsonify({"error": f"Unknown step type: {stype}"}), 400

        built.append({"name": name, "cmd": cmd})

    return Response(
        _stream_pipeline(built),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Organize ──────────────────────────────────────────────────────────────────

@bp.route("/api/run/organize")
def api_organize():
    sources = [s.strip() for s in request.args.getlist("source") if s.strip()]
    target = request.args.get("target", "").strip()
    if not sources or not target:
        return jsonify({"error": "at least one source and a target are required"}), 400

    cmd = [sys.executable, str(CLI_PATH), "organize", sources[0], target]
    for extra in sources[1:]:
        cmd += ["--also-scan", extra]

    if request.args.get("no_dry_run") == "1":
        cmd.append("--no-dry-run")

    org_mode = request.args.get("mode", "assimilate").strip()
    if org_mode == "integrate":
        cmd += ["--mode", "integrate"]

    workers = request.args.get("workers", "1").strip()
    if workers.isdigit() and int(workers) > 1:
        cmd += ["--workers", workers]

    threshold = request.args.get("mix_threshold", "").strip()
    if threshold:
        try:
            if float(threshold) > 0:
                cmd += ["--mix-threshold", threshold]
        except ValueError:
            pass

    checkpoint_action = request.args.get("checkpoint_action", "").strip()
    if checkpoint_action in ("resume", "reset"):
        cmd += ["--checkpoint-action", checkpoint_action]

    library_root = _get_library_root(request, "target")
    return _sse_response(cmd, library_root=library_root, step_name="organize")


# ── Convert ───────────────────────────────────────────────────────────────────

@bp.route("/api/run/convert")
def api_convert():
    paths = [p.strip() for p in request.args.getlist("path") if p.strip()]
    format_target = request.args.get("format", "").strip()
    if not paths or not format_target:
        return jsonify({"error": "path and format are required"}), 400

    cmd = [sys.executable, str(CLI_PATH), "convert", paths[0], format_target]
    for extra in paths[1:]:
        cmd += ["--also-scan", extra]
    workers = request.args.get("workers", "1").strip()
    if workers.isdigit() and int(workers) > 1:
        cmd += ["--workers", workers]
    checkpoint_action = request.args.get("checkpoint_action", "").strip()
    if checkpoint_action in ("resume", "reset"):
        cmd += ["--checkpoint-action", checkpoint_action]
    library_root = paths[0]
    return _sse_response(cmd, library_root=library_root, step_name="convert")


# ── Novelty ───────────────────────────────────────────────────────────────────

@bp.route("/api/run/novelty")
def api_novelty():
    sources = [s.strip() for s in request.args.getlist("source") if s.strip()]
    dest = request.args.get("dest", "").strip()
    if not sources or not dest:
        return jsonify({"error": "at least one source and a dest are required"}), 400

    cmd = [sys.executable, str(CLI_PATH), "novelty", sources[0], dest]
    for extra in sources[1:]:
        cmd += ["--also-scan", extra]

    copy_to = request.args.get("copy_to", "").strip()
    if copy_to:
        cmd += ["--copy-to", copy_to]

    if request.args.get("no_dry_run") == "1":
        cmd.append("--no-dry-run")

    match_mode = request.args.get("match_mode", "fingerprint").strip().lower()
    if match_mode not in ("fingerprint", "filename"):
        return jsonify({"error": "match_mode must be fingerprint or filename"}), 400
    cmd += ["--match-mode", match_mode]

    workers = request.args.get("workers", "1").strip()
    if workers.isdigit() and int(workers) > 1:
        cmd += ["--workers", workers]

    checkpoint_action = request.args.get("checkpoint_action", "").strip()
    if checkpoint_action in ("resume", "reset"):
        cmd += ["--checkpoint-action", checkpoint_action]

    library_root = _get_library_root(request, "dest")
    return _sse_response(cmd, library_root=library_root, step_name="novelty")


# ── Rename ────────────────────────────────────────────────────────────────────

@bp.route("/api/run/rename")
def api_rename():
    paths = [p.strip() for p in request.args.getlist("path") if p.strip()]
    if not paths:
        single = request.args.get("path", "").strip()
        if single:
            paths = [single]
    if not paths:
        return jsonify({"error": "at least one path is required"}), 400

    live_run = request.args.get("no_dry_run") == "1"
    if live_run:
        err = _require_rb_closed()
        if err:
            return err

    cmd = [sys.executable, str(CLI_PATH), "rename", paths[0]]
    for extra in paths[1:]:
        cmd += ["--also-scan", extra]

    if live_run:
        cmd.append("--no-dry-run")

    workers = request.args.get("workers", "1").strip()
    if workers.isdigit() and int(workers) > 1:
        cmd += ["--workers", workers]

    checkpoint_action = request.args.get("checkpoint_action", "").strip()
    if checkpoint_action in ("resume", "reset"):
        cmd += ["--checkpoint-action", checkpoint_action]

    library_root = paths[0]
    return _sse_response(cmd, library_root=library_root, step_name="rename")


@bp.route("/api/rename/probe")
def api_rename_probe():
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400

    root = Path(path)
    if not root.is_dir():
        return jsonify({"error": f"Not a directory: {path}"}), 400

    top_n = request.args.get("top_n", "5").strip()
    sample_size = request.args.get("sample_size", "100").strip()
    try:
        top_n_int = max(1, min(20, int(top_n)))
        sample_size_int = max(top_n_int, min(500, int(sample_size)))
    except ValueError:
        return jsonify({"error": "top_n and sample_size must be integers"}), 400

    from renamer import probe_ambiguous  # noqa: PLC0415

    candidates = probe_ambiguous(root, top_n=top_n_int, sample_size=sample_size_int)
    return jsonify({
        "path": str(root),
        "top_n": top_n_int,
        "sample_size": sample_size_int,
        "candidates": [candidate.to_dict() for candidate in candidates],
    })


@bp.route("/api/rename/preflight/apply", methods=["POST"])
def api_rename_preflight_apply():
    data = request.get_json(silent=True) or {}
    root_str = str(data.get("path", "")).strip()
    entries = data.get("entries") or []
    if not root_str:
        return jsonify({"error": "path is required"}), 400
    if not isinstance(entries, list):
        return jsonify({"error": "entries must be a list"}), 400

    root = Path(root_str)
    if not root.is_dir():
        return jsonify({"error": f"Not a directory: {root}"}), 400

    import renamer_learned as _learned  # noqa: PLC0415
    from renamer import quarantine_track  # noqa: PLC0415

    rules = _learned.load()
    results = []

    for item in entries:
        if not isinstance(item, dict):
            return jsonify({"error": "Each entry must be an object"}), 400
        action = str(item.get("action", "")).strip()
        source_path = str(item.get("source_path", "")).strip()
        if not action or not source_path:
            return jsonify({"error": "Each entry requires action and source_path"}), 400

        if action == "manual":
            target_name = str(item.get("target_name", "")).strip()
            if not target_name:
                return jsonify({"error": f"target_name is required for {source_path}"}), 400
            rules.add_manual_rename(source_path, target_name)
            _learned.harvest_from_confirmation(rules, target_name)
            results.append({"action": action, "source_path": source_path, "target_name": target_name})
        elif action == "producer_alias":
            token = str(item.get("token", "")).strip()
            canonical = str(item.get("canonical", "")).strip()
            if not token or not canonical:
                return jsonify({"error": f"token and canonical are required for {source_path}"}), 400
            rules.add_producer_alias(token, canonical)
            rules.add_known_producer(canonical)
            results.append({"action": action, "source_path": source_path, "token": token, "canonical": canonical})
        elif action == "quarantine":
            moved = quarantine_track(Path(source_path), root)
            rules.add_quarantine(source_path)
            results.append({"action": action, "source_path": source_path, "moved": moved})
        elif action == "skip":
            results.append({"action": action, "source_path": source_path})
        else:
            return jsonify({"error": f"Unsupported action: {action}"}), 400

    _learned.save(rules)
    return jsonify({
        "ok": True,
        "path": str(root),
        "results": results,
        "history_count": len(rules.history),
    })


# ── Checkpoint pre-flight check ───────────────────────────────────────────────

@bp.route("/api/checkpoint/check")
def api_checkpoint_check():
    """
    Pre-flight check for checkpoint/resume support.

    Query params:
      tool          — tool name: duplicates, process, convert, organize, novelty, rename
      path[]        — one or more root paths (repeatable)
      match_mode    — (duplicates only) matching strategy
      fuzzy_threshold — (duplicates only) similarity threshold

    Returns JSON:
      {exists: false}                             — no checkpoint found
      {exists: true, tool, saved_at, completed,   — checkpoint found
       total, roots, config, is_partial}
    """
    try:
        from checkpoint import Checkpoint  # noqa: PLC0415
    except ImportError:
        return jsonify({"exists": False, "error": "checkpoint module not available"}), 200

    tool = request.args.get("tool", "").strip()
    valid_tools = {"duplicates", "process", "convert", "organize", "novelty", "rename"}
    if tool not in valid_tools:
        return jsonify({"error": f"tool must be one of: {', '.join(sorted(valid_tools))}"}), 400

    paths = [p.strip() for p in request.args.getlist("path") if p.strip()]
    if not paths:
        return jsonify({"error": "at least one path is required"}), 400

    # Build config dict matching what the CLI subprocess would use
    config: dict = {}
    if tool == "duplicates":
        match_mode = request.args.get("match_mode", "exact").strip()
        if match_mode in ("exact", "fuzzy", "tags", "all"):
            config["match_mode"] = match_mode
        else:
            config["match_mode"] = "exact"
        fuzzy_threshold = request.args.get("fuzzy_threshold", "0.85").strip()
        try:
            ft = float(fuzzy_threshold)
            config["fuzzy_threshold"] = f"{ft:.2f}"
        except ValueError:
            config["fuzzy_threshold"] = "0.85"

    try:
        ck = Checkpoint(tool, paths, config)
        return jsonify(ck.info())
    except Exception as exc:
        return jsonify({"exists": False, "error": str(exc)}), 200


@bp.route("/api/checkpoint/reset", methods=["POST"])
def api_checkpoint_reset():
    """
    Discard every saved checkpoint for a tool — the server-side half of
    "Start Fresh". Clearing only the browser's localStorage banner (what the
    UI previously did on its own) left the actual ~/.fablegear/checkpoints
    file in place, so the next run silently resumed from stale state anyway.

    Query params:
      tool — tool name: duplicates, process, convert, organize, novelty, rename
    """
    try:
        from checkpoint import reset_all  # noqa: PLC0415
    except ImportError:
        return jsonify({"ok": False, "error": "checkpoint module not available"}), 200

    tool = request.args.get("tool", "").strip()
    valid_tools = {"duplicates", "process", "convert", "organize", "novelty", "rename"}
    if tool not in valid_tools:
        return jsonify({"error": f"tool must be one of: {', '.join(sorted(valid_tools))}"}), 400

    removed = reset_all(tool)
    return jsonify({"ok": True, "tool": tool, "removed": removed})


# ── Duplicates ────────────────────────────────────────────────────────────────

@bp.route("/api/run/duplicates")
def api_duplicates():
    paths = [p.strip() for p in request.args.getlist("path") if p.strip()]
    if not paths:
        return jsonify({"error": "at least one path is required"}), 400

    running_dupes = list_running_managed_subprocesses(tool="duplicates")
    if running_dupes:
        return _sse_done([
            "[ERROR] A duplicate scan is already running.",
            f"Active duplicate worker PID(s): {', '.join(str(p) for p in running_dupes)}",
            "Use Interrupt/Emergency Stop before starting another duplicate run.",
        ], exit_code=1)

    cmd = [sys.executable, str(CLI_PATH), "duplicates"] + paths
    # Tier select: quick = instant cached-hash match, deep = acoustic fpcalc (default).
    scan_mode = request.args.get("scan_mode", "").strip().lower()
    if scan_mode in ("quick", "deep"):
        cmd += ["--scan-mode", scan_mode]
    workers = request.args.get("workers", "").strip()
    if workers and workers.isdigit() and int(workers) > 1:
        cmd += ["--workers", workers]
    match_mode = request.args.get("match_mode", "").strip()
    if match_mode in ("exact", "fuzzy", "tags", "all"):
        cmd += ["--match-mode", match_mode]
    fuzzy_threshold = request.args.get("fuzzy_threshold", "").strip()
    if fuzzy_threshold:
        try:
            ft = float(fuzzy_threshold)
            if 0.0 < ft < 1.0:
                cmd += ["--fuzzy-threshold", f"{ft:.2f}"]
        except ValueError:
            pass
    # Resume/reset an interrupted scan (cli.py --checkpoint-action; preflight
    # via /api/checkpoint/check tells the UI whether a checkpoint exists).
    checkpoint_action = request.args.get("checkpoint_action", "").strip()
    if checkpoint_action in ("resume", "reset"):
        cmd += ["--checkpoint-action", checkpoint_action]
    library_root = paths[0] if paths else ""
    return _sse_response(cmd, library_root=library_root, step_name="duplicates")


# ── Prune stage / load / remove-paths ────────────────────────────────────────

@bp.route("/api/prune/stage", methods=["POST"])
def api_prune_stage():
    """
    Accept a JSON body {"paths": [...], "permanent": false, "csv_path": "..."} and
    return a single-use token consumed by GET /api/run/prune?token=<uuid>.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        paths = data.get("paths", [])
        if not isinstance(paths, list):
            return jsonify({"error": "paths must be a list"}), 400

        keeper_map: dict[str, str] = {}
        csv_path = _resolve_duplicates_report_path(str(data.get("csv_path", "")).strip())
        if csv_path.exists():
            cached = _load_duplicate_cache(csv_path, include_db=False)
            for group in cached["groups"]:
                keep_entry = next((entry for entry in group["entries"] if entry["action"] == "KEEP"), None)
                if keep_entry:
                    for entry in group["entries"]:
                        if entry["action"] == "REVIEW_REMOVE":
                            keeper_map[entry["file_path"]] = keep_entry["file_path"]

        token = str(uuid.uuid4())
        with _prune_token_lock:
            _evict_old_jobs(_prune_token_store, _MAX_PRUNE_TOKENS)
            _prune_token_store[token] = {
                "paths":      paths,
                "permanent":  bool(data.get("permanent", False)),
                "keeper_map": keeper_map,
                "_issued_at": time.time(),
            }
        return jsonify({"token": token, "keeper_map_size": len(keeper_map)})
    except Exception as exc:
        return api_error_from_exc(exc)


@bp.route("/api/duplicates/load")
def api_duplicates_load():
    """
    Load a duplicate_report.csv, enrich with live disk + DB data,
    and return a paginated slice of groups for the prune UI.
    """
    csv_path_str = request.args.get("csv_path", "").strip()
    try:
        page = max(0, int(request.args.get("page", 0)))
        per_page = max(1, int(request.args.get("per_page", 200)))
    except (ValueError, TypeError):
        return jsonify({"error": "page and per_page must be integers"}), 400

    csv_path = _resolve_duplicates_report_path(csv_path_str)

    if not csv_path.exists():
        return jsonify({"error": f"Report not found: {csv_path}"}), 404

    try:
        cached = _load_duplicate_cache(csv_path, include_db=True)
        all_groups = cached["groups"]
        total = len(all_groups)
        start = page * per_page
        page_groups = all_groups[start : start + per_page]

        return jsonify({
            "groups":          page_groups,
            "total_groups":    total,
            "total_remove":    len(cached["remove_paths"]),
            "total_remove_mb": cached.get("total_remove_mb", 0),
            "db_warning":      cached.get("db_warning"),
            "page":            page,
            "per_page":        per_page,
            "csv_path":        str(csv_path),
        })

    except Exception as exc:
        return api_error_from_exc(exc)


@bp.route("/api/duplicates/remove-paths")
def api_duplicates_remove_paths():
    """Return the full remove_paths and keep_paths lists for Select All operations."""
    csv_path_str = request.args.get("csv_path", "").strip()
    csv_path = _resolve_duplicates_report_path(csv_path_str)
    if not csv_path.exists():
        return jsonify({"error": "Report not loaded — call /api/duplicates/load first"}), 400

    try:
        cached = _load_duplicate_cache(csv_path, include_db=False)
    except Exception as exc:
        return api_error_from_exc(exc)

    return jsonify({
        "remove_paths": cached["remove_paths"],
        "keep_paths":   cached["keep_paths"],
    })


@bp.route("/api/open-file")
def api_open_file():
    """Open a file in the system's default application (native media player)."""
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": f"File not found: {path}"}), 404
    try:
        _sys = platform.system()
        if _sys == "Darwin":
            subprocess.Popen(
                ["open", str(p)],
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif _sys == "Windows":
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(
                ["xdg-open", str(p)],
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return jsonify({"ok": True})
    except Exception as exc:
        return api_error_from_exc(exc)


@bp.route("/api/run/prune")
def api_run_prune():
    """
    Execute a confirmed prune. Expects ?token=<uuid> issued by POST /api/prune/stage.
    Returns an SSE stream of progress lines.
    """
    token = request.args.get("token", "").strip()
    if not token:
        return _sse_done([
            "[ERROR] Missing prune token.",
            "Re-stage the prune list and try again.",
        ], exit_code=1)

    staged = None
    token_state = "ok"
    with _prune_token_lock:
        existing = _prune_token_store.get(token)
        if not existing:
            token_state = "missing"
        elif (time.time() - existing.get("_issued_at", 0)) > _PRUNE_TOKEN_TTL:
            _prune_token_store.pop(token, None)
            token_state = "expired"
        else:
            staged = _prune_token_store.pop(token, None)

    if token_state == "expired":
        return _sse_done([
            "[ERROR] Prune token expired.",
            "Re-stage the prune list and try again.",
        ], exit_code=1)

    if token_state == "missing" or not staged:
        return _sse_done([
            "[ERROR] Invalid or already-used prune token.",
            "Re-stage the prune list and try again.",
        ], exit_code=1)
    paths: list[str] = staged.get("paths", [])
    permanent: bool = staged.get("permanent", False)
    keeper_map: dict = staged.get("keeper_map", {})

    with _prune_state_lock:
        global _active_prune_workers
        if _active_prune_workers > 0:
            return _sse_done([
                "[ERROR] A prune operation is already running.",
                "Wait for the current prune to finish before starting another.",
            ], exit_code=1)
        _active_prune_workers += 1
        _PRUNE_CANCEL_EVENT.clear()

    log_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            if _PRUNE_CANCEL_EVENT.is_set():
                log_q.put(("line", "[ERROR] Prune cancelled before start."))
                log_q.put(("done", 130))
                return

            if _rb_is_running():
                log_q.put(("line", "[ERROR] Rekordbox is open — close it before pruning."))
                log_q.put(("done", 1))
                return

            if not paths:
                log_q.put(("line", "[ERROR] No files were passed to the prune endpoint."))
                log_q.put(("done", 1))
                return

            from pruner import prune_files  # noqa: PLC0415
            from db_connection import write_db  # noqa: PLC0415
            from config import DJMT_DB as _DJMT_DB, LOCAL_DB as _LOCAL_DB  # noqa: PLC0415

            # Use the device DB (Pioneer drive) when it's mounted — that's where
            # the actual library lives. Fall back to LOCAL_DB only if DJMT_DB is absent.
            _prune_db_path = _DJMT_DB if (_DJMT_DB and _DJMT_DB.exists()) else _LOCAL_DB

            from helpers import get_archive, get_archive_error  # noqa: PLC0415
            _fg_archive = get_archive()
            if _fg_archive is None:
                log_q.put(("line", f"[WARN] FableGear archive unavailable — this prune will not be recorded ({get_archive_error() or 'unknown'})"))

            summary = {}
            with write_db(_prune_db_path) as db:
                summary = prune_files(
                    paths,
                    db,
                    log=lambda m: log_q.put(("line", m)),
                    permanent=permanent,
                    keeper_map=keeper_map,
                    should_cancel=_PRUNE_CANCEL_EVENT.is_set,
                    archive=_fg_archive,
                )

            if summary.get("cancelled"):
                log_q.put(("line", "[ERROR] Prune cancelled before completion."))
                log_q.put(("done", 130))
                return

            # Treat as failure when nothing was moved/deleted but errors were recorded
            hard_errors = summary.get("errors", [])
            files_moved = summary.get("files_moved", 0)
            exit_code = 1 if (hard_errors and files_moved == 0) else 0

            _prune_root = staged.get("library_root", "")
            if not _prune_root:
                try:
                    from config import MUSIC_ROOT as _MR  # noqa: PLC0415
                    _prune_root = str(_MR)
                except Exception:
                    pass
            if _prune_root and exit_code == 0:
                mark_step_complete(_prune_root, "prune", 0)

            log_q.put(("done", exit_code))
        except Exception as exc:
            log_q.put(("line", f"[ERROR] {exc}"))
            log_q.put(("done", 1))
        finally:
            _mark_prune_worker(-1)

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as exc:
        _mark_prune_worker(-1)
        return _sse_done([
            f"[ERROR] Could not start prune worker: {exc}",
        ], exit_code=1)

    def _generate():
        while True:
            kind, val = log_q.get()
            if kind == "line":
                yield f"data: {json.dumps({'line': val})}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'done': True, 'exit_code': val})}\n\n"
                break

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Scan cancel ───────────────────────────────────────────────────────────────

@bp.route("/api/cancel", methods=["POST"])
def api_cancel():
    """Send SIGTERM to all active subprocesses (graceful interrupt / checkpoint)."""
    kill_result = terminate_managed_subprocesses(force=False, include_orphans=True)
    count = kill_result["tracked"] + kill_result["orphans"]

    prune_running = _prune_workers_running() > 0
    if prune_running:
        _PRUNE_CANCEL_EVENT.set()

    if count > 0 or prune_running:
        msg = "Interrupt signal sent — waiting for process to exit…"
        if prune_running and count == 0:
            msg = "Prune is running in-process and will stop at the next safe checkpoint."
        elif prune_running:
            msg = "Interrupt sent to subprocesses. Prune will stop at the next safe checkpoint."
        return jsonify({
            "ok": True,
            "terminated": count,
            "terminated_tracked": kill_result["tracked"],
            "terminated_orphans": kill_result["orphans"],
            "terminated_pids": kill_result["pids"],
            "prune_running": prune_running,
            "message": msg,
        })

    return jsonify({"ok": False, "error": "No active scan"}), 404


@bp.route("/api/cancel/force", methods=["POST"])
def api_cancel_force():
    """Send SIGKILL to all active subprocesses (emergency stop — server stays running)."""
    if _prune_workers_running() > 0:
        return jsonify({
            "ok": False,
            "error": "Prune is running in-process and cannot be force-killed safely. Wait for prune to finish.",
        }), 409

    kill_result = terminate_managed_subprocesses(force=True, include_orphans=True)
    count = kill_result["tracked"] + kill_result["orphans"]

    if count > 0:
        return jsonify({
            "ok": True,
            "killed": count,
            "killed_tracked": kill_result["tracked"],
            "killed_orphans": kill_result["orphans"],
            "killed_pids": kill_result["pids"],
        })
    return jsonify({"ok": False, "error": "No active scan"}), 404


# ── Normalize preview ─────────────────────────────────────────────────────────

def _preview_set(job_id: str, **kw) -> None:
    with _PREVIEW_LOCK:
        if job_id in _PREVIEW_JOBS:
            _PREVIEW_JOBS[job_id].update(kw)


def _preview_duration(path: Path) -> "float | None":
    try:
        r = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=8,
        )
        v = float(r.stdout.strip())
        return v if v > 0 else None
    except Exception:
        return None


def _preview_lufs(path: Path, start: float) -> "float | None":
    """Measure integrated LUFS over _PREVIEW_WINDOW seconds starting at start."""
    try:
        r = subprocess.run(
            [_FFMPEG, "-ss", str(max(0, start)), "-t", str(_PREVIEW_WINDOW),
             "-i", str(path), "-af", "loudnorm=print_format=json",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=40,
        )
        m = _re.search(r'"input_i"\s*:\s*"(-?\d+\.?\d*)"', r.stderr)
        if m:
            val = float(m.group(1))
            return val if val > -70 else None
    except Exception:
        pass
    return None


def _preview_extract(src: Path, start: float, dest: Path) -> bool:
    try:
        subprocess.run(
            [_FFMPEG, "-y", "-ss", str(max(0, start)), "-t", "10",
             "-i", str(src), "-acodec", "libmp3lame", "-q:a", "2",
             str(dest)],
            capture_output=True, timeout=30, check=True,
        )
        return True
    except Exception:
        return False


def _preview_normalize(src: Path, dest: Path) -> bool:
    try:
        subprocess.run(
            [_FFMPEG, "-y", "-i", str(src),
             "-af", "loudnorm=I=-8:TP=-1.5:LRA=11",
             "-acodec", "libmp3lame", "-q:a", "2", str(dest)],
            capture_output=True, timeout=30, check=True,
        )
        return True
    except Exception:
        return False


def _run_preview_job(job_id: str, folder: Path) -> None:
    try:
        _preview_set(job_id, status="scanning", msg="Listing audio files…", progress=0, total=0)

        all_audio = [
            f for f in sorted(folder.iterdir())
            if f.suffix.lower() in _PREVIEW_AUDIO_EXTS and not f.name.startswith(".")
        ]

        qualified: list[tuple[Path, float]] = []
        for f in all_audio:
            d = _preview_duration(f)
            if d and d >= _PREVIEW_MIN_DUR:
                qualified.append((f, d))

        if len(qualified) < 2:
            _preview_set(job_id, status="error",
                         msg=f"Need at least 2 tracks ≥ 2 min (found {len(qualified)}).")
            return

        sample = (
            qualified
            if len(qualified) <= _PREVIEW_MAX_SCAN
            else _random.sample(qualified, _PREVIEW_MAX_SCAN)
        )

        _preview_set(job_id, status="measuring",
                     msg=f"Measuring loudness of {len(sample)} tracks…",
                     total=len(sample))

        measured: list[tuple[Path, float, float]] = []
        for i, (f, dur) in enumerate(sample):
            start = max(0, dur / 2 - _PREVIEW_WINDOW / 2)
            lufs = _preview_lufs(f, start)
            if lufs is not None:
                measured.append((f, dur, lufs))
            _preview_set(job_id, progress=i + 1)

        if len(measured) < 2:
            _preview_set(job_id, status="error",
                         msg="Could not measure loudness for enough tracks.")
            return

        measured.sort(key=lambda x: x[2])
        quietest = measured[0]
        loudest = measured[-1]

        _preview_set(job_id, status="extracting", msg="Extracting preview clips…")

        clips = []
        for tag, (f, dur, lufs) in [("q", quietest), ("l", loudest)]:
            clip_start = max(0, dur / 2 - 5)

            orig_id = f"{job_id}_{tag}_orig"
            norm_id = f"{job_id}_{tag}_norm"
            orig_path = _PREVIEW_TMP / f"{orig_id}.mp3"
            norm_path = _PREVIEW_TMP / f"{norm_id}.mp3"

            ok_orig = _preview_extract(f, clip_start, orig_path)
            ok_norm = ok_orig and _preview_normalize(orig_path, norm_path)

            clips.append({
                "clip_id": orig_id if ok_orig else None,
                "track":   f.name,
                "lufs":    round(lufs, 1),
                "label":   "Original",
                "kind":    "quietest" if tag == "q" else "loudest",
            })
            clips.append({
                "clip_id": norm_id if ok_norm else None,
                "track":   f.name,
                "lufs":    -8.0,
                "label":   "Normalized  −8 LUFS",
                "kind":    "quietest" if tag == "q" else "loudest",
            })

        _preview_set(job_id, status="done", msg="", clips=clips)

    except Exception as exc:
        _preview_set(job_id, status="error", msg=str(exc))


@bp.route("/api/normalize/preview", methods=["POST"])
def api_normalize_preview():
    data = request.get_json(silent=True) or {}
    path = data.get("path") or request.form.get("path", "")
    folder = Path(path)
    if not path or not folder.is_dir():
        return jsonify({"error": "valid folder path required"}), 400

    _cleanup_preview_tmp()

    job_id = uuid.uuid4().hex[:8]
    with _PREVIEW_LOCK:
        _evict_old_jobs(_PREVIEW_JOBS, _MAX_PREVIEW_JOBS)
        _PREVIEW_JOBS[job_id] = {"status": "queued", "msg": "", "progress": 0,
                                 "total": 0, "clips": []}

    threading.Thread(target=_run_preview_job, args=(job_id, folder),
                     daemon=True, name=f"preview-{job_id}").start()
    return jsonify({"job_id": job_id})


@bp.route("/api/normalize/preview/<job_id>")
def api_normalize_preview_status(job_id):
    if not _re.match(r"^[0-9a-f]{8}$", job_id):
        return jsonify({"error": "invalid"}), 400
    with _PREVIEW_LOCK:
        job = _PREVIEW_JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@bp.route("/api/normalize/preview/clip/<clip_id>")
def api_normalize_preview_clip(clip_id):
    if not _re.match(r"^[0-9a-f]{8}_[ql]_(orig|norm)$", clip_id):
        return jsonify({"error": "invalid"}), 400
    clip_path = _PREVIEW_TMP / f"{clip_id}.mp3"
    if not clip_path.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(str(clip_path), mimetype="audio/mpeg", conditional=True)
