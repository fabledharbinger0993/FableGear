import pytest
import pyrekordbox.db6.database
import pyrekordbox.utils

@pytest.fixture(autouse=True)
def mock_rekordbox_not_running(monkeypatch):
    monkeypatch.setattr(pyrekordbox.db6.database, "get_rekordbox_pid", lambda: None)
    monkeypatch.setattr(pyrekordbox.utils, "get_rekordbox_pid", lambda: None)
