#!/usr/bin/env python3
"""Command wrapper for the passive FableGear live-link daemon."""

from __future__ import annotations

try:
    from .daemon import main
except ImportError:  # pragma: no cover - direct script execution fallback
    from daemon import main


if __name__ == "__main__":
    raise SystemExit(main())
