"""
checkpoint.py — Checkpoint/resume support for long-running FableGear operations.

Checkpoints are stored in:
    ~/.fablegear/checkpoints/<tool>/<16-char-key>.json

The key is a deterministic SHA-256 hash of the scan roots + relevant config
options, so two runs with identical inputs share the same checkpoint slot.

Usage in a scan function
------------------------

    import threading
    from checkpoint import Checkpoint

    cancel_event = threading.Event()

    ck = Checkpoint(
        "duplicates",
        roots=[Path("/path/to/music")],
        config={"match_mode": "all", "fuzzy_threshold": "0.85"},
    )

    saved = ck.load()       # returns {} if no checkpoint exists
    if saved:
        # resume from saved["fp_map"], saved["completed"], etc.
        ...

    # Inside the scan loop, every N files:
    ck.save({"fp_map": ..., "completed": i, "total": total})

    # On clean completion:
    ck.reset()

Cancel / SIGINT handling
------------------------
The caller installs a SIGINT/SIGTERM handler that sets `cancel_event`.
Scan loops check `cancel_event.is_set()` after each unit of work, break
out, and call `ck.save(...)` before writing the partial report.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_CHECKPOINT_BASE: Path = Path.home() / ".fablegear" / "checkpoints"


def _config_key(tool: str, roots: list, config: dict) -> str:
    """Return a 16-char stable key for this (tool, roots, config) triple."""
    parts = [tool] + sorted(str(r) for r in roots) + [
        f"{k}={v}" for k, v in sorted(config.items())
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


class Checkpoint:
    """Persistent checkpoint for one tool + root(s) + config combination."""

    def __init__(
        self,
        tool: str,
        roots: list,
        config: dict | None = None,
    ) -> None:
        self.tool = tool
        self.roots = [Path(r) for r in roots]
        self.config = config or {}
        self._key = _config_key(tool, self.roots, self.config)
        self._dir = _CHECKPOINT_BASE / tool
        self._path = self._dir / f"{self._key}.json.gz"

    @property
    def path(self) -> Path:
        return self._path

    def _legacy_path(self) -> Path:
        return self._path.with_suffix("")

    def _candidate_paths(self) -> list[Path]:
        return [self._path, self._legacy_path()]

    def _existing_path(self) -> Path | None:
        for path in self._candidate_paths():
            if path.exists():
                return path
        return None

    @staticmethod
    def _read_payload(path: Path) -> dict:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_payload(path: Path, payload: dict, compress: bool) -> None:
        if compress:
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(payload, f)
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    # ── Existence / query ─────────────────────────────────────────────────────

    def exists(self) -> bool:
        """Return True if a checkpoint file exists for this slot."""
        return self._existing_path() is not None

    def info(self) -> dict:
        """
        Return lightweight metadata for the checkpoint without loading full data.

        Used by Flask routes for the pre-flight check so the UI can show the
        user a "resume or start over?" dialog before spawning the subprocess.
        """
        if not self._path.exists():
            legacy = self._legacy_path()
            if not legacy.exists():
                return {"exists": False}
        try:
            existing = self._existing_path()
            if existing is None:
                return {"exists": False}
            data = self._read_payload(existing)
            return {
                "exists": True,
                "tool": data.get("tool", self.tool),
                "saved_at": data.get("saved_at"),
                "completed": data.get("completed", 0),
                "total": data.get("total", 0),
                "roots": data.get("roots", [str(r) for r in self.roots]),
                "config": data.get("config", {}),
                "is_partial": True,
            }
        except Exception as exc:
            # Unlike load(), this stays broad and reports "exists but unreadable"
            # rather than {"exists": False} — the UI should still offer a
            # reset, not silently pretend there's nothing to resume.
            log.warning("Checkpoint info unreadable for %s: %s", self.tool, exc)
            return {"exists": True, "readable": False, "is_partial": True}

    # ── I/O ───────────────────────────────────────────────────────────────────

    def load(self) -> dict:
        """
        Load and return checkpoint data, or {} if absent / unreadable.
        Does NOT remove the checkpoint — call reset() on clean completion.
        """
        existing = self._existing_path()
        if existing is None:
            return {}
        try:
            data = self._read_payload(existing)
            log.info(
                "Checkpoint loaded for %s (completed=%s / total=%s, saved %s)",
                self.tool,
                data.get("completed", "?"),
                data.get("total", "?"),
                data.get("saved_at", "?"),
            )
            return data
        except Exception as exc:
            log.warning("Checkpoint unreadable — starting fresh: %s", exc)
            return {}

    def save(self, data: dict) -> None:
        """
        Atomically write checkpoint data to disk via a .tmp swap.
        Merges standard metadata (tool, roots, config, saved_at) with
        caller-supplied data.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            payload: dict = {
                "tool": self.tool,
                "roots": [str(r) for r in self.roots],
                "config": self.config,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                **data,
            }
            self._write_payload(tmp, payload, compress=True)
            tmp.replace(self._path)
            legacy = self._legacy_path()
            if legacy.exists():
                legacy.unlink(missing_ok=True)
        except Exception as exc:
            log.warning("Could not save checkpoint: %s", exc)

    def reset(self) -> None:
        """Delete the checkpoint file (clean completion or explicit user reset)."""
        try:
            self._path.unlink(missing_ok=True)
            self._legacy_path().unlink(missing_ok=True)
            log.info("Checkpoint cleared for %s", self.tool)
        except Exception as exc:
            log.warning("Could not remove checkpoint: %s", exc)


def reset_all(tool: str) -> int:
    """
    Delete every saved checkpoint for *tool*, regardless of which roots/config
    produced it.

    Used by the "Start Fresh" UI action: the frontend only knows the tool
    name, not the exact config dict the CLI subprocess derived internally
    (e.g. process's {bpm, key, normalize, enrich} from several flags) — so it
    can't reconstruct the same hash key a single-checkpoint reset would need.
    A user hitting "Start Fresh" only ever has one interrupted run of a given
    tool in mind; wiping every checkpoint under that tool's directory is the
    simple, unambiguous action that actually matches their intent.

    Returns the number of checkpoint files removed.
    """
    tool_dir = _CHECKPOINT_BASE / tool
    if not tool_dir.is_dir():
        return 0
    removed = 0
    for entry in tool_dir.iterdir():
        if entry.is_file() and (entry.suffix == ".gz" or entry.suffix == ".json"):
            try:
                entry.unlink()
                removed += 1
            except Exception as exc:
                log.warning("Could not remove checkpoint %s: %s", entry, exc)
    if removed:
        log.info("Reset %d checkpoint(s) for %s", removed, tool)
    return removed


# ── Flask-side helper ─────────────────────────────────────────────────────────

def check_checkpoint(tool: str, roots: list, config: dict | None = None) -> dict:
    """
    Convenience wrapper for Flask route pre-flight checks.

    Returns the checkpoint info dict ({exists: False} when absent).
    Never raises — safe to call from a request handler.
    """
    try:
        ck = Checkpoint(tool, roots, config or {})
        return ck.info()
    except Exception as exc:
        log.warning("check_checkpoint error: %s", exc)
        return {"exists": False}
