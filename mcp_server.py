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
import subprocess
import sys
from pathlib import Path

# ── MCP SDK ───────────────────────────────────────────────────────────────────

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "The 'mcp' package is required. Install with:\n"
        "  pip install mcp\n"
        "or add it to requirements.txt and reinstall.",
        file=sys.stderr,
    )
    sys.exit(1)

# ── FableGear imports (lazy — server still starts if config is absent) ─────────

_CONFIG_ERROR: str | None = None

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
_CLI = REPO_DIR / "cli.py"


def _cfg_gate() -> str | None:
    """Return an error string if FableGear is not yet configured."""
    if _CONFIG_ERROR:
        return (
            f"FableGear is not configured: {_CONFIG_ERROR}\n"
            "Run `python3 cli.py setup` in the FableGear directory first, "
            "then retry this tool."
        )
    return None


def _rb_gate() -> str | None:
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


def _run_cli(*args: str, timeout: int = 600) -> str:
    """
    Invoke cli.py with the given arguments.
    Returns combined stdout+stderr as a single string.
    All of cli.py's safety guards (Rekordbox check, DB backup, etc.) apply.
    """
    cmd = [sys.executable, str(_CLI), *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(REPO_DIR),
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"Command failed (exit {result.returncode}).\n\n{output}"
        return output or "Completed successfully."
    except subprocess.TimeoutExpired:
        return (
            f"Operation timed out after {timeout}s. "
            "Large libraries may need more time — try again with a smaller folder."
        )
    except Exception as exc:
        return f"Failed to launch FableGear CLI: {exc}"


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
    if _CONFIG_ERROR:
        return (
            f"FableGear is not configured: {_CONFIG_ERROR}\n"
            "Run `python3 cli.py setup` first."
        )

    rb_running = False
    try:
        rb_running = rekordbox_is_running()
    except Exception:
        pass

    lines = [
        "FableGear Library Status",
        "─" * 44,
        f"Rekordbox running : {'YES — close before any write tool' if rb_running else 'No'}",
        f"Local DB          : {LOCAL_DB}",
        f"Music root        : {MUSIC_ROOT}",
        f"Archive root      : {ARCHIVE_ROOT}",
    ]
    return "\n".join(lines)


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

    cli_args: list[str] = ["audit"]
    root = music_root.strip() or (str(MUSIC_ROOT) if MUSIC_ROOT else "")
    if root:
        cli_args += ["--root", root]
    for extra in [e.strip() for e in extra_roots.split(",") if e.strip()]:
        cli_args += ["--also-scan", extra]

    return _run_cli(*cli_args)


@mcp.tool()
def find_duplicates(
    music_root: str = "",
    match_mode: str = "exact",
    workers: int = 1,
) -> str:
    """
    Scan the library for acoustically identical files using Chromaprint
    fingerprinting. Returns a summary of duplicate groups and the path
    to a detailed CSV report for manual review.

    Requires the 'fpcalc' binary (install via: brew install chromaprint).
    Safe to run with Rekordbox open or closed.

    Args:
        music_root:  Folder to scan. Uses configured default if blank.
        match_mode:  "exact" (strict fingerprint match, default) or
                     "fuzzy" (catches near-identical files with minor edits).
        workers:     Parallel fingerprinting workers. Default 1.
                     Increase to 2–4 on fast SSDs; keep at 1 on external drives.
    """
    if err := _cfg_gate():
        return err

    root = music_root.strip() or (str(MUSIC_ROOT) if MUSIC_ROOT else "")
    if not root:
        return "No music root configured. Pass music_root or run `python3 cli.py setup`."

    return _run_cli(
        "duplicates", root,
        "--match-mode", match_mode,
        "--workers", str(workers),
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

    return _run_cli(*cli_args)


# ─── Write tools (Rekordbox must be closed) ────────────────────────────────────

@mcp.tool()
def tag_tracks(
    music_root: str = "",
    detect_bpm: bool = True,
    detect_key: bool = True,
    normalize_loudness: bool = True,
    workers: int = 1,
) -> str:
    """
    Analyze audio files and write BPM, musical key (Camelot notation),
    and loudness (LUFS normalization) tags to all audio files under
    the given path. Uses librosa for analysis and mutagen for tag writes.

    REQUIRES Rekordbox to be closed.

    Args:
        music_root:         Folder to process. Uses configured default if blank.
        detect_bpm:         Detect and write BPM values. Default True.
        detect_key:         Detect and write key in Camelot notation. Default True.
        normalize_loudness: Normalize loudness to the configured LUFS target. Default True.
        workers:            Parallel workers. Default 1 (safe). Increase on fast SSDs.
    """
    if err := _cfg_gate():
        return err
    if err := _rb_gate():
        return err

    root = music_root.strip() or (str(MUSIC_ROOT) if MUSIC_ROOT else "")
    if not root:
        return "No music root configured. Pass music_root or run `python3 cli.py setup`."

    cli_args = ["process", root, "--workers", str(workers)]
    if not detect_bpm:
        cli_args.append("--no-bpm")
    if not detect_key:
        cli_args.append("--no-key")
    if not normalize_loudness:
        cli_args.append("--no-normalize")

    # Large libraries can take hours — generous timeout
    return _run_cli(*cli_args, timeout=14400)


@mcp.tool()
def import_to_rekordbox(
    source_path: str,
    dry_run: bool = False,
    resume: bool = True,
) -> str:
    """
    Import audio files from a folder into the Rekordbox database.
    Each audio file found under source_path is registered as a new track.

    REQUIRES Rekordbox to be closed (unless dry_run=True).

    Args:
        source_path: Path to the folder containing audio files to import.
        dry_run:     Preview what would be imported without writing. Default False.
        resume:      Skip tracks already present in the database. Default True.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    if not source_path.strip():
        return "source_path is required."

    cli_args = ["import", source_path.strip()]
    if dry_run:
        cli_args.append("--dry-run")
    if resume:
        cli_args.append("--resume")

    return _run_cli(*cli_args)


@mcp.tool()
def link_playlists(
    source_path: str,
    dry_run: bool = False,
) -> str:
    """
    Match imported tracks to existing Rekordbox playlists based on the
    folder structure of the source path. Run this after import_to_rekordbox
    to wire tracks into the correct playlists automatically.

    REQUIRES Rekordbox to be closed (unless dry_run=True).

    Args:
        source_path: The same folder used for import.
        dry_run:     Preview playlist matches without writing. Default False.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    if not source_path.strip():
        return "source_path is required."

    cli_args = ["link", source_path.strip()]
    if dry_run:
        cli_args.append("--dry-run")

    return _run_cli(*cli_args)


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
                  the Rekordbox database (e.g. /Volumes/OLD/DJMT PRIMARY).
        new_root: The new folder path where the files now live
                  (e.g. /Volumes/DJMT/DJMT PRIMARY).
    """
    if err := _cfg_gate():
        return err
    if err := _rb_gate():
        return err

    if not old_root.strip() or not new_root.strip():
        return "Both old_root and new_root are required."

    return _run_cli("relocate", old_root.strip(), new_root.strip())


@mcp.tool()
def organize_library(
    source_path: str,
    target_path: str,
    dry_run: bool = True,
    mode: str = "assimilate",
    mix_threshold_minutes: float = 15.0,
    workers: int = 1,
) -> str:
    """
    Consolidate audio files into a clean Artist / Album / Track folder
    hierarchy. Mixes and long sets (above mix_threshold_minutes) are routed
    to a 'Live Sets & Mixes' folder automatically.

    Defaults to dry_run=True — always preview first.
    REQUIRES Rekordbox to be closed when dry_run=False.

    Args:
        source_path:            Folder to scan for audio files.
        target_path:            Root of the organized library destination.
        dry_run:                Preview moves without executing. Default True (safe).
        mode:                   "assimilate" (move files, prune empty dirs from source)
                                or "integrate" (copy only — source is never modified).
        mix_threshold_minutes:  Tracks at or above this length go to Live Sets & Mixes.
                                Default 15.0 minutes.
        workers:                Parallel I/O workers for the move phase. Default 1.
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

    return _run_cli(*cli_args, timeout=7200)


@mcp.tool()
def rename_files(
    source_path: str = "",
    dry_run: bool = True,
    workers: int = 1,
) -> str:
    """
    Rename audio files to clean titles derived from their embedded metadata
    (ID3 tags / Vorbis comments). Uses learned rules from previous sessions
    to auto-approve known artist and producer name patterns.

    Defaults to dry_run=True — always preview the proposed renames first.
    REQUIRES Rekordbox to be closed when dry_run=False.

    Args:
        source_path: Folder containing files to rename.
                     Uses configured music root if blank.
        dry_run:     Preview proposed renames without executing. Default True (safe).
        workers:     Parallel workers. Default 1.
    """
    if err := _cfg_gate():
        return err
    if not dry_run:
        if err := _rb_gate():
            return err

    root = source_path.strip() or (str(MUSIC_ROOT) if MUSIC_ROOT else "")
    if not root:
        return "No source path configured. Pass source_path or run `python3 cli.py setup`."

    cli_args = ["rename", root, "--workers", str(workers)]
    if not dry_run:
        cli_args.append("--no-dry-run")

    return _run_cli(*cli_args)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="FableGear MCP Server — exposes FableGear tools to AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 mcp_server.py                    # stdio (Claude Desktop, Cursor)\n"
            "  python3 mcp_server.py --transport sse    # SSE on port 8765\n"
            "  python3 mcp_server.py --transport sse --port 9000\n"
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
        default=8765,
        help="Port number for SSE transport. (default: 8765)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
