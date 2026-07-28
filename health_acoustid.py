"""
fablegear / health_acoustid.py

Read-only preflight checks for AcoustID enrichment.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path


def _find_fpcalc() -> str | None:
    """Locate fpcalc from $FPCALC / PATH, then common Homebrew locations."""
    import os  # noqa: PLC0415

    env_fpcalc = os.environ.get("FPCALC", "").strip()
    if env_fpcalc:
        found = shutil.which(env_fpcalc)
        if found:
            return found

    found = shutil.which("fpcalc")
    if found:
        return found
    for fallback in ("/opt/homebrew/bin/fpcalc", "/usr/local/bin/fpcalc"):
        if Path(fallback).exists():
            return fallback
    return None


def _fpcalc_available(fpcalc: str | None) -> bool:
    """Return True if fpcalc is executable and responds to -version."""
    if not fpcalc:
        return False
    try:
        result = subprocess.run(
            [fpcalc, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _acoustid_module_available() -> bool:
    return importlib.util.find_spec("acoustid") is not None


def _acoustid_key_configured() -> bool:
    try:
        from config import ACOUSTID_API_KEY  # noqa: PLC0415
    except Exception:
        return False
    if ACOUSTID_API_KEY is None:
        return False
    return bool(str(ACOUSTID_API_KEY).strip())


def collect_health() -> dict[str, str | bool]:
    """Collect AcoustID enrichment prerequisite status without side effects."""
    fpcalc = _find_fpcalc()
    key_ok = _acoustid_key_configured()
    module_ok = _acoustid_module_available()
    fpcalc_ok = _fpcalc_available(fpcalc)
    return {
        "ok": key_ok and module_ok and fpcalc_ok,
        "key_ok": key_ok,
        "module_ok": module_ok,
        "fpcalc_ok": fpcalc_ok,
        "fpcalc_path": fpcalc or "",
    }


def full_health_check(*, raise_on_fail: bool = False) -> bool:
    """
    Return True when AcoustID enrichment prerequisites are met.

    Checks:
    - config exposes a non-empty ACOUSTID_API_KEY
    - pyacoustid module is importable
    - fpcalc is available and executable
    """
    health = collect_health()
    checks = (
        ("AcoustID API key is not configured", bool(health["key_ok"])),
        ("pyacoustid is not installed/importable", bool(health["module_ok"])),
        ("fpcalc is not available", bool(health["fpcalc_ok"])),
    )
    failures = [message for message, ok in checks if not ok]
    if not failures:
        return True
    if raise_on_fail:
        raise RuntimeError("; ".join(failures))
    return False
