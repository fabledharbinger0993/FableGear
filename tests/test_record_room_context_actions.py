"""Record Room right-click actions: track/playlist routes and DB helpers."""
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
        file_path=str(path), file_name=path.name, file_size=5,
        title=kw.pop("title", name), artist=kw.pop("artist", "A"), **kw))


def test_remove_track_clears_playlist_rows_not_file(client, fg):
    t = _track(fg, "one")
    pl = fg.create_playlist("P")
    fg.add_song(pl, t)
    r = client.delete(f"/api/library/tracks/{t}")
    assert r.status_code == 200
    assert fg.get_content_by_id(t) is None
    assert fg.get_playlist_songs(pl) == []
    assert client.delete(f"/api/library/tracks/{t}").status_code == 404


def test_rekordbox_source_is_refused(client, fg):
    t = _track(fg, "one")
    assert client.delete(f"/api/library/tracks/{t}?db=local").status_code == 400
    assert fg.get_content_by_id(t) is not None


def test_duplicate_track_and_find_duplicates(client, fg):
    t = _track(fg, "one", title="Same", artist="X")
    r = client.post(f"/api/library/tracks/{t}/duplicate")
    assert r.status_code == 201
    new_id = int(r.get_json()["id"])
    dup = fg.get_content_by_id(new_id)
    assert dup.title == "Same (copy)" and dup.file_path.endswith("one (copy).mp3")
    assert Path(dup.file_path).read_bytes() == b"audio"
    _track(fg, "two", title="same ", artist="x")
    ids = {d["id"] for d in client.get(f"/api/library/tracks/{t}/duplicates").get_json()["duplicates"]}
    assert len(ids) == 1 and str(new_id) not in ids


def test_duplicate_playlist_copies_tracks(client, fg):
    t = _track(fg, "one")
    pl = fg.create_playlist("Mix")
    fg.add_song(pl, t)
    r = client.post(f"/api/library/playlists/{pl}/duplicate")
    assert r.status_code == 201
    new_id = int(r.get_json()["id"])
    assert fg.get_playlist(new_id)["name"] == "Mix (copy)"
    assert [x.id for x in fg.get_playlist_songs(new_id)] == [t]
    assert fg.get_playlist(pl) is not None


def test_move_playlist_rules(client, fg):
    folder = fg.create_playlist("F", playlist_type="folder")
    sub = fg.create_playlist("Sub", parent_id=folder, playlist_type="folder")
    pl = fg.create_playlist("P")
    assert client.post(f"/api/library/playlists/{pl}/move", json={"parent_id": folder}).status_code == 200
    assert fg.get_playlist(pl)["parent_id"] == folder
    assert client.post(f"/api/library/playlists/{pl}/move", json={"parent_id": None}).status_code == 200
    assert fg.get_playlist(pl)["parent_id"] is None
    assert client.post(f"/api/library/playlists/{folder}/move", json={"parent_id": sub}).status_code == 400
    assert client.post(f"/api/library/playlists/{folder}/move", json={"parent_id": pl}).status_code == 400


def test_export_playlist_xml(client, fg):
    t = _track(fg, "one", title="A & B")
    pl = fg.create_playlist("My <Mix>")
    fg.add_song(pl, t)
    r = client.get(f"/api/library/playlists/{pl}/export.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.data)
    assert root.find("COLLECTION/TRACK").get("Name") == "A & B"
    assert root.find(".//NODE[@Type='1']").get("Name") == "My <Mix>"
    assert "filename=" in r.headers["Content-Disposition"]


def test_eject_rejects_unmounted_path(client, monkeypatch):
    monkeypatch.setattr(routes_player, "get_connected_volumes", lambda: [{"name": "X", "path": "/Volumes/X"}])
    assert client.post("/api/drives/eject", json={"path": "/etc"}).status_code == 403
    assert client.post("/api/drives/eject", json={}).status_code == 400
