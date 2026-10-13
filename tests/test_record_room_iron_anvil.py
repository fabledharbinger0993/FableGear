"""Record Room Iron (analyse) and Anvil (write tags) actions on FableGear's own database."""
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audio_processor
import routes_player
from fablegear_database.database import ContentRecord, DatabaseConfig, FableGearDatabase


@pytest.fixture
def fg(tmp_path):
    return FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fg.db"))


@pytest.fixture
def client(fg, monkeypatch):
    monkeypatch.setattr(routes_player, "_fablegear_db", lambda create=False: fg)
    app = Flask(__name__)
    app.register_blueprint(routes_player.bp)
    return app.test_client()


def _track(fg, name, **kw):
    path = fg.config.db_path.parent / f"{name}.mp3"
    path.write_bytes(b"audio")
    return fg.insert_content(ContentRecord(
        file_path=str(path), file_name=path.name, file_size=5, title=name, **kw))


def test_analyze_persists_bpm_and_key(client, fg, monkeypatch):
    t = _track(fg, "one")
    monkeypatch.setattr(audio_processor, "_analyze_iron",
                        lambda path, *, bpm, key: ((124.0, 0.9), "8A"))
    r = client.post(f"/api/library/tracks/{t}/analyze")
    assert r.status_code == 200
    body = r.get_json()
    assert body["bpm"] == 124.0 and body["key"] == "8A"
    rec = fg.get_content_by_id(t)
    assert rec.bpm == pytest.approx(124.0)
    assert rec.key == "8A"


def test_analyze_partial_result_keeps_existing_value(client, fg, monkeypatch):
    t = _track(fg, "two", bpm=128.0, key="5B")
    monkeypatch.setattr(audio_processor, "_analyze_iron",
                        lambda path, *, bpm, key: (None, "9A"))
    assert client.post(f"/api/library/tracks/{t}/analyze").status_code == 200
    rec = fg.get_content_by_id(t)
    assert rec.bpm == pytest.approx(128.0)
    assert rec.key == "9A"


def test_analyze_nothing_found_is_422_and_changes_nothing(client, fg, monkeypatch):
    t = _track(fg, "three", bpm=100.0)
    monkeypatch.setattr(audio_processor, "_analyze_iron",
                        lambda path, *, bpm, key: (None, None))
    assert client.post(f"/api/library/tracks/{t}/analyze").status_code == 422
    assert fg.get_content_by_id(t).bpm == pytest.approx(100.0)


def test_analyze_missing_file_is_409(client, fg):
    t = _track(fg, "four")
    Path(fg.get_content_by_id(t).file_path).unlink()
    assert client.post(f"/api/library/tracks/{t}/analyze").status_code == 409


def test_rekordbox_source_is_refused(client, fg):
    t = _track(fg, "five")
    assert client.post(f"/api/library/tracks/{t}/analyze?db=local").status_code == 400
    assert client.post(f"/api/library/tracks/{t}/tags?db=local").status_code == 400


def test_write_tags_uses_database_values(client, fg, monkeypatch):
    t = _track(fg, "six", bpm=174.0, key="11A")
    seen = {}

    def fake_write(path, *, bpm, key):
        seen.update(path=path, bpm=bpm, key=key)
        return "anvil"

    monkeypatch.setattr(audio_processor, "_write_tags", fake_write)
    r = client.post(f"/api/library/tracks/{t}/tags")
    assert r.status_code == 200
    assert r.get_json()["backend"] == "anvil"
    assert seen["bpm"] == pytest.approx(174.0)
    assert seen["key"] == "11A"
    assert seen["path"] == Path(fg.get_content_by_id(t).file_path)


def test_write_tags_refuses_track_with_no_analysis(client, fg, monkeypatch):
    t = _track(fg, "seven")
    called = []
    monkeypatch.setattr(audio_processor, "_write_tags", lambda *a, **k: called.append(1))
    assert client.post(f"/api/library/tracks/{t}/tags").status_code == 409
    assert called == []


def test_write_tags_failure_is_500_and_logged(client, fg, monkeypatch):
    t = _track(fg, "eight", bpm=90.0)

    def boom(path, *, bpm, key):
        raise OSError("disk full")

    monkeypatch.setattr(audio_processor, "_write_tags", boom)
    r = client.post(f"/api/library/tracks/{t}/tags")
    assert r.status_code == 500
    assert "disk full" in r.get_json()["error"]
    assert fg.count_operations("write_tags") == 1
