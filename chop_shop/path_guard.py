"""
path_guard.py — shared source guardrails for every file-touching tool.

Born from a live incident: an organize run pointed at the user's home folder
moved rekordbox/Pioneer app resources into the music library and pruned
directories inside Apple Photos' app container. The rule, app-wide:

    FableGear tools scan MUSIC LOCATIONS — folders and drives — never the
    top of an operating system, the user profile itself, or app-data trees.

Every scanning tool calls guard_sources() on its roots before touching the
filesystem. Callers surface the ValueError as a clear, actionable message.
"""

from __future__ import annotations

from pathlib import Path


def forbidden_source_reason(source: Path) -> str | None:
    """Return why *source* is unsafe to scan/mutate from, or None if it's fine.

    Whole external drives (/Volumes/<name>) are legitimate DJ sources.
    System roots, the user profile itself, and app-data trees are not.
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
                "tool at a music folder instead.")
    if src in (Path("/Users"), Path("/System"), Path("/Applications"),
               Path("/Library"), Path("/private"), Path("/Volumes")):
        return "this is an operating-system area, not a music folder"
    if src == home / "Library" or (home / "Library") in src.parents:
        return "~/Library holds application data — never music to organize"
    if home in src.parents and src.name == "Library":
        return "Library folders hold application data"
    return None


def guard_sources(sources, tool: str) -> None:
    """Raise ValueError if any source is unsafe for *tool* to operate on."""
    for s in sources:
        reason = forbidden_source_reason(Path(s))
        if reason:
            raise ValueError(f"Refusing to run {tool} on {s}: {reason}")
