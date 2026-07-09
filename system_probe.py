"""
fablegear / system_probe.py

Detects host hardware capabilities once at import time and returns recommended
operating constants for FableGear's scan / import / archive loops.

Priority order when resolving values:
  1. Explicit ``"performance"`` stanza in ``~/.fablegear/config.json``
  2. Auto-detected hardware tier (available RAM, physical CPU cores, storage type)
  3. Conservative static fallbacks when psutil is unavailable

Public surface:
  SYSTEM_PROFILE  — module-level singleton (SystemProfile dataclass)
  detect_system_profile() — call to re-probe (only needed in tests or CLI)

Subprocess notes:
  ``_detect_storage_type`` and ``_has_gpu`` spawn short-lived ``system_profiler``
  (macOS) or ``/sys/block`` reads (Linux) with a 3-second hard timeout. Both
  fail silently so a missing or slow system_profiler never blocks startup.
  Their results are cached in ~/.fablegear/hardware_probe_cache.json for 24
  hours — hardware doesn't change between runs, and every dispatched CLI job
  subprocess imports this module, so uncached probes would tax each job with
  up to two system_profiler spawns. RAM and CPU stay live (psutil, no
  subprocess) because available RAM drives tier selection.
"""
from __future__ import annotations

import json
import logging
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# ─── RAM tier thresholds (available RAM in GB) ────────────────────────────────

_RAM_MID_GB   =  4.0
_RAM_HIGH_GB  = 12.0
_RAM_XHIGH_GB = 32.0

# ─── Tier parameter table ─────────────────────────────────────────────────────
# (batch_size, archive_chunk_size, progress_item_interval, progress_min_seconds, max_workers)
# max_workers=0 is a sentinel meaning "use physical CPU core count at detection time".

_TIER_LOW   = (100,   100,  200, 0.50, 2)
_TIER_MID   = (250,   250,  100, 0.25, 0)
_TIER_HIGH  = (500,   500,   50, 0.15, 0)
_TIER_XHIGH = (1000, 1000,   25, 0.10, 0)

# SSD penalty multiplier on progress_min_seconds:
# disk I/O is faster → loops run faster → we can afford a tighter time gate.
_SSD_MIN_SEC_FACTOR = 0.75


@dataclass(frozen=True)
class SystemProfile:
    """Recommended operating parameters derived from host hardware."""

    batch_size: int
    archive_chunk_size: int
    progress_item_interval: int
    progress_min_seconds: float
    max_workers: int

    # Diagnostic metadata (not used as constants, but useful in logs / UI)
    ram_available_gb: float
    cpu_cores: int
    storage_type: str   # "ssd" | "hdd" | "unknown"
    gpu_available: bool
    source: str         # "override" | "detected" | "fallback"


# ─── Hardware probe cache ─────────────────────────────────────────────────────
# Storage type and GPU presence don't change between runs, but probing them
# spawns system_profiler (up to 3s each). Cache on disk so only the first
# process of the day pays; all cache failures degrade to a fresh probe.

_HW_CACHE_PATH = Path.home() / ".fablegear" / "hardware_probe_cache.json"
_HW_CACHE_TTL_SECONDS = 24 * 3600.0


