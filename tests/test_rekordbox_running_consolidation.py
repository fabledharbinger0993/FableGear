"""
Regression tests for consolidating the three separate "is Rekordbox
running?" implementations (db_connection.py, cli.py, rekordbox_safe_write.py)
into one canonical function, db_connection.rekordbox_is_running().

Also covers the fail-safe behavior fix: this check gates every write to a
shared, irreplaceable Rekordbox library, so when the check itself can't run
(pgrep/tasklist missing), it must fail closed (assume Rekordbox might be
running) rather than fail open (assume it's safe to write).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import db_connection

# Captured at collection time, before tests/conftest.py's autouse
# _rekordbox_not_running fixture monkeypatches db_connection.rekordbox_is_running
# to an unconditional `False` for the whole suite. This test specifically wants
# to exercise the real implementation's error-handling branch, so it calls this
# direct reference rather than db_connection.rekordbox_is_running (which would
# just hit the autouse fixture's fake).
_real_rekordbox_is_running = db_connection.rekordbox_is_running


def test_fails_closed_when_process_check_tool_is_missing(monkeypatch):
    def fake_run(*a, **k):
        raise FileNotFoundError("pgrep not found")

    monkeypatch.setattr(db_connection.subprocess, "run", fake_run)
    assert _real_rekordbox_is_running() is True


def test_cli_delegates_to_db_connection_canonical_check(monkeypatch):
    import cli

    monkeypatch.setattr(db_connection, "rekordbox_is_running", lambda: True)
    assert cli._rekordbox_running() is True

    monkeypatch.setattr(db_connection, "rekordbox_is_running", lambda: False)
    assert cli._rekordbox_running() is False


def test_rekordbox_safe_write_delegates_to_db_connection_canonical_check(monkeypatch):
    import rekordbox_safe_write as rsw

    monkeypatch.setattr(db_connection, "rekordbox_is_running", lambda: True)
    assert rsw.rekordbox_running() is True

    monkeypatch.setattr(db_connection, "rekordbox_is_running", lambda: False)
    assert rsw.rekordbox_running() is False
