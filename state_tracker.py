"""
FableGear / state_tracker.py
Per-library persistent step tracking.
Stored as <library_root>/.fablegear_state.json
Created lazily on first use.
"""

import json
import logging
import os
import threading
from pathlib import Path
from datetime import datetime, timezone

STATE_FILENAME = ".fablegear_state.json"
log = logging.getLogger(__name__)
_STATE_WRITE_LOCK = threading.Lock()

def _state_path(library_root: str) -> Path:
    """Return the state file path inside the library root."""
    return Path(library_root).resolve() / STATE_FILENAME

def load_state(library_root: str) -> dict:
    """Load or return fresh default state."""
    path = _state_path(library_root)
    if not path.exists():
        return {
            "library_root": str(library_root),
            "fablegear_version": os.environ.get("FABLEGEAR_VERSION", "unknown"),
            "steps_completed": {},
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        # corrupt file → graceful fallback
        log.warning("state_tracker: could not load state from %s (%s) — starting fresh", path, exc)
        return {"library_root": str(library_root), "steps_completed": {}}




def save_state(library_root: str, state: dict):
    """Save with timestamp and ensure parent dir exists."""
    path = _state_path(library_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Guard: never write to filesystem roots or non-writable system dirs
    if not os.access(path.parent, os.W_OK):
        log.warning("state_tracker: %s is not writable — skipping state save", path.parent)
        return
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        payload = json.dumps(state, indent=2)
        with _STATE_WRITE_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
    except OSError as exc:
        log.warning("state_tracker: could not write state file %s — %s", path, exc)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

def mark_step_complete(library_root: str, step: str, exit_code: int):
    """Journal success/failure for a step. Safe no-op if no root."""
    if not library_root or not str(library_root).strip():
        return
    state = load_state(library_root)
    if "steps_completed" not in state:
        state["steps_completed"] = {}
    state["steps_completed"][step] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code
    }
    save_state(library_root, state)

def get_step_status(library_root: str) -> dict:
    """Return only the steps_completed dict (UI-friendly)."""
    if not library_root:
        return {}
    state = load_state(library_root)
    return state.get("steps_completed", {})