def _hw_cache_load() -> dict:
    try:
        if _HW_CACHE_PATH.exists():
            if time.time() - _HW_CACHE_PATH.stat().st_mtime < _HW_CACHE_TTL_SECONDS:
                data = json.loads(_HW_CACHE_PATH.read_text())
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _hw_cache_store(key: str, value) -> None:
    try:
        data = _hw_cache_load()
        data[key] = value
        _HW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HW_CACHE_PATH.with_name(_HW_CACHE_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(_HW_CACHE_PATH)
    except Exception:
        pass


# ─── Hardware probe helpers ───────────────────────────────────────────────────

def _available_ram_gb() -> float:
    """Return currently available (not total installed) RAM in GB, or 0.0 on failure."""
    try:
        import psutil  # noqa: PLC0415
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return 0.0


def _physical_cores() -> int:
    """Return physical CPU core count, or 2 as a conservative fallback."""
    try:
        import psutil  # noqa: PLC0415
        return psutil.cpu_count(logical=False) or 2
    except Exception:
        return 2


def _detect_storage_type() -> str:
    """
    Try to determine whether the primary disk is SSD or HDD.

    - macOS: parses ``system_profiler SPStorageDataType -detailLevel mini``
    - Linux: reads ``/sys/block/<dev>/queue/rotational``
    - All other platforms / any error: returns ``"unknown"``

    Conclusive results are cached for 24h (see _HW_CACHE_PATH).
    """
    cached = _hw_cache_load().get("storage_type")
    if cached in ("ssd", "hdd"):
        return cached

    system = platform.system()
    result_type = "unknown"
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["system_profiler", "SPStorageDataType", "-detailLevel", "mini"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if "Solid State: Yes" in result.stdout:
                result_type = "ssd"
            elif "Solid State: No" in result.stdout:
                result_type = "hdd"

        elif system == "Linux":
            block = Path("/sys/block")
            if block.is_dir():
                for dev in sorted(block.iterdir()):
                    rotational_path = dev / "queue" / "rotational"
                    if rotational_path.exists():
                        rotational = rotational_path.read_text().strip()
                        result_type = "hdd" if rotational == "1" else "ssd"
                        break

    except Exception:
        pass
    if result_type != "unknown":
        _hw_cache_store("storage_type", result_type)
    return result_type


def _has_gpu() -> bool:
    """
    Returns True if a discrete GPU or Metal display device is detectable.

    - macOS: parses ``system_profiler SPDisplaysDataType -detailLevel mini``
    - Other platforms: probes ``nvidia-smi``
    - Any error: returns False

    Results are cached for 24h (see _HW_CACHE_PATH).
    """
    cached = _hw_cache_load().get("gpu_available")
    if isinstance(cached, bool):
        return cached

    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            gpu = "Chipset Model" in result.stdout
        else:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            gpu = result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False
    _hw_cache_store("gpu_available", gpu)
    return gpu


def _read_user_overrides() -> dict:
    """
    Load the ``"performance"`` stanza from ``~/.fablegear/config.json``.

    Recognised keys (all optional):
      batch_size, archive_chunk_size, progress_item_interval,
      progress_min_seconds, max_workers

    Returns an empty dict if the file is absent, unreadable, or has no
    ``"performance"`` key.
    """
    try:
        cfg_path = Path.home() / ".fablegear" / "config.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text())
            overrides = data.get("performance", {})
            if overrides:
                return dict(overrides)
    except Exception:
        pass
    return {}


def _override_value(overrides: dict, key: str, fallback, cast):
    """
    Coerce one user override, keeping the detected value when the entry is
    missing or malformed. This runs at import time, so a typo in
    ~/.fablegear/config.json must never raise.
    """
    if key not in overrides:
        return fallback
    try:
        return cast(overrides[key])
    except (TypeError, ValueError):
        log.warning(
            "system_probe: ignoring invalid performance override %s=%r (expected %s)",
            key, overrides[key], cast.__name__,
        )
        return fallback


# ─── Main detection ───────────────────────────────────────────────────────────

def detect_system_profile() -> SystemProfile:
    """
    Probe the host hardware and return a :class:`SystemProfile` with
    recommended operating parameters.

    This function is safe to call multiple times but does spawn subprocess
    calls for storage/GPU detection on macOS. Cache the result (the module
    already does this via :data:`SYSTEM_PROFILE`) and avoid calling in loops.
    """
    ram_gb    = _available_ram_gb()
    cpu_cores = _physical_cores()
    storage   = _detect_storage_type()
    gpu       = _has_gpu()
    overrides = _read_user_overrides()

    # Select tier from available RAM
    if ram_gb == 0 or ram_gb < _RAM_MID_GB:
        tier      = _TIER_LOW
        tier_name = "low"
    elif ram_gb < _RAM_HIGH_GB:
        tier      = _TIER_MID
        tier_name = "mid"
    elif ram_gb < _RAM_XHIGH_GB:
        tier      = _TIER_HIGH
        tier_name = "high"
    else:
        tier      = _TIER_XHIGH
        tier_name = "xhigh"

    batch_size, archive_chunk_size, progress_item_interval, progress_min_seconds, workers_sentinel = tier

    # Resolve the max_workers sentinel (0 → physical core count)
    max_workers = cpu_cores if workers_sentinel == 0 else workers_sentinel

    # SSD: tighten the minimum emit interval — I/O is faster so loops run faster
    if storage == "ssd":
        progress_min_seconds = max(0.05, progress_min_seconds * _SSD_MIN_SEC_FACTOR)

    source = "detected"

    if overrides:
        batch_size             = _override_value(overrides, "batch_size",             batch_size,             int)
        archive_chunk_size     = _override_value(overrides, "archive_chunk_size",     archive_chunk_size,     int)
        progress_item_interval = _override_value(overrides, "progress_item_interval", progress_item_interval, int)
        progress_min_seconds   = _override_value(overrides, "progress_min_seconds",   progress_min_seconds,   float)
        max_workers            = _override_value(overrides, "max_workers",            max_workers,            int)
        source = "override"

    # Floor everything a consumer divides by, chunks with, or hands to a thread
    # pool — a zero or negative value would crash mid-run (modulo throttles,
    # chunked writes, ThreadPoolExecutor), so bad overrides degrade instead.
    batch_size             = max(1, batch_size)
    archive_chunk_size     = max(1, archive_chunk_size)
    progress_item_interval = max(1, progress_item_interval)
    progress_min_seconds   = max(0.0, progress_min_seconds)
    max_workers            = max(1, max_workers)

    log.debug(
        "system_probe: tier=%s ram_avail=%.1fGB cores=%d storage=%s gpu=%s "
        "→ batch=%d chunk=%d interval=%d min_sec=%.2f workers=%d source=%s",
        tier_name, ram_gb, cpu_cores, storage, gpu,
        batch_size, archive_chunk_size, progress_item_interval,
        progress_min_seconds, max_workers, source,
    )

    return SystemProfile(
        batch_size=batch_size,
        archive_chunk_size=archive_chunk_size,
        progress_item_interval=progress_item_interval,
        progress_min_seconds=progress_min_seconds,
        max_workers=max_workers,
        ram_available_gb=ram_gb,
        cpu_cores=cpu_cores,
        storage_type=storage,
        gpu_available=gpu,
        source=source,
    )


# ─── Module-level singleton ───────────────────────────────────────────────────
# Detected once at import time. Consumers (config.py, etc.) import this name.
# Do not re-run detect_system_profile() outside of tests.

SYSTEM_PROFILE: SystemProfile = detect_system_profile()
