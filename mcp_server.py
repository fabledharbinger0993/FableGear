#!/usr/bin/env python3
"""
fablegear / mcp_server.py

FableGear MCP Server — exposes FableGear's library management tools as
callable MCP tools so any compatible AI agent can manage a DJ library
without opening the FableGear app.

Supported clients (anything that speaks the MCP open standard):
  Claude Desktop · Cursor · Windsurf · VS Code Copilot · Continue.dev

Transport:
  stdio (default) — used by Claude Desktop, Cursor, most local clients.
  sse             — HTTP-based; used by web-connected or multi-user setups.

Usage:
  python3 mcp_server.py                          # stdio
  python3 mcp_server.py --transport sse          # SSE on port 8765
  python3 mcp_server.py --transport sse --port 9000

Claude Desktop config snippet (~/.config/claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "fablegear": {
        "command": "/path/to/venv/bin/python3",
        "args": ["/path/to/FableGear/mcp_server.py"]
      }
    }
  }

Safety contract:
  - Read tools (audit, find_duplicates, scan_novelty, get_status) run any time.
  - Write tools (tag_tracks, import_to_rekordbox, link_playlists, relocate_tracks,
    organize_library, rename_files) check that Rekordbox is closed first and
    return a clear error if it is running — they never bypass that gate.
  - All write operations create a timestamped DB backup before touching the
    database (enforced by db_connection.write_db()).
"""

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

import job_dispatcher

from user_config import (
    DEFAULTS as USER_CONFIG_DEFAULTS,
    generate_mcp_token,
    get_drive_status as probe_drive_status,
    load_user_config,
    save_user_config,
)

# Hosts that never require a bearer token — only reachable from this machine.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# ── MCP SDK ───────────────────────────────────────────────────────────────────

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:
    print(
        "The 'mcp' package is required. Install with:\n"
        "  pip install mcp\n"
        "or add it to requirements.txt and reinstall.",
        file=sys.stderr,
    )
    sys.exit(1)

# ── FableGear imports (lazy — server still starts if config is absent) ─────────

_CONFIG_ERROR: Optional[str] = None

try:
    from db_connection import rekordbox_is_running
    from config import LOCAL_DB, MUSIC_ROOT, ARCHIVE_ROOT
except RuntimeError as _e:
    _CONFIG_ERROR = str(_e)
    LOCAL_DB = None
    MUSIC_ROOT = None
    ARCHIVE_ROOT = None

    def rekordbox_is_running() -> bool:  # type: ignore[misc]
        return False

# ── Internal helpers ──────────────────────────────────────────────────────────

REPO_DIR = Path(__file__).parent

# ── Job dispatcher init ────────────────────────────────────────────────────────
_checkpoints_dir: Optional[Path] = None
try:
    from config import ARCHIVE_ROOT as _ARCHIVE_ROOT

    _checkpoints_dir = Path(str(_ARCHIVE_ROOT)) / "Checkpoints"
except Exception:
    _checkpoints_dir = None

job_dispatcher.init(REPO_DIR, _checkpoints_dir)


def _cfg_gate() -> Optional[str]:
    """Return an error string if FableGear is not yet configured."""
    try:
        load_user_config()
    except Exception as exc:
        return (
            f"FableGear is not configured: {exc}\n"
            "Run `python3 cli.py setup` in the FableGear directory first, "
            "then retry this tool."
        )
    return None


def _get_config() -> Optional[Dict]:
    """Return the live user config if available, otherwise None."""
    try:
        return load_user_config()
    except Exception:
        return None


def _effective_music_root() -> str:
    """Read music_root from live config with fallback to startup import values."""
    cfg = _get_config()
    if cfg and cfg.get("music_root"):
        return str(cfg["music_root"])
    return str(MUSIC_ROOT) if MUSIC_ROOT else ""


def _rb_gate() -> Optional[str]:
    """Return an error string if Rekordbox is currently running."""
    try:
        if rekordbox_is_running():
            return (
                "Rekordbox is currently open. This tool writes to the Rekordbox "
                "database, so Rekordbox must be closed first to avoid conflicts. "
                "Close Rekordbox and retry."
            )
    except Exception:
        pass
    return None


# Dependency map enforced by `_dep_gate`. Mirrors the advisory order in the
# server instructions string. Each value is the prerequisite tool that must
# have a completed checkpoint for the given scope before the keyed tool runs.
# Callers can bypass with force=True (e.g. when checkpoints exist out-of-band
# or the operator has explicit reason to skip the precondition).
_DEPENDENCY_MAP: Dict[str, str] = {
    "find_duplicates":     "audit_library",
    "tag_tracks":          "audit_library",
    "rename_files":        "tag_tracks",
    "organize_library":    "rename_files",
    "import_to_rekordbox": "organize_library",
    "link_playlists":      "import_to_rekordbox",
}


