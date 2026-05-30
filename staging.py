"""
FableGear / staging.py
Persistent staging queue — the deliberate hand-off between Record Room and Chop Shop.

Paths added here become the default scope for Chop Shop tool operations so
tools never silently reach outside what the user explicitly chose to work on.

State file: ~/.fablegear/staging.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_STAGING_PATH = Path.home() / ".fablegear" / "staging.json"


def _load() -> dict:
    if _STAGING_PATH.exists():
        try:
            return json.loads(_STAGING_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"items": [], "saved_batches": {}}


def _save(data: dict) -> None:
    _STAGING_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _STAGING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("staging: could not write %s — %s", _STAGING_PATH, exc)


def get_items() -> list[dict]:
    return _load()["items"]


def add_items(paths: list[str]) -> list[dict]:
    """Append paths (deduplicates by resolved path). Returns updated list."""
    data = _load()
    existing = {item["path"] for item in data["items"]}
    now = datetime.now(timezone.utc).isoformat()
    for raw in paths:
        p = Path(raw)
        key = str(p)
        if key not in existing:
            data["items"].append({
                "path": key,
                "name": p.name,
                "is_dir": p.is_dir(),
                "added_at": now,
            })
            existing.add(key)
    _save(data)
    return data["items"]


def remove_item(path: str) -> list[dict]:
    """Remove one item. Returns updated list."""
    data = _load()
    data["items"] = [i for i in data["items"] if i["path"] != path]
    _save(data)
    return data["items"]


def clear_items() -> list[dict]:
    """Remove all staged items. Returns empty list."""
    data = _load()
    data["items"] = []
    _save(data)
    return data["items"]


def save_batch(name: str) -> dict:
    """Snapshot the current queue as a named batch. Returns all saved batches."""
    data = _load()
    data.setdefault("saved_batches", {})[name] = {
        "items": list(data["items"]),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(data)
    return data["saved_batches"]


def list_batches() -> dict:
    return _load().get("saved_batches", {})


def load_batch(name: str) -> list[dict]:
    """Replace queue with a saved batch. Returns updated list."""
    data = _load()
    batch = data.get("saved_batches", {}).get(name)
    if not batch:
        return data["items"]
    data["items"] = list(batch["items"])
    _save(data)
    return data["items"]


def delete_batch(name: str) -> dict:
    """Delete a saved batch. Returns remaining batches."""
    data = _load()
    data.get("saved_batches", {}).pop(name, None)
    _save(data)
    return data.get("saved_batches", {})
