"""
Tests for system_probe.detect_system_profile().

All psutil calls and subprocess calls are mocked so these tests run on any
machine regardless of actual hardware. The goal is to verify:
  - correct tier selection from available RAM
  - max_workers sentinel resolution
  - SSD reduces progress_min_seconds
  - user overrides win over auto-detection
  - psutil ImportError produces a safe fallback profile
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# system_probe runs detect_system_profile() at module level; we import it
# once here but call detect_system_profile() fresh in each test with mocks.
import system_probe as sp


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_vmem(available_gb: float):
    """Return a mock psutil.virtual_memory() result."""
    m = MagicMock()
    m.available = int(available_gb * 1024 ** 3)
    return m


def _probe(available_gb: float, cores: int = 4, storage: str = "unknown", gpu: bool = False, overrides: dict | None = None):
    """
    Run detect_system_profile() under controlled mocks and return the profile.
    """
    psutil_mock = MagicMock()
    psutil_mock.virtual_memory.return_value = _make_vmem(available_gb)
    psutil_mock.cpu_count.return_value = cores

    override_return = overrides if overrides is not None else {}

    with (
        patch.dict(sys.modules, {"psutil": psutil_mock}),
        patch.object(sp, "_detect_storage_type", return_value=storage),
        patch.object(sp, "_has_gpu", return_value=gpu),
        patch.object(sp, "_read_user_overrides", return_value=override_return),
    ):
        return sp.detect_system_profile()


# ─── Tier selection ───────────────────────────────────────────────────────────

class TestRamTierSelection:
    def test_low_tier_below_4gb(self):
        p = _probe(available_gb=2.0, cores=2)
        assert p.batch_size == 100
        assert p.archive_chunk_size == 100
        assert p.progress_item_interval == 200
        assert p.max_workers == 2          # low tier hardcodes 2, not cores

    def test_low_tier_zero_ram(self):
        """Zero RAM (psutil returns 0) must also fall into the low tier."""
        p = _probe(available_gb=0.0, cores=4)
        assert p.batch_size == 100

    def test_mid_tier_at_4gb(self):
        p = _probe(available_gb=4.0, cores=6)
        assert p.batch_size == 250
        assert p.archive_chunk_size == 250
        assert p.progress_item_interval == 100
        assert p.max_workers == 6

    def test_mid_tier_at_8gb(self):
        p = _probe(available_gb=8.0, cores=4)
        assert p.batch_size == 250

    def test_high_tier_at_12gb(self):
        p = _probe(available_gb=12.0, cores=8)
        assert p.batch_size == 500
        assert p.archive_chunk_size == 500
        assert p.progress_item_interval == 50
        assert p.max_workers == 8

    def test_high_tier_at_16gb(self):
        p = _probe(available_gb=16.0, cores=8)
        assert p.batch_size == 500

    def test_xhigh_tier_at_32gb(self):
        p = _probe(available_gb=32.0, cores=10)
        assert p.batch_size == 1000
        assert p.archive_chunk_size == 1000
        assert p.progress_item_interval == 25
        assert p.max_workers == 10

    def test_xhigh_tier_at_64gb(self):
        p = _probe(available_gb=64.0, cores=16)
        assert p.batch_size == 1000
        assert p.max_workers == 16


# ─── SSD adjustment ───────────────────────────────────────────────────────────

class TestSsdAdjustment:
    def test_ssd_tightens_min_seconds(self):
        hdd = _probe(available_gb=8.0, storage="hdd")
        ssd = _probe(available_gb=8.0, storage="ssd")
        # SSD should produce a tighter (smaller) minimum emit interval
        assert ssd.progress_min_seconds < hdd.progress_min_seconds

    def test_ssd_does_not_go_below_floor(self):
        # Even on the fastest tier with SSD, we never go below 0.05s
        ssd = _probe(available_gb=64.0, storage="ssd")
        assert ssd.progress_min_seconds >= 0.05

    def test_hdd_unchanged_from_tier_default(self):
        p = _probe(available_gb=8.0, storage="hdd")
        assert p.progress_min_seconds == 0.25   # mid-tier baseline

    def test_unknown_storage_unchanged(self):
        p = _probe(available_gb=8.0, storage="unknown")
        assert p.progress_min_seconds == 0.25


# ─── User overrides ───────────────────────────────────────────────────────────

class TestUserOverrides:
    def test_batch_size_override(self):
        p = _probe(available_gb=8.0, overrides={"batch_size": 42})
        assert p.batch_size == 42
        assert p.source == "override"

    def test_partial_override_leaves_others_at_detected(self):
        p = _probe(available_gb=8.0, cores=6, overrides={"max_workers": 12})
        assert p.max_workers == 12
        assert p.batch_size == 250      # mid-tier, not overridden
        assert p.source == "override"

    def test_all_overrides(self):
        overrides = {
            "batch_size": 333,
            "archive_chunk_size": 333,
            "progress_item_interval": 77,
            "progress_min_seconds": 0.42,
            "max_workers": 3,
        }
        p = _probe(available_gb=8.0, overrides=overrides)
        assert p.batch_size == 333
        assert p.archive_chunk_size == 333
        assert p.progress_item_interval == 77
        assert pytest.approx(p.progress_min_seconds, abs=0.001) == 0.42
        assert p.max_workers == 3
        assert p.source == "override"


# ─── psutil unavailable ───────────────────────────────────────────────────────

class TestPsutilUnavailable:
    def test_fallback_to_low_tier_when_psutil_missing(self):
        """If psutil is not importable, available RAM=0 → low tier."""
        saved = sys.modules.pop("psutil", None)
        try:
            # Temporarily make psutil unimportable
            sys.modules["psutil"] = None  # type: ignore[assignment]
            p = sp.detect_system_profile()
            # _available_ram_gb returns 0.0 → low tier
            assert p.batch_size == 100
            assert p.max_workers == 2
            assert p.source in ("detected", "override", "fallback")
        finally:
            if saved is not None:
                sys.modules["psutil"] = saved
            else:
                sys.modules.pop("psutil", None)


# ─── Profile metadata ─────────────────────────────────────────────────────────

class TestProfileMetadata:
    def test_source_is_detected_without_overrides(self):
        p = _probe(available_gb=8.0)
        assert p.source == "detected"

    def test_gpu_flag_propagates(self):
        with_gpu    = _probe(available_gb=8.0, gpu=True)
        without_gpu = _probe(available_gb=8.0, gpu=False)
        assert with_gpu.gpu_available is True
        assert without_gpu.gpu_available is False

    def test_ram_stored_in_profile(self):
        p = _probe(available_gb=16.3, cores=8)
        assert abs(p.ram_available_gb - 16.3) < 0.05

    def test_cpu_cores_stored_in_profile(self):
        p = _probe(available_gb=8.0, cores=12)
        assert p.cpu_cores == 12
