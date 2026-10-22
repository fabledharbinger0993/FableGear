"""`cli.py export-onelibrary` writes a whole Pioneer stick: OneLibrary, the DeviceSQL Device Library
(export.pdb + exportExt.pdb, which CDJ-3000 firmware 3.2x reads instead of OneLibrary), the staged
audio and the USBANLZ analysis files -- all keyed to the same track ids and paths.

Runs the real command against a temporary FableGear database and a temporary "stick"; nothing
touches the user's library, Archive or Rekordbox.
"""
import argparse
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "fablegear"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import cli
import fablegear_database
from chop_shop import devicesql_reader as R
from fablegear_database.database import ContentRecord, FableGearDatabase
from fablegear_database.onelibrary_writer import _CIPHER_COMPATIBILITY, _ONELIBRARY_KEY
from fablegear_database.schema import DatabaseConfig


def _tone(path: Path, bpm: float, seconds: float = 4.0, sr: int = 22050) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        beat = 60.0 / bpm
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.exp(-((i / sr) % beat) * 30) * math.sin(2 * math.pi * 60 * i / sr)))
            for i in range(int(seconds * sr))
        ))


@pytest.fixture
def library(tmp_path, monkeypatch):
    fg = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "fablegear.db"))
    music = tmp_path / "music"
    music.mkdir()
    ids = []
    for i, (title, key, bpm) in enumerate([("Kick One", "8A", 120.0), ("Beyoncé Edit", "11B", 124.5)], 1):
        p = music / f"{i:02d} {title}.wav"
        _tone(p, bpm)
        ids.append(fg.insert_content(ContentRecord(
            file_path=str(p), file_name=p.name, file_size=p.stat().st_size, title=title, artist="Test Artist",
            album="Test Album", bpm=bpm, key=key, genre="House", duration=4.0, format="wav",
            bit_rate=352800, sample_rate=22050, color="#ff0000", created_at="2026-10-01T10:00:00")))
    folder = fg.create_playlist("Gigs", playlist_type="folder")
    pl = fg.create_playlist("Friday", parent_id=folder)
    for cid in ids:
        fg.add_song(pl, cid)
    monkeypatch.setattr(fablegear_database, "FableGearDatabase", lambda *a, **k: fg)
    return fg


