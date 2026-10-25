"""The Record Room's USB export writes a CDJ-ready stick onto a blank drive (or a stick FableGear wrote).

device_export assembles the selected playlists in a throwaway database -- FableGear's own analysis
(Iron's BPM/key, grids) wins over the playlist source's -- and runs the same writers as
`export-onelibrary`. These tests use fake Rekordbox objects, a temporary FableGear database and a
temporary "drive"; nothing touches the user's library.
"""
import math
import sqlite3
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[2] / "fablegear"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import device_export
import helpers
from chop_shop import devicesql_reader as R
from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.schema import DatabaseConfig


def _tone(path: Path, bpm: float, seconds: float = 3.0, sr: int = 22050) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        beat = 60.0 / bpm
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.exp(-((i / sr) % beat) * 30) * math.sin(2 * math.pi * 60 * i / sr)))
            for i in range(int(seconds * sr))))
    return path


# ─── Layout detection ───────────────────────────────────────────────────────

def test_blank_drive_is_a_cdj_ready_target(tmp_path):
    info = helpers._detect_pioneer_drive_layout(tmp_path)
    assert info["layout"] == "blank" and info["export_supported"] is True and info["pioneer"] is False
    assert "export.pdb" in info["export_note"]


def test_fablegear_stick_is_replaceable(tmp_path):
    rb = tmp_path / "PIONEER" / "rekordbox"
    rb.mkdir(parents=True)
    (rb / "exportLibrary.db").touch()
    (rb / device_export.MARKER_NAME).write_text("x")
    info = helpers._detect_pioneer_drive_layout(tmp_path)
    assert info["layout"] == "fablegear-usb" and info["export_supported"] is True


def test_rekordbox_stick_and_unknown_pioneer_folder_are_refused(tmp_path):
    rb = tmp_path / "rbstick" / "PIONEER" / "rekordbox"
    rb.mkdir(parents=True)
    (rb / "export.pdb").touch()
    info = helpers._detect_pioneer_drive_layout(tmp_path / "rbstick")
    assert info["export_supported"] is False and "blank drive" in info["export_error"]
    (tmp_path / "odd" / "PIONEER").mkdir(parents=True)
    assert helpers._detect_pioneer_drive_layout(tmp_path / "odd")["export_supported"] is False


# ─── Rekordbox sources ──────────────────────────────────────────────────────

class _Query:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def order_by(self, *_a):
        return _Query(sorted(self._items, key=lambda s: s.TrackNo))


class _FakeRekordbox:
    def __init__(self, playlists, songs):
        self._playlists, self._songs = playlists, songs

    def get_playlist(self):
        return _Query(self._playlists)

    def get_playlist_songs(self, PlaylistID):
        return _Query(self._songs.get(PlaylistID, []))


def _rb_content(path: Path, title: str, bpm100: int, key: str):
    return SimpleNamespace(
        FolderPath=str(path), Title=title, Artist=SimpleNamespace(Name="RB Artist"),
        Album=SimpleNamespace(Name="RB Album"), Genre=SimpleNamespace(Name="Disco"), Label=None,
        Key=SimpleNamespace(ScaleName=key), BPM=bpm100, Length=3, ReleaseYear=1979, TrackNo=1, DiscNo=1,
        Commnt="", Rating=0, ColorID=7, BitRate=1411, SampleRate=22050)


@pytest.fixture
def rekordbox(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    a = _tone(music / "a.wav", 120.0)
    b = _tone(music / "b.wav", 98.0)
    folder = SimpleNamespace(ID=10, Name="Gigs", ParentID="root", Attribute=1)
    pl = SimpleNamespace(ID=11, Name="Friday", ParentID=10, Attribute=0)
    songs = {11: [SimpleNamespace(TrackNo=2, Content=_rb_content(b, "Bee", 9800, "Gm")),
                  SimpleNamespace(TrackNo=1, Content=_rb_content(a, "Ay", 12000, "Am")),
                  SimpleNamespace(TrackNo=3, Content=_rb_content(music / "gone.wav", "Gone", 12000, "C"))]}
    return _FakeRekordbox([folder, pl], songs), a, b


def test_sources_keep_order_folders_and_report_missing_files(rekordbox):
    src, a, b = rekordbox
    tracks, playlists, errors = helpers._rekordbox_export_sources(src, ["11"])
    assert [t.title for t in tracks] == ["Ay", "Bee"]
    assert tracks[0].bpm == 120.0 and tracks[0].key == "Am" and tracks[0].color == "#0000ff"
    assert [(p.key, p.name, p.is_folder, p.parent_key) for p in playlists] == \
           [("10", "Gigs", True, None), ("11", "Friday", False, "10")]
    assert playlists[1].track_paths == [str(a), str(b)]
    assert any("gone.wav" in e for e in errors)


# ─── The stick ──────────────────────────────────────────────────────────────

@pytest.fixture
def fablegear_db(tmp_path, rekordbox):
    """The user's FableGear library: knows track A, with Iron's answer (different from Rekordbox's)."""
    _src, a, _b = rekordbox
    db = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))
    db.insert_content(ContentRecord(file_path=str(a), file_name=a.name, file_size=a.stat().st_size,
                                    title="Ay", bpm=121.5, key="9A", duration=3.0, format="wav"))
    return db


def _rows(db_path: Path) -> list:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT * FROM fg_content ORDER BY id").fetchall()


