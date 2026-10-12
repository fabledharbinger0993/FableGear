"""The one switch between 'loopback only' and 'reachable on the network' must be strict."""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lan_policy import lan_ui_enabled


def _home(tmp_path, raw: str | None) -> Path:
    if raw is not None:
        (tmp_path / ".fablegear").mkdir()
        (tmp_path / ".fablegear" / "config.json").write_text(raw)
    return tmp_path


def test_only_the_json_boolean_true_opens_the_server(tmp_path):
    assert lan_ui_enabled(_home(tmp_path, json.dumps({"allow_lan_ui": True}))) is True


@pytest.mark.parametrize("raw", [
    None,                                   # no config file at all
    "",                                     # empty file
    "{not json",                            # invalid JSON
    "[]", "true", "null", "42",             # valid JSON that is not an object
    json.dumps({}),                         # key missing
    json.dumps({"allow_lan_ui": False}),
    json.dumps({"allow_lan_ui": "true"}),   # the strings and numbers the review warned about
    json.dumps({"allow_lan_ui": "false"}),
    json.dumps({"allow_lan_ui": "yes"}),
    json.dumps({"allow_lan_ui": 1}),
    json.dumps({"allow_lan_ui": None}),
    json.dumps({"allow_lan_ui": [True]}),
])
def test_everything_else_means_loopback_only(tmp_path, raw):
    assert lan_ui_enabled(_home(tmp_path, raw)) is False


def test_main_and_app_use_the_same_policy():
    assert "from lan_policy import lan_ui_enabled" in (REPO_ROOT / "main.py").read_text()
    assert "lan_ui_enabled()" in (REPO_ROOT / "app.py").read_text()
