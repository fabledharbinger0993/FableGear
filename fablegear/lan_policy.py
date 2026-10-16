"""Whether the owner has deliberately made FableGear's UI reachable from their network.

Deliberately tiny and stdlib-only: main.py consults it before the app is imported, and app.py's network guard
consults it on every request, so the two can never disagree.
"""

import json
from pathlib import Path


def lan_ui_enabled(home: Path | None = None) -> bool:
    """True only if ``~/.fablegear/config.json`` contains ``{"allow_lan_ui": true}`` (the JSON boolean).

    This is the one switch between "loopback only" and "reachable on the network", so it is strict: a missing
    file, unreadable or invalid JSON, a non-object, ``"true"``, ``"false"``, ``1``, ``"yes"`` or ``null`` all mean
    loopback only. Only a literal ``true`` opens the server.
    """
    cfg = (home or Path.home()) / ".fablegear" / "config.json"
    try:
        return json.loads(cfg.read_text()).get("allow_lan_ui") is True
    except Exception:
        return False