def _dep_gate(tool: str, scope: str, force: bool = False) -> Optional[str]:
    """Return an error string if the prerequisite checkpoint is missing.

    Returns None when:
      - tool has no prerequisite, OR
      - force=True (caller explicitly overrides), OR
      - a completed checkpoint for the prerequisite exists for this scope.
    """
    if force:
        return None
    prereq = _DEPENDENCY_MAP.get(tool)
    if not prereq:
        return None
    cp = job_dispatcher.find_checkpoint(prereq, scope.strip())
    if cp is not None:
        return None
    return (
        f"Dependency gate: '{tool}' requires a completed '{prereq}' checkpoint "
        f"for scope='{scope or '<global>'}'. Run '{prereq}' first, or pass "
        f"force=True to bypass this check."
    )


# ── MCP server definition ──────────────────────────────────────────────────────

mcp = FastMCP(
    name="FableGear",
    instructions=(
        "FableGear is a headless DJ library management toolkit for Rekordbox. "
        "Use these tools to audit, tag, import, organize, deduplicate, and "
        "maintain a local music library — all without opening the FableGear app. "
        "\n\n"
        "SAFETY RULES:\n"
        "- Write tools require Rekordbox to be closed. They will tell you if it is open.\n"
        "- organize_library and rename_files default to dry_run=True. "
        "  Always preview before committing.\n"
        "- import_to_rekordbox and link_playlists default to live mode (no dry run). "
        "  Pass dry_run=True to preview first.\n"
        "- All writes create a timestamped backup of the Rekordbox database automatically."
        "\n\n"
        "DEPENDENCY GATES (check_checkpoint before dispatching dependent tools):\n"
        "- audit_library     → no prerequisites\n"
        "- scan_novelty      → no prerequisites\n"
        "- find_duplicates   → check_checkpoint(\"audit_library\", scope) first\n"
        "- tag_tracks        → check_checkpoint(\"audit_library\", scope) first\n"
        "- rename_files      → check_checkpoint(\"tag_tracks\", scope) first\n"
        "- organize_library  → check_checkpoint(\"rename_files\", scope) first\n"
        "- import_to_rekordbox → check_checkpoint(\"organize_library\", scope) first\n"
        "- link_playlists    → check_checkpoint(\"import_to_rekordbox\", scope) first\n"
        "\n"
        "Parallel-safe (no shared write resource conflict):\n"
        "  audit_library + scan_novelty\n"
        "  find_duplicates + tag_tracks (after audit checkpoint)"
    ),
)


# ─── Read-only tools ──────────────────────────────────────────────────────────

@mcp.tool()
def get_library_status() -> str:
    """
    Return the current FableGear configuration and Rekordbox status.
    Shows database paths, music root, archive location, and whether
    Rekordbox is currently running. No files are read or written.
    """
    cfg = _get_config()
    if not cfg:
        return (
            f"FableGear is not configured: {_CONFIG_ERROR or 'missing or invalid config.json'}\n"
            "Run `python3 cli.py setup` first."
        )

    rb_running = False
    try:
        rb_running = rekordbox_is_running()
    except Exception:
        pass

    archive_mode = str(cfg.get("archive_mode", "auto"))
    custom_archive = str(cfg.get("custom_archive_dir", "")).strip()
    if archive_mode == "custom" and custom_archive:
        archive_root = custom_archive
    else:
        archive_root = str(Path(str(cfg["music_root"])).parent / "FableGear Archive")

    lines = [
        "FableGear Library Status",
        "─" * 44,
        f"Rekordbox running : {'YES — close before any write tool' if rb_running else 'No'}",
        f"Local DB          : {cfg.get('local_db')}",
        f"Device DB         : {cfg.get('device_db')}",
        f"Music root        : {cfg.get('music_root')}",
        f"Archive root      : {archive_root}",
        f"Mode              : {cfg.get('mode', 'suburban')}",
    ]
    return "\n".join(lines)


@mcp.tool(name="get_drive_status")
def get_drive_status_tool() -> str:
    """
    Return current mount/path health for configured library paths.

    Safe read-only helper for onboarding and troubleshooting. This tool never
    writes files and is safe to call even before configuration exists.
    """
    return json.dumps(probe_drive_status(), indent=2)


