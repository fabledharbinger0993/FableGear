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
        roots=[Path("/Volumes/DJMT")],
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
        self._path = self._dir / f"{self._key}.json"

    @property
    def path(self) -> Path:
        return self._path

    # ── Existence / query ─────────────────────────────────────────────────────

    def exists(self) -> bool:
        """Return True if a checkpoint file exists for this slot."""
        return self._path.exists()

    def info(self) -> dict:
        """
        Return lightweight metadata for the checkpoint without loading full data.

        Used by Flask routes for the pre-flight check so the UI can show the
        user a "resume or start over?" dialog before spawning the subprocess.
        """
        if not self._path.exists():
            return {"exists": False}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
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
        except Exception:
            return {"exists": True, "readable": False, "is_partial": True}

    # ── I/O ───────────────────────────────────────────────────────────────────

    def load(self) -> dict:
        """
        Load and return checkpoint data, or {} if absent / unreadable.
        Does NOT remove the checkpoint — call reset() on clean completion.
        """
        if not self._path.exists():
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
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
            tmp = self._path.with_suffix(".tmp")
            payload: dict = {
                "tool": self.tool,
                "roots": [str(r) for r in self.roots],
                "config": self.config,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                **data,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            tmp.replace(self._path)
        except Exception as exc:
            log.warning("Could not save checkpoint: %s", exc)

    def reset(self) -> None:
        """Delete the checkpoint file (clean completion or explicit user reset)."""
        try:
            self._path.unlink(missing_ok=True)
            log.info("Checkpoint cleared for %s", self.tool)
        except Exception as exc:
            log.warning("Could not remove checkpoint: %s", exc)


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
