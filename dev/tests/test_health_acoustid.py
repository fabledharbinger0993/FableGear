from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import health_acoustid as ha


def test_find_fpcalc_honors_env_override():
    def mock_which(command: str) -> str | None:
        if command == "/custom/bin/fpcalc":
            return command
        return None

    with (
        patch.dict(os.environ, {"FPCALC": "/custom/bin/fpcalc"}, clear=False),
        patch("health_acoustid.shutil.which", side_effect=mock_which),
    ):
        assert ha._find_fpcalc() == "/custom/bin/fpcalc"


def test_acoustid_key_configured_false_for_none():
    with patch.dict(sys.modules, {"config": SimpleNamespace(ACOUSTID_API_KEY=None)}):
        assert ha._acoustid_key_configured() is False


def test_collect_health_reflects_missing_prerequisites():
    with (
        patch("health_acoustid._find_fpcalc", return_value=None),
        patch("health_acoustid._acoustid_key_configured", return_value=False),
        patch("health_acoustid._acoustid_module_available", return_value=False),
        patch("health_acoustid._fpcalc_available", return_value=False),
    ):
        health = ha.collect_health()
    assert health["ok"] is False
    assert health["key_ok"] is False
    assert health["module_ok"] is False
    assert health["fpcalc_ok"] is False
    assert health["fpcalc_path"] == ""


def test_full_health_check_raises_with_fail_reasons():
    with (
        patch("health_acoustid._find_fpcalc", return_value=None),
        patch("health_acoustid._acoustid_key_configured", return_value=False),
        patch("health_acoustid._acoustid_module_available", return_value=False),
        patch("health_acoustid._fpcalc_available", return_value=False),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            ha.full_health_check(raise_on_fail=True)
    message = str(excinfo.value)
    assert "AcoustID API key is not configured" in message
    assert "pyacoustid is not installed/importable" in message
    assert "fpcalc is not available" in message