@mcp.tool()
def configure_paths(
    local_db: str,
    device_db: str,
    music_root: str,
    backup_dir: str,
    target_lufs: Optional[float] = None,
    mode: Optional[str] = None,
) -> str:
    """
    Create or update ~/.fablegear/config.json from MCP.

    This writes only the FableGear config file (atomic save), not the Rekordbox
    database. It enables AI-assisted onboarding without shell setup.

    Required:
      local_db, device_db, music_root, backup_dir

    Optional:
      target_lufs (float), mode ("rural" or "suburban")
    """
    cleaned = {
        "local_db": local_db.strip(),
        "device_db": device_db.strip(),
        "music_root": music_root.strip(),
        "backup_dir": backup_dir.strip(),
    }
    missing = [k for k, v in cleaned.items() if not v]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"

    cfg = _get_config() or {}
    for key, default in USER_CONFIG_DEFAULTS.items():
        cfg.setdefault(key, default)
    cfg.update(cleaned)

    if target_lufs is not None:
        try:
            cfg["target_lufs"] = float(target_lufs)
        except (TypeError, ValueError):
            return "target_lufs must be a number."

    if mode is not None:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in ("rural", "suburban"):
            return "mode must be either 'rural' or 'suburban'."
        cfg["mode"] = normalized_mode

    try:
        save_user_config(cfg)
    except Exception as exc:
        return f"Failed to save config: {exc}"

    try:
        import config as _config

        _config = importlib.reload(_config)
        job_dispatcher.reconfigure(Path(str(_config.ARCHIVE_ROOT)) / "Checkpoints")
    except Exception:
        pass

    return (
        "Configuration saved successfully.\n\n"
        f"local_db: {cfg.get('local_db')}\n"
        f"device_db: {cfg.get('device_db')}\n"
        f"music_root: {cfg.get('music_root')}\n"
        f"backup_dir: {cfg.get('backup_dir')}\n"
        f"target_lufs: {cfg.get('target_lufs')}\n"
        f"mode: {cfg.get('mode')}"
    )


@mcp.tool()
def audit_library(
    music_root: str = "",
    extra_roots: str = "",
) -> str:
    """
    Run a full read-only health audit of the Rekordbox library.
    Checks for missing files, dead path roots, orphaned tracks, and
    database consistency. Returns a human-readable summary report.
    A report file is also saved to the FableGear Archive automatically.

    Safe to run at any time — Rekordbox can be open or closed.

    Args:
        music_root:  Path to the music library folder to scan.
                     Uses the configured default if left blank.
        extra_roots: Comma-separated list of additional folders to include
                     in the filesystem scan alongside music_root.
    """
    if err := _cfg_gate():
        return err

    cli_args: list = ["audit"]
    root = music_root.strip() or _effective_music_root()
    if root:
        cli_args += ["--root", root]
    for extra in [e.strip() for e in extra_roots.split(",") if e.strip()]:
        cli_args += ["--also-scan", extra]

    return job_dispatcher.dispatch(
        tool="audit_library",
        cli_args=cli_args,
        scope=root,
    )


@mcp.tool()
def find_duplicates(
    music_root: str = "",
    match_mode: str = "exact",
    workers: int = 1,
    force: bool = False,
    quick_mode: bool = False,
) -> str:
    """
    Scan the library for acoustically identical files using Chromaprint
    fingerprinting. Returns a summary of duplicate groups and the path
    to a detailed CSV report for manual review.

    Requires the 'fpcalc' binary (install via: brew install chromaprint).
    Safe to run with Rekordbox open or closed.
    Gate: requires an audit_library checkpoint for this scope unless force=True.

    Args:
        music_root:  Folder to scan. Uses configured default if blank.
        match_mode:  "exact" (strict fingerprint match, default) or
                     "fuzzy" (catches near-identical files with minor edits).
        workers:     Parallel fingerprinting workers. Default 1.
                     Increase to 2–4 on fast SSDs; keep at 1 on external drives.
        force:       Bypass the audit_library dependency gate. Default False.
        quick_mode:  When True, run an INSTANT byte-identical scan from the
                     cached database hashes (no fpcalc). Much faster; finds exact
                     copies only. Default False (acoustic fingerprint scan).
    """
    if err := _cfg_gate():
        return err

    root = music_root.strip() or _effective_music_root()
    if not root:
        return "No music root configured. Pass music_root or run `python3 cli.py setup`."

    if err := _dep_gate("find_duplicates", root, force=force):
        return err

    cli_args = [
        "duplicates",
        root,
        "--match-mode",
        match_mode,
        "--workers",
        str(workers),
    ]
    if quick_mode:
        cli_args += ["--scan-mode", "quick"]
    return job_dispatcher.dispatch(
        tool="find_duplicates",
        cli_args=cli_args,
        scope=root,
    )