def _args(target: Path, **kw) -> argparse.Namespace:
    base = dict(target=str(target), stage_audio=True, with_anlz=False, force=False, no_playlists=False,
                device_name="", dj_name="", no_identity_files=False, no_device_library=False, playlist=None,
                playlist_id=None, content_ids=None, require_parsed=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _onelibrary(path: Path) -> list[tuple]:
    import sqlcipher3

    conn = sqlcipher3.connect(str(path))
    cur = conn.cursor()
    cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
    cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
    rows = cur.execute(
        "SELECT c.content_id, c.title, c.bpmx100, k.name, c.path, c.analysisDataFilePath, c.bitrate "
        "FROM content c LEFT JOIN key k ON k.key_id = c.key_id ORDER BY c.content_id").fetchall()
    conn.close()
    return rows


def test_stick_carries_both_libraries_audio_and_anlz_that_agree(library, tmp_path):
    usb = tmp_path / "USB"
    target = usb / "PIONEER" / "rekordbox" / "exportLibrary.db"
    cli.cmd_export_onelibrary(_args(target, with_anlz=True))

    pdb = R.read_pdb(usb / "PIONEER" / "rekordbox" / "export.pdb")
    assert pdb.valid_header and not pdb.partial
    assert R.read_pdb(usb / "PIONEER" / "rekordbox" / "exportExt.pdb").table_row_counts["table_3"] == 28
    tracks = sorted(pdb.tracks, key=lambda t: t.track_id or 0)
    assert [(t.title, t.bpm, t.key, t.artist) for t in tracks] == [
        ("Kick One", 120.0, "Am", "Test Artist"), ("Beyoncé Edit", 124.5, "A", "Test Artist")]

    # Both databases describe the same tracks, ids, audio paths and ANLZ paths.
    one = _onelibrary(target)
    assert [(r[0], r[1], r[2] / 100, r[3], r[4], r[5]) for r in one] == \
           [(t.track_id, t.title, t.bpm, t.key, t.drive_relative_path, t.anlz_folder_path) for t in tracks]
    assert [r[6] for r in one] == [353, 353]  # kbps, as Rekordbox stores it (FableGear holds bits/s)

    from pyrekordbox.anlz import AnlzFile

    for t in tracks:
        assert (usb / (t.drive_relative_path or "").lstrip("/")).is_file()
        dat = usb / (t.anlz_folder_path or "").lstrip("/")
        assert dat.is_file() and dat.with_suffix(".EXT").is_file()
        anlz = AnlzFile.parse_file(str(dat))
        assert anlz.get("PPTH") == t.drive_relative_path
        assert set(anlz.get("beat_grid")[1]) == {t.bpm}

    pl = R.read_playlists(usb / "PIONEER" / "rekordbox" / "export.pdb")
    assert [(p.folder_path, [r.title for r in p.tracks]) for p in pl.playlists] == \
           [("Gigs/Friday", ["Kick One", "Beyoncé Edit"])]


def _tag_sequence(path: Path) -> list[str]:
    """Tags in file order; cue lists marked h (hot) / m (memory)."""
    data, out = path.read_bytes(), []
    i = int.from_bytes(data[4:8], "big")
    while i < len(data):
        fourcc, lt = data[i:i + 4].decode(), int.from_bytes(data[i + 8:i + 12], "big")
        out.append(fourcc + ("h" if int.from_bytes(data[i + 12:i + 16], "big") == 1 else "m")
                   if fourcc in ("PCOB", "PCO2") else fourcc)
        i += lt
    return out


# Rekordbox's sequence in all 412 tracks of the untouched export (PVBR, PQT2, PSSI not written by FableGear).
_RB_DAT = ["PPTH", "PVBR", "PQTZ", "PWAV", "PWV2", "PCOBh", "PCOBm"]
_RB_EXT = ["PPTH", "PWV3", "PCOBh", "PCOBm", "PCO2h", "PCO2m", "PQT2", "PWV5", "PWV4", "PSSI"]


def test_anlz_tags_follow_rekordbox_order_with_and_without_cues(library, tmp_path):
    from fablegear_database.database import CueRecord

    first = library.get_content_with_relations(None)[0]
    library.bulk_upsert_cues(first.id, [CueRecord(kind=1, slot=0, in_msec=500), CueRecord(kind=0, in_msec=250)])
    usb = tmp_path / "USB"
    cli.cmd_export_onelibrary(_args(usb / "PIONEER" / "rekordbox" / "exportLibrary.db", with_anlz=True))
    for d in sorted((usb / "PIONEER" / "USBANLZ").glob("*/*")):
        dat, ext = _tag_sequence(d / "ANLZ0000.DAT"), _tag_sequence(d / "ANLZ0000.EXT")
        assert dat == [t for t in _RB_DAT if t in dat] and dat[-2:] == ["PCOBh", "PCOBm"] and "PQTZ" in dat
        assert ext == [t for t in _RB_EXT if t in ext]
        assert [t for t in ext if t[:3] == "PCO"] == ["PCOBh", "PCOBm", "PCO2h", "PCO2m"]


def test_staged_file_names_are_safe_for_fat32(tmp_path):
    from fablegear_database.onelibrary_writer import _stage_audio_file

    src = tmp_path / 'What? "Edit" 1*2.wav'
    src.write_bytes(b"RIFF" + bytes(40))
    rel = _stage_audio_file(ContentRecord(file_path=str(src), artist="AC/DC", album="Live: 1991"), tmp_path / "USB")
    assert rel == "/Contents/AC_DC/Live_ 1991/What_ _Edit_ 1_2.wav"
    assert (tmp_path / "USB" / rel.lstrip("/")).read_bytes() == src.read_bytes()


def test_existing_device_library_is_refused_before_anything_is_written(library, tmp_path):
    rb = tmp_path / "USB" / "PIONEER" / "rekordbox"
    rb.mkdir(parents=True)
    (rb / "export.pdb").write_bytes(b"someone else's library")
    with pytest.raises(SystemExit):
        cli.cmd_export_onelibrary(_args(rb / "exportLibrary.db"))
    assert (rb / "export.pdb").read_bytes() == b"someone else's library"
    assert not (rb / "exportLibrary.db").exists()

    cli.cmd_export_onelibrary(_args(rb / "exportLibrary.db", force=True))
    assert len(R.read_pdb(rb / "export.pdb").tracks) == 2


def test_no_device_library_flag_skips_export_pdb(library, tmp_path):
    rb = tmp_path / "USB" / "PIONEER" / "rekordbox"
    cli.cmd_export_onelibrary(_args(rb / "exportLibrary.db", no_device_library=True))
    assert (rb / "exportLibrary.db").is_file()
    assert not (rb / "export.pdb").exists() and not (rb / "exportExt.pdb").exists()


def test_device_library_failure_is_loud_and_keeps_the_rest(library, tmp_path, monkeypatch):
    from chop_shop import devicesql_writer as W

    def boom(*a, **k):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(W, "write_device_library", boom)
    rb = tmp_path / "USB" / "PIONEER" / "rekordbox"
    with pytest.raises(SystemExit) as exc:
        cli.cmd_export_onelibrary(_args(rb / "exportLibrary.db"))
    assert exc.value.code == 1
    assert (rb / "exportLibrary.db").is_file() and not (rb / "export.pdb").exists()