def test_stick_uses_fablegear_analysis_and_leaves_the_library_alone(tmp_path, rekordbox, fablegear_db):
    src, _a, _b = rekordbox
    tracks, playlists, _ = helpers._rekordbox_export_sources(src, ["11"])
    before = _rows(tmp_path / "fablegear.db")
    drive = tmp_path / "USB"
    drive.mkdir()
    res = device_export.export_playlists_to_drive(drive, tracks, playlists, fablegear_db=fablegear_db)
    assert res.tracks_written == 2 and res.anlz_written == 2 and res.tracks_from_fablegear == 1
    assert _rows(tmp_path / "fablegear.db") == before

    pdb = R.read_pdb(drive / "PIONEER" / "rekordbox" / "export.pdb")
    got = {t.title: t for t in pdb.tracks}
    assert (got["Ay"].bpm, got["Ay"].key) == (121.5, "Em")  # FableGear's Iron answer, not Rekordbox's
    assert (got["Bee"].bpm, got["Bee"].key) == (98.0, "Gm")  # unknown to FableGear: Rekordbox's values
    for t in pdb.tracks:
        assert (drive / (t.drive_relative_path or "").lstrip("/")).is_file()
        assert (drive / (t.anlz_folder_path or "").lstrip("/")).is_file()
    pl = R.read_playlists(drive / "PIONEER" / "rekordbox" / "export.pdb")
    assert [(p.folder_path, [r.title for r in p.tracks]) for p in pl.playlists] == [("Gigs/Friday", ["Ay", "Bee"])]
    assert device_export.is_fablegear_export(drive)
    assert (drive / "PIONEER" / "rekordbox" / "exportLibrary.db").is_file()


def test_reexport_replaces_the_library_and_keeps_audio(tmp_path, rekordbox):
    src, a, _b = rekordbox
    tracks, playlists, _ = helpers._rekordbox_export_sources(src, ["11"])
    drive = tmp_path / "USB"
    drive.mkdir()
    device_export.export_playlists_to_drive(drive, tracks, playlists)
    stale = drive / "PIONEER" / "USBANLZ" / "P999" / "STALE"
    stale.mkdir(parents=True)
    audio = next((drive / "Contents").rglob("a.wav"))
    stamp = audio.stat().st_mtime_ns
    only_a = device_export.SourcePlaylist(key="11", name="Friday", parent_key="10", track_paths=[str(a)])
    res = device_export.export_playlists_to_drive(drive, tracks[:1], [playlists[0], only_a])
    assert res.tracks_written == 1 and not stale.exists()
    assert audio.stat().st_mtime_ns == stamp  # same size: not copied again
    assert [t.title for t in R.read_pdb(drive / "PIONEER" / "rekordbox" / "export.pdb").tracks] == ["Ay"]


def test_a_failed_export_can_be_retried(tmp_path, rekordbox, monkeypatch):
    from chop_shop import devicesql_writer as W

    src, *_ = rekordbox
    tracks, playlists, _ = helpers._rekordbox_export_sources(src, ["11"])
    drive = tmp_path / "USB"
    drive.mkdir()
    real = W.write_device_library
    monkeypatch.setattr(W, "write_device_library", lambda *a, **k: (_ for _ in ()).throw(OSError("unplugged")))
    with pytest.raises(OSError):
        device_export.export_playlists_to_drive(drive, tracks, playlists)
    assert device_export.is_fablegear_export(drive)  # the half-written library is still recognisably ours
    assert helpers._detect_pioneer_drive_layout(drive)["export_supported"] is True
    monkeypatch.setattr(W, "write_device_library", real)
    res = device_export.export_playlists_to_drive(drive, tracks, playlists)
    assert res.tracks_written == 2 and len(R.read_pdb(drive / "PIONEER" / "rekordbox" / "export.pdb").tracks) == 2


def test_a_drive_holding_another_library_is_never_written(tmp_path, rekordbox):
    src, *_ = rekordbox
    tracks, playlists, _ = helpers._rekordbox_export_sources(src, ["11"])
    drive = tmp_path / "USB"
    (drive / "PIONEER" / "rekordbox").mkdir(parents=True)
    (drive / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"rekordbox's")
    with pytest.raises(FileExistsError):
        device_export.export_playlists_to_drive(drive, tracks, playlists)
    assert (drive / "PIONEER" / "rekordbox" / "export.pdb").read_bytes() == b"rekordbox's"
    assert not (drive / "Contents").exists()


def test_background_job_reports_completion(tmp_path, rekordbox, monkeypatch):
    import contextlib

    import config
    import db_connection

    src, *_ = rekordbox
    monkeypatch.setattr(db_connection, "read_db", lambda *_a, **_k: contextlib.nullcontext(src))
    monkeypatch.setattr(config, "LOCAL_DB", tmp_path / "unused.db", raising=False)
    monkeypatch.setattr("fablegear_database.FableGearDatabase",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no library")))
    drive = tmp_path / "USB"
    drive.mkdir()
    job = "job-1"
    helpers._EXPORT_JOBS[job] = {"job_id": job, "status": "running", "errors": []}
    try:
        helpers._run_export(job, ["11"], str(drive))
        state = helpers._EXPORT_JOBS[job]
        assert state["status"] == "complete_with_errors"  # gone.wav is reported, the rest exported
        assert state["tracks_done"] == 2 and state["anlz_written"] == 2
        assert any("gone.wav" in e for e in state["errors"])
        assert len(R.read_pdb(drive / "PIONEER" / "rekordbox" / "export.pdb").tracks) == 2
    finally:
        helpers._EXPORT_JOBS.pop(job, None)
