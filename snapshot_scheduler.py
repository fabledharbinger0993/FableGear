"""
Background scheduler for periodic archive savepoints.

The scheduler keeps the cadence lightweight: it wakes up infrequently, checks
whether the configured interval has elapsed, and only then writes snapshots.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_STARTUP_DELAY = 120
_POLL_INTERVAL = 60 * 60
_state_lock = threading.Lock()
_started = False
_status = {
    "last_run": None,
    "last_error": None,
    "last_paths": [],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path() -> Path:
    from config import SNAPSHOT_STATE_FILE  # noqa: PLC0415
    return SNAPSHOT_STATE_FILE


def _load_state() -> dict:
    path = _state_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # State file does not exist yet; treat as "never run"
        return {}
    except json.JSONDecodeError:
        log.warning(
            "Snapshot state file %s contains invalid JSON; ignoring state and treating as empty.",
            path,
        )
        return {}
    except Exception:
        # Unexpected errors (permissions, corruption, etc.) are logged for diagnosis
        log.exception(
            "Unexpected error while loading snapshot state from %s; treating state as empty.",
            path,
        )
        return {}


def _save_state(payload: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _snapshot_master_db(timestamp: str) -> str:
    from config import BACKUP_DIR, LOCAL_DB  # noqa: PLC0415

    if not LOCAL_DB.exists():
        raise RuntimeError(f"Local Rekordbox DB not found at {LOCAL_DB}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"rekordbox.master.backup_{timestamp}.db"
    shutil.copy2(LOCAL_DB, target)
    return str(target)


def _snapshot_device_db() -> str:
    from db_connection import _backup_db  # noqa: PLC0415
    from config import DEVICE_DB  # noqa: PLC0415

    return str(_backup_db(DEVICE_DB))


def _perform_snapshot() -> dict:
    from config import SNAPSHOT_INCLUDE_MASTER_DB, SNAPSHOT_INTERVAL_SECONDS  # noqa: PLC0415
    from db_connection import rekordbox_is_running  # noqa: PLC0415

    if rekordbox_is_running():
        raise RuntimeError("Rekordbox is running; snapshot skipped")

    timestamp = _now().strftime("%Y%m%d_%H%M%S")
    paths: list[str] = []
    errors: list[str] = []

    try:
        paths.append(_snapshot_device_db())
    except Exception as exc:
        log.warning("snapshot_scheduler: device DB snapshot failed — %s", exc)
        errors.append(f"device DB: {exc}")

    if SNAPSHOT_INCLUDE_MASTER_DB:
        try:
            paths.append(_snapshot_master_db(timestamp))
        except Exception as exc:
            log.warning("snapshot_scheduler: master.db snapshot failed — %s", exc)
            errors.append(f"master DB: {exc}")

    if not paths:
        raise RuntimeError("No snapshot files were written")

    payload = {
        "last_run": _now().isoformat(),
        "last_interval_seconds": SNAPSHOT_INTERVAL_SECONDS,
        "last_paths": paths,
        "last_error": "; ".join(errors) if errors else None,
    }
    with _state_lock:
        _status.update(payload)
    try:
        _save_state(payload)
    except Exception as exc:
        with _state_lock:
            _status["last_error"] = f"Could not persist snapshot state: {exc}"
        log.warning("snapshot_scheduler: could not persist state — %s", exc)
    return dict(_status)


def run_now() -> dict:
    """Run a snapshot immediately."""
    try:
        return _perform_snapshot()
    except Exception as exc:
        with _state_lock:
            _status["last_error"] = str(exc)
        log.warning("snapshot_scheduler: run failed — %s", exc)
        return dict(_status)


def get_status() -> dict:
    with _state_lock:
        return dict(_status)


def _due(last_run: str | None, interval_seconds: int) -> bool:
    if not last_run:
        return True
    try:
        then = datetime.fromisoformat(last_run)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (_now() - then).total_seconds() >= interval_seconds


def _background_loop() -> None:
    time.sleep(_STARTUP_DELAY)
    while True:
        try:
            from config import SNAPSHOT_INTERVAL_SECONDS  # noqa: PLC0415
        except Exception as exc:
            log.warning("snapshot_scheduler: config unavailable — %s", exc)
            time.sleep(_POLL_INTERVAL)
            continue

        state = _load_state()
        if _due(state.get("last_run"), SNAPSHOT_INTERVAL_SECONDS):
            run_now()
        time.sleep(_POLL_INTERVAL)


def start_background_scheduler() -> None:
    """Start the periodic snapshot scheduler once per process."""
    global _started
    with _state_lock:
        if _started:
            return
        _started = True

    thread = threading.Thread(target=_background_loop, daemon=True, name="snapshot-scheduler")
    thread.start()
    log.info("snapshot_scheduler: background scheduler started")

