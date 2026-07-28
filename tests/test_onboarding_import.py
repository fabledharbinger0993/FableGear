"""
Tests for the Stage 4 onboarding endpoints: import-sources validation and
setup-state persistence of the MCP opt-in.

Run from the repo root:
    python3 -m pytest tests/test_onboarding_import.py -v

The happy-path import itself is covered by tests/test_fablegear_importer.py
(FileImporter against a temp database). These tests pin the HTTP contract:
bad input must be rejected BEFORE any thread starts or any database is
touched, and the mcp_opted_in flag must survive state normalization.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app as appmod


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_import_sources_requires_paths():
    r = _client().post("/api/onboarding/import-sources", json={})
    assert r.status_code == 400
    assert "paths" in r.get_json()["error"]


def test_import_sources_rejects_non_directories():
    r = _client().post(
        "/api/onboarding/import-sources",
        json={"paths": ["/definitely/not/a/real/dir"]},
    )
    assert r.status_code == 400
    assert "not a directory" in r.get_json()["error"]


def test_import_sources_rejects_blank_paths():
    r = _client().post("/api/onboarding/import-sources", json={"paths": ["", "  "]})
    assert r.status_code == 400


def test_import_status_shape():
    r = _client().get("/api/onboarding/import-sources/status")
    assert r.status_code == 200
    body = r.get_json()
    for key in ("running", "phase", "done", "total", "result", "error"):
        assert key in body


def test_setup_state_normalizes_mcp_opted_in():
    assert appmod._normalize_setup_state({"mcp_opted_in": True})["mcp_opted_in"] is True
    assert appmod._normalize_setup_state({})["mcp_opted_in"] is False
    assert appmod._normalize_setup_state(None)["mcp_opted_in"] is False
    # Non-bool truthy input coerces rather than crashes
    assert appmod._normalize_setup_state({"mcp_opted_in": "yes"})["mcp_opted_in"] is True
