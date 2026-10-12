"""The DeviceSQL reader against a REAL Rekordbox-written export that a CDJ-3000 (firmware 3.2x) loaded.

tests/fixtures/rekordbox_21track_export/ holds that stick's export.pdb, exportExt.pdb and exportLibrary.db (the
OneLibrary database, an independent record of the same 21 tracks). These tests are the reason the row-index decoder
is trusted: an earlier version read the 36-byte row groups backwards, recovered 3 of the 21 tracks, and passed every
test because its synthetic pages were built with the same mistake.
"""
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chop_shop.devicesql_reader import _row_slots, read_pdb

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rekordbox_21track_export" / "PIONEER" / "rekordbox"


@pytest.fixture(scope="module")
def pdb():
    return read_pdb(FIXTURE / "export.pdb")


@pytest.fixture(scope="module")
def onelibrary():
    """(tracks, keys) read out of the same stick's OneLibrary database with an independent connection."""
    import sqlcipher3

    from fablegear_database.onelibrary_writer import _CIPHER_COMPATIBILITY, _ONELIBRARY_KEY

    conn = sqlcipher3.connect(str(FIXTURE / "exportLibrary.db"))
    cur = conn.cursor()
    cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
    cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
    tracks = cur.execute(
        "SELECT c.path, c.title, c.bpmx100, k.name FROM content c LEFT JOIN key k ON k.key_id = c.key_id"
    ).fetchall()
    keys = dict(cur.execute("SELECT key_id, name FROM key").fetchall())
    conn.close()
    return tracks, keys


def test_every_row_of_every_table_is_found(pdb):
    assert pdb.valid_header is True and pdb.partial is False
    counts = pdb.table_row_counts
    assert counts["tracks"] == 21
    assert (counts["artists"], counts["albums"], counts["genres"], counts["labels"]) == (8, 1, 1, 1)
    assert (counts["keys"], counts["colors"], counts["playlist_entries"]) == (10, 8, 20)
    assert (counts["artwork"], counts["columns"], counts["history"]) == (6, 27, 1)
    assert len(pdb.tracks) == 21 and [t.track_id for t in pdb.tracks] == list(range(1, 22))


def test_the_pdb_agrees_with_the_onelibrary_database_on_every_track(pdb, onelibrary):
    one_tracks, _ = onelibrary
    expected = {path: (title, bpmx100, key) for path, title, bpmx100, key in one_tracks}
    got = {t.drive_relative_path: (t.title, round(t.bpm * 100), t.key) for t in pdb.tracks}
    assert len(expected) == 21 and got == expected


def test_key_table_matches_onelibrary_names_and_ids(pdb, onelibrary):
    _, one_keys = onelibrary
    assert pdb.keys == one_keys
    assert pdb.keys[1] == "Abm" and set(pdb.keys.values()) >= {"Abm", "Ebm", "F#m", "E", "A"}


def test_names_are_resolved_on_each_track(pdb):
    assert {t.artist for t in pdb.tracks} == {"Michael Jackson"}
    assert pdb.albums == {1: "Invincible"} and pdb.genres == {1: "R&B/Pop"} and pdb.labels == {1: "Epic"}
    assert pdb.artists[2] == "M. J. Jackson" and len(pdb.artists) == 8
    unbreakable = pdb.tracks[0]
    assert (unbreakable.title, unbreakable.bpm, unbreakable.key) == ("Unbreakable", 94.0, "Abm")


def test_ext_file_reads_without_borrowing_the_main_files_table_names():
    ext = read_pdb(FIXTURE / "exportExt.pdb")
    assert ext.valid_header is True and ext.num_tables == 9 and ext.tracks == []
    assert all(name.startswith("table_") for name in ext.table_row_counts)


def _group(offsets, present_bits, unknown=0x0004):
    """One 36-byte row group in the VERIFIED layout: 16 reversed u16 offsets, presence flags, unknown."""
    body = b"".join(struct.pack("<H", offsets.get(i, 0)) for i in range(15, -1, -1))
    return body + struct.pack("<HH", present_bits, unknown)


def test_row_slots_follow_the_verified_layout_across_two_groups_with_gaps():
    page_size = 4096
    buf = bytearray(page_size)
    g0 = {i: 0x10 * (i + 1) for i in range(16)}                     # rows 0-15
    g1 = {0: 0x400, 1: 0x410}                                        # rows 16-17
    buf[page_size - 36:page_size] = _group(g0, 0b1111_1111_1111_1011)  # row 2 absent (a deleted row)
    buf[page_size - 72:page_size - 36] = _group(g1, 0b01)            # row 17 absent
    slots = _row_slots(bytes(buf), page_size, 18)
    assert len(slots) == 18
    assert [s[0] for s in slots[:16]] == [0x10 * (i + 1) for i in range(16)]  # offsets land on the right rows
    assert [i for i, s in enumerate(slots) if not s[1]] == [2, 17]            # only the cleared bits are absent
    assert slots[16] == (0x400, True) and slots[17] == (0x410, False)
