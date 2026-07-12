"""
fablegear / health_acoustid.py

Read-only preflight checks for AcoustID enrichment.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path


def _find_fpcalc() -> str | None:
    """Locate fpcalc from PATH, then common Homebrew locations."""
    found = shutil.which("fpcalc")
    if found:
        return found
    for fallback in ("/opt/homebrew/bin/fpcalc", "/usr/local/bin/fpcalc"):
        if Path(fallback).exists():
            return fallback
    return None


def _fpcalc_available() -> bool:
    """Return True if fpcalc is executable and responds to -version."""
    fpcalc = _find_fpcalc()
    if not fpcalc:
        return False
    os.environ["FPCALC"] = fpcalc
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
    return bool(str(ACOUSTID_API_KEY).strip())


def full_health_check(*, raise_on_fail: bool = False) -> bool:
    """
    Return True when AcoustID enrichment prerequisites are met.

    Checks:
    - config exposes a non-empty ACOUSTID_API_KEY
    - pyacoustid module is importable
    - fpcalc is available and executable
    """
    checks = (
        ("acoustid_api_key is not configured", _acoustid_key_configured()),
        ("pyacoustid is not installed/importable", _acoustid_module_available()),
        ("fpcalc is not available", _fpcalc_available()),
    )
    failures = [message for message, ok in checks if not ok]
    if not failures:
        return True
    if raise_on_fail:
        raise RuntimeError("; ".join(failures))
    return False