@mcp.tool()
def scan_novelty(
    source_path: str,
    dest_path: str,
    dry_run: bool = True,
    workers: int = 1,
) -> str:
    """
    Find tracks in source_path that do not exist in dest_path (novel tracks).
    Useful for discovering newly downloaded music not yet in the main library.
    Defaults to dry_run=True — set dry_run=False to copy novel tracks for real.

    Safe to run with Rekordbox open or closed.

    Args:
        source_path: Folder to scan for novel tracks (e.g. a download folder).
        dest_path:   Your main library root to compare against.
        dry_run:     Preview which tracks would be copied, without copying. Default True.
        workers:     Parallel workers for scanning. Default 1.
    """
    if err := _cfg_gate():
        return err

    if not source_path.strip():
        return "source_path is required."
    if not dest_path.strip():
        return "dest_path is required (the main library root to compare against)."

    cli_args = ["novelty", source_path.strip(), dest_path.strip(), "--workers", str(workers)]
    if not dry_run:
        cli_args.append("--no-dry-run")

    return job_dispatcher.dispatch(
        tool="scan_novelty",
        cli_args=cli_args,
        scope=source_path.strip(),
    )


# ─── Write tools (Rekordbox must be closed) ────────────────────────────────────

@mcp.tool()
def tag_tracks(
    music_root: str = "",
    detect_bpm: bool = True,
    detect_key: bool = True,
    normalize_loudness: bool = True,
    workers: int = 1,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    """
    Analyze audio files and write BPM, musical key (Camelot notation),
    and loudness (LUFS normalization) tags to all audio files under
    the given path. Uses librosa for analysis and mutagen for tag writes.

    REQUIRES Rekordbox to be closed (unless dry_run=True).
    Gate: requires an audit_library checkpoint for this scope unless force=True.

    Args:
        music_root:         Folder to process. Uses configured default if blank.
        detect_bpm:         Detect and write BPM values. Default True.
        detect_key:         Detect and write key in Camelot notation. Default True.
        normalize_loudness: Normalize loudness to the configured LUFS target. Default True.
        workers:            Parallel workers. Default 1 (safe). Increase on fast SSDs.
        dry_run:            Preview mode — loudness normalisation suppressed.
                            BPM/key tag writes still occur unless detect_bpm /
                            detect_key are False. Default False.
        force:              Bypass the audit_library dependency gate. Default False.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    root = music_root.strip() or _effective_music_root()
    if not root:
        return "No music root configured. Pass music_root or run `python3 cli.py setup`."

    if err := _dep_gate("tag_tracks", root, force=force):
        return err

    cli_args = ["process", root, "--workers", str(workers)]
    if not detect_bpm:
        cli_args.append("--no-bpm")
    if not detect_key:
        cli_args.append("--no-key")
    if not normalize_loudness:
        cli_args.append("--no-normalize")
    if dry_run:
        cli_args.append("--dry-run")

    return job_dispatcher.dispatch(
        tool="tag_tracks",
        cli_args=cli_args,
        scope=root,
        timeout=14400,
    )


@mcp.tool()
def import_to_rekordbox(
    source_path: str,
    dry_run: bool = False,
    resume: bool = True,
    force: bool = False,
) -> str:
    """
    Import audio files from a folder into the Rekordbox database.
    Each audio file found under source_path is registered as a new track.

    REQUIRES Rekordbox to be closed (unless dry_run=True).
    Gate: requires an organize_library checkpoint for this scope unless force=True.

    Args:
        source_path: Path to the folder containing audio files to import.
        dry_run:     Preview what would be imported without writing. Default False.
        resume:      Skip tracks already present in the database. Default True.
        force:       Bypass the organize_library dependency gate. Default False.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    if not source_path.strip():
        return "source_path is required."

    if err := _dep_gate("import_to_rekordbox", source_path, force=force):
        return err

    cli_args = ["import", source_path.strip()]
    if dry_run:
        cli_args.append("--dry-run")
    if resume:
        cli_args.append("--resume")

    return job_dispatcher.dispatch(
        tool="import_to_rekordbox",
        cli_args=cli_args,
        scope=source_path.strip(),
    )


@mcp.tool()
def link_playlists(
    source_path: str,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    """
    Match imported tracks to existing Rekordbox playlists based on the
    folder structure of the source path. Run this after import_to_rekordbox
    to wire tracks into the correct playlists automatically.

    REQUIRES Rekordbox to be closed (unless dry_run=True).
    Gate: requires an import_to_rekordbox checkpoint for this scope unless force=True.

    Args:
        source_path: The same folder used for import.
        dry_run:     Preview playlist matches without writing. Default False.
        force:       Bypass the import_to_rekordbox dependency gate. Default False.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    if not source_path.strip():
        return "source_path is required."

    if err := _dep_gate("link_playlists", source_path, force=force):
        return err

    cli_args = ["link", source_path.strip()]
    if dry_run:
        cli_args.append("--dry-run")

    return job_dispatcher.dispatch(
        tool="link_playlists",
        cli_args=cli_args,
        scope=source_path.strip(),
    )


@mcp.tool()
def relocate_tracks(
    old_root: str,
    new_root: str,
) -> str:
    """
    Update Rekordbox database paths after physically moving files to a
    new location. Use this when you've moved or renamed a folder and
    Rekordbox shows all those tracks as missing (red exclamation marks).

    FableGear matches by exact path, file hash, and fuzzy filename — in
    that order — so most tracks are found even with partial renames.

    REQUIRES Rekordbox to be closed.

    Args:
        old_root: The old folder path prefix as it currently appears in
              the Rekordbox database (e.g. /old/library/root).
        new_root: The new folder path where the files now live
              (e.g. /new/library/root).
    """
    if err := _cfg_gate():
        return err
    if err := _rb_gate():
        return err

    if not old_root.strip() or not new_root.strip():
        return "Both old_root and new_root are required."

    cli_args = ["relocate", old_root.strip(), new_root.strip()]
    return job_dispatcher.dispatch(
        tool="relocate_tracks",
        cli_args=cli_args,
        scope=new_root.strip(),
    )


@mcp.tool()
def organize_library(
    source_path: str,
    target_path: str,
    dry_run: bool = True,
    mode: str = "assimilate",
    mix_threshold_minutes: float = 15.0,
    workers: int = 1,
    force: bool = False,
) -> str:
    """
    Consolidate audio files into a clean Artist / Album / Track folder
    hierarchy. Mixes and long sets (above mix_threshold_minutes) are routed
    to a 'Live Sets & Mixes' folder automatically.

    Defaults to dry_run=True — always preview first.
    REQUIRES Rekordbox to be closed when dry_run=False.
    Gate: requires a rename_files checkpoint for the source scope unless force=True.

    Args:
        source_path:            Folder to scan for audio files.
        target_path:            Root of the organized library destination.
        dry_run:                Preview moves without executing. Default True (safe).
        mode:                   "assimilate" (move files, prune empty dirs from source)
                                or "integrate" (copy only — source is never modified).
        mix_threshold_minutes:  Tracks at or above this length go to Live Sets & Mixes.
                                Default 15.0 minutes.
        workers:                Parallel I/O workers for the move phase. Default 1.
        force:                  Bypass the rename_files dependency gate. Default False.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    if not source_path.strip():
        return "source_path is required."
    if not target_path.strip():
        return "target_path is required."

    if err := _dep_gate("organize_library", source_path, force=force):
        return err

    cli_args = [
        "organize",
        source_path.strip(),
        target_path.strip(),
        "--mode", mode,
        "--mix-threshold", str(mix_threshold_minutes),
        "--workers", str(workers),
    ]
    if not dry_run:
        cli_args.append("--no-dry-run")

    return job_dispatcher.dispatch(
        tool="organize_library",
        cli_args=cli_args,
        scope=target_path.strip(),
        timeout=7200,
    )


@mcp.tool()
def rename_files(
    source_path: str = "",
    dry_run: bool = True,
    workers: int = 1,
    force: bool = False,
) -> str:
    """
    Rename audio files to clean titles derived from their embedded metadata
    (ID3 tags / Vorbis comments). Uses learned rules from previous sessions
    to auto-approve known artist and producer name patterns.

    Defaults to dry_run=True — always preview the proposed renames first.
    REQUIRES Rekordbox to be closed when dry_run=False.
    Gate: requires a tag_tracks checkpoint for this scope unless force=True.

    Args:
        source_path: Folder containing files to rename.
                     Uses configured music root if blank.
        dry_run:     Preview proposed renames without executing. Default True (safe).
        workers:     Parallel workers. Default 1.
        force:       Bypass the tag_tracks dependency gate. Default False.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    root = source_path.strip() or _effective_music_root()
    if not root:
        return "No source path configured. Pass source_path or run `python3 cli.py setup`."

    if err := _dep_gate("rename_files", root, force=force):
        return err

    cli_args = ["rename", root, "--workers", str(workers)]
    if not dry_run:
        cli_args.append("--no-dry-run")

    return job_dispatcher.dispatch(
        tool="rename_files",
        cli_args=cli_args,
        scope=root,
    )


@mcp.tool()
def get_job_status(job_id: str) -> str:
    """
    Poll the status of a previously dispatched background job.

    Returns full job details including state (pending/running/done/error),
    timing, and the complete CLI output once the job completes.

    Looks up in-memory state first (jobs dispatched in this process), then
    falls back to persisted SQLite history for jobs that completed in a
    prior session. The response includes a `source` field of either
    "memory" or "persisted".

    Call this repeatedly until state is 'done' or 'error'.
    Typically poll every 5–15 seconds for long-running tools.

    Args:
        job_id: The job_id string returned by the tool that dispatched the job.
    """
    record = job_dispatcher.get_status(job_id.strip())
    if record is None:
        return (
            f"No job found with id '{job_id}'. Not present in memory or "
            f"persisted history — check the id spelling or use list_jobs / "
            f"get_job_history to find recent jobs."
        )
    return json.dumps(record, indent=2)


@mcp.tool()
def list_jobs(state: str = "") -> str:
    """
    List all background jobs in the current session.

    Args:
        state: Optional filter. One of: pending, running, done, error.
               Leave blank to list all jobs.
    """
    records = job_dispatcher.list_all(state_filter=state.strip() or None)
    if not records:
        msg = f"No jobs with state '{state}'." if state else "No jobs dispatched this session."
        return msg

    summary = []
    for r in records:
        summary.append(
            {
                "job_id": r["job_id"],
                "tool": r["tool"],
                "state": r["state"],
                "scope": r["scope"],
                "dispatched_at": r["dispatched_at"],
                "duration_seconds": r["duration_seconds"],
                "checkpoint_path": r["checkpoint_path"],
            }
        )
    return json.dumps(summary, indent=2)


@mcp.tool()
def cancel_job(job_id: str) -> str:
    """
    Cancel a dispatched background job.

    If the job is still queued behind other work (the pool runs up to 4 jobs
    at once), it's removed before it ever spawns its cli.py subprocess. If
    it's already running, its subprocess is sent a termination signal — the
    job will settle into state 'cancelled' shortly after; poll
    get_job_status(job_id) to confirm. Jobs already done/error/cancelled are
    left alone.

    Args:
        job_id: The job_id string returned by the tool that dispatched the job.
    """
    result = job_dispatcher.cancel(job_id.strip())
    return json.dumps(result, indent=2)


@mcp.tool()
def check_checkpoint(tool: str, scope: str = "") -> str:
    """
    Check whether a completed checkpoint exists for a tool + scope combination.

    Use this to determine whether a prerequisite tool has already run on a
    given folder before dispatching a dependent tool. This is how the
    dependency gate works: tag_tracks before rename_files, rename_files before
    organize_library, and so on.

    Returns checkpoint metadata if found (including when it ran and the
    report path), or a clear 'not found' message.

    Args:
        tool:  Tool name to check. E.g. "tag_tracks", "audit_library".
        scope: The folder path the tool ran on. Leave blank for global tools.
    """
    cp = job_dispatcher.find_checkpoint(tool.strip(), scope.strip())
    if cp is None:
        return (
            f"No completed checkpoint found for tool='{tool}' scope='{scope}'. "
            f"Dispatch '{tool}' first, then wait for it to complete."
        )
    return json.dumps(cp, indent=2)


@mcp.tool()
def get_job_history(
    limit: int = 50,
    tool: str = "",
    state: str = "",
    scope: str = "",
) -> str:
    """
    Return persisted background job history from SQLite.

    Args:
        limit: Max rows to return (1-500). Default 50.
        tool: Optional tool name filter.
        state: Optional state filter: pending, running, done, error.
        scope: Optional scope path filter (canonicalized before matching).
    """
    records = job_dispatcher.get_history(
        limit=limit,
        tool=tool.strip() or None,
        state=state.strip() or None,
        scope=scope.strip() or None,
    )
    if not records:
        return "No persisted jobs found for the provided filters."
    return json.dumps(records, indent=2)


@mcp.tool()
def get_job_output(job_id: str, max_chars: int = 0) -> str:
    """
    Return output for a specific job, preferring full blob output when available.

    Args:
        job_id: Job identifier returned by dispatch tools.
        max_chars: Optional truncation limit for output text. 0 means no truncation.
    """
    if not job_id.strip():
        return "job_id is required."
    record = job_dispatcher.get_output(job_id.strip(), max_chars=max_chars)
    if record is None:
        return f"No persisted output found for job_id '{job_id}'."
    return json.dumps(record, indent=2)


# ─── Dev-mode tools (registered only when .dev sentinel or FABLEGEAR_DEV=1) ──

_DEV_MODE = (REPO_DIR / ".dev").exists() or os.environ.get("FABLEGEAR_DEV") == "1"
_log = logging.getLogger("fablegear.mcp_server")

if _DEV_MODE:
    _log.info("Dev mode active — registering diagnostic tools")

    @mcp.tool()
    def get_health_report() -> str:
        """
        [DEV] Deep introspection of FableGear state: tool dependency chain
        status, checkpoint integrity, config validation, known failure modes.
        """
        sections = []

        # Config
        cfg = _get_config()
        if cfg:
            sections.append("Config: OK")
            for k in ("local_db", "device_db", "music_root", "backup_dir"):
                p = Path(str(cfg.get(k, "")))
                exists = p.exists() if str(p) else False
                sections.append(f"  {k}: {p} ({'exists' if exists else 'MISSING'})")
        else:
            sections.append(f"Config: FAILED — {_CONFIG_ERROR or 'not configured'}")

        # Rekordbox state
        try:
            rb = rekordbox_is_running()
            sections.append(f"Rekordbox running: {'YES' if rb else 'No'}")
        except Exception as e:
            sections.append(f"Rekordbox check failed: {e}")

        # Dependency chain checkpoint status
        sections.append("\nDependency chain checkpoints:")
        music_root = _effective_music_root()
        for tool_name in ("audit_library", "find_duplicates", "tag_tracks",
                          "rename_files", "organize_library", "import_to_rekordbox",
                          "link_playlists"):
            cp = job_dispatcher.find_checkpoint(tool_name, music_root)
            status = f"completed ({cp.get('completed_at', '?')})" if cp else "MISSING"
            sections.append(f"  {tool_name}: {status}")

        # Job dispatcher state
        all_jobs = job_dispatcher.list_all()
        by_state = {}
        for j in all_jobs:
            by_state.setdefault(j["state"], []).append(j["tool"])
        sections.append(f"\nJobs this session: {len(all_jobs)}")
        for state, tools in sorted(by_state.items()):
            sections.append(f"  {state}: {len(tools)} ({', '.join(tools[:5])})")

        return "\n".join(sections)

    @mcp.tool()
    def get_tool_manifest() -> str:
        """
        [DEV] Structured dump of every registered MCP tool — name, parameters,
        dependency gates, and current gate status.
        """
        manifest = []
        music_root = _effective_music_root()
        for tool_name, prereq in _DEPENDENCY_MAP.items():
            cp = job_dispatcher.find_checkpoint(prereq, music_root)
            manifest.append({
                "tool": tool_name,
                "requires": prereq,
                "gate_satisfied": cp is not None,
            })
        gateless = [t for t in ("audit_library", "scan_novelty", "get_library_status",
                                "get_drive_status", "configure_paths", "relocate_tracks",
                                "get_job_status", "list_jobs", "check_checkpoint",
                                "get_job_history", "get_job_output")
                    if t not in _DEPENDENCY_MAP]
        for t in gateless:
            manifest.append({"tool": t, "requires": None, "gate_satisfied": True})
        return json.dumps(manifest, indent=2)

    @mcp.tool()
    def replay_job(job_id: str) -> str:
        """
        [DEV] Re-run a completed job from history with the same CLI args.
        Returns the new job_id for polling.
        """
        record = job_dispatcher.get_status(job_id.strip())
        if record is None:
            hist = job_dispatcher.get_output(job_id.strip())
            if hist:
                record = hist
        if record is None:
            return f"No job found with id '{job_id}'."

        cli_args = record.get("cli_args")
        tool = record.get("tool")
        scope = record.get("scope", "")
        if not cli_args or not tool:
            return f"Job '{job_id}' has no cli_args or tool name — cannot replay."

        return job_dispatcher.dispatch(
            tool=tool,
            cli_args=cli_args,
            scope=scope,
        )

    @mcp.tool()
    def get_logs(lines: int = 100, pattern: str = "") -> str:
        """
        [DEV] Tail the FableGear app log. Returns the last N lines,
        optionally filtered by a grep pattern.
        """
        import re as _re  # noqa: PLC0415
        log_file = REPO_DIR / "fablegear.log"
        if not log_file.exists():
            return "No log file found."
        try:
            all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if pattern:
                try:
                    rx = _re.compile(pattern, _re.IGNORECASE)
                    all_lines = [l for l in all_lines if rx.search(l)]
                except _re.error:
                    all_lines = [l for l in all_lines if pattern.lower() in l.lower()]
            tail = all_lines[-lines:]
            return "\n".join(tail) if tail else "(no matching lines)"
        except Exception as e:
            return f"Error reading log: {e}"

    @mcp.tool()
    def run_test_suite(test_path: str = "") -> str:
        """
        [DEV] Run the FableGear test suite and return results.
        Optionally pass a specific test file or pattern.
        """
        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]
        if test_path.strip():
            resolved = (REPO_DIR / test_path.strip()).resolve()
            if not str(resolved).startswith(str(REPO_DIR.resolve())):
                return "test_path must be within the FableGear repository."
            cmd.append(str(resolved))
        else:
            cmd.append(str(REPO_DIR / "tests"))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(REPO_DIR),
            )
            return (result.stdout + result.stderr).strip() or "Tests completed (no output)."
        except subprocess.TimeoutExpired:
            return "Test suite timed out after 300s."
        except Exception as e:
            return f"Failed to run tests: {e}"


# ─── Embedded server (started by main.py) ────────────────────────────────────

_mcp_thread: Optional[threading.Thread] = None
_mcp_running = threading.Event()
_mcp_port: Optional[int] = None
_mcp_host: Optional[str] = None


def _make_token_auth_app(app, token: str):
    """Wrap a Starlette/ASGI app with bearer token auth middleware."""
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import JSONResponse  # noqa: PLC0415

    async def middleware(scope, receive, send):
        if scope["type"] == "http":
            req = Request(scope, receive)
            # Allow health check without auth
            if req.url.path == "/health":
                await app(scope, receive, send)
                return
            # Check bearer token in header or query param
            auth = req.headers.get("authorization", "")
            query_token = req.query_params.get("token", "")
            if auth == f"Bearer {token}" or query_token == token:
                await app(scope, receive, send)
                return
            resp = JSONResponse(
                {"error": "Unauthorized — include Bearer token or ?token= query param"},
                status_code=401,
            )
            await resp(scope, receive, send)
            return
        # Non-HTTP scopes (lifespan, etc.) pass through
        await app(scope, receive, send)

    return middleware


def start_embedded(
    host: str = "127.0.0.1",
    port: int = 5002,
    token: str = "",
) -> bool:
    """Start the MCP SSE server in a daemon thread.

    Called by main.py during app startup. Returns True if the server
    started successfully, False otherwise.
    """
    global _mcp_thread, _mcp_port, _mcp_host

    if _mcp_running.is_set():
        _log.info("MCP server already running on %s:%d", _mcp_host, _mcp_port)
        return True

    _mcp_port = port
    _mcp_host = host

    def _run():
        try:
            import uvicorn  # noqa: PLC0415

            app = mcp.sse_app()

            # Wrap with token auth if binding to all interfaces
            if host != "127.0.0.1" and token:
                app = _make_token_auth_app(app, token)

            _mcp_running.set()
            _log.info("MCP SSE server starting on %s:%d (dev=%s)", host, port, _DEV_MODE)

            uvicorn.run(
                app,
                host=host,
                port=port,
                log_level="warning",
            )
        except Exception:
            _log.exception("MCP server failed to start")
            _mcp_running.clear()

    _mcp_thread = threading.Thread(target=_run, daemon=True, name="fablegear-mcp")
    _mcp_thread.start()

    # Brief wait to confirm startup
    import time  # noqa: PLC0415
    time.sleep(0.5)
    if not _mcp_thread.is_alive():
        _mcp_running.clear()
        _log.error("MCP server thread died immediately")
        return False

    return True


def stop_embedded() -> bool:
    """Signal the MCP server to stop. Returns True if it was running."""
    global _mcp_thread
    if not _mcp_running.is_set():
        return False
    _mcp_running.clear()
    # uvicorn doesn't have a clean shutdown from another thread;
    # since the thread is a daemon, it dies with the process.
    # For manual stop, we signal and let it wind down.
    _mcp_thread = None
    _mcp_port_val = _mcp_port
    _log.info("MCP server stop requested (port %s)", _mcp_port_val)
    return True


def is_running() -> bool:
    """Return True if the embedded MCP server is currently running."""
    return _mcp_running.is_set() and _mcp_thread is not None and _mcp_thread.is_alive()


def get_embedded_status() -> dict:
    """Return current embedded MCP server status."""
    return {
        "running": is_running(),
        "host": _mcp_host,
        "port": _mcp_port,
        "dev_mode": _DEV_MODE,
        "url": f"http://{_mcp_host}:{_mcp_port}/sse" if is_running() else None,
    }


# ── Entry point (standalone mode) ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="FableGear MCP Server — exposes FableGear tools to AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 mcp_server.py                    # stdio (Claude Desktop, Cursor)\n"
            "  python3 mcp_server.py --transport sse    # SSE on port 5002\n"
            "  python3 mcp_server.py --transport sse --port 9000\n"
            "  python3 mcp_server.py --embedded         # SSE, read config for port/host\n"
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol. 'stdio' for local clients. 'sse' for HTTP clients. (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5002,
        help="Port number for SSE transport. (default: 5002)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 for network access. (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--token",
        default="",
        help=(
            "Bearer token required on every request when --host is not loopback. "
            "If omitted on a non-loopback host, one is generated and printed once "
            "at startup. Clients send it as 'Authorization: Bearer <token>' or "
            "'?token=<token>'."
        ),
    )
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )

        app = mcp.sse_app()
        if args.host not in _LOOPBACK_HOSTS:
            token = args.token or generate_mcp_token()
            if not args.token:
                print(
                    "\n"
                    "  No --token supplied for a non-loopback host — generated one:\n"
                    f"\n  {token}\n\n"
                    "  Every request must include it as 'Authorization: Bearer <token>' "
                    "or '?token=<token>'.\n"
                    "  Anyone with this token has full read/write access to your "
                    "Rekordbox library and filesystem — treat it like a password.\n",
                    file=sys.stderr,
                )
            app = _make_token_auth_app(app, token)

        import uvicorn  # noqa: PLC0415

        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
