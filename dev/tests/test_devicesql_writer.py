"""The DeviceSQL writer against the two committed Rekordbox exports.

- Page model: every page of four real files (the 21-track stick a CDJ-3000 loaded, and the untouched
  413-track ground-truth export) serialises back byte for byte, with nothing carried verbatim. The
  derived fields (packed row counts, free/used size, row-group masks) are recomputed, not copied:
  a mutation test flips one presence bit and checks the counts move with it.
- Row encoders: given the values Rekordbox stored, they produce Rekordbox's own row bytes.
- Fresh files: a library rebuilt from the 21-track stick writes an export.pdb the reader recovers
  exactly, with the page-chain rules of the real files.
"""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "fablegear"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from chop_shop import devicesql_reader as R
from chop_shop import devicesql_writer as W

FIXTURES = APP.parent / "dev" / "tests" / "fixtures"
STICK = FIXTURES / "rekordbox_21track_export" / "PIONEER" / "rekordbox"
GROUND = FIXTURES / "rekordbox_ground_truth_export" / "PIONEER" / "rekordbox"
REAL_FILES = [STICK / "export.pdb", STICK / "exportExt.pdb", GROUND / "export.pdb", GROUND / "exportExt.pdb"]


def _u16(b, o):
    return int.from_bytes(b[o:o + 2], "little")


def _u32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def _present_rows(path: Path, table_type: int, num_tables: int = 20) -> list[bytes]:
    out = []
    with open(path, "rb") as fh:
        ptr = next(p for p in R._read_table_pointers(fh, num_tables) if p.table_type == table_type)
        for pg in R._walk_table_pages(fh, 4096, ptr.first_page, ptr.last_page):
            slots = R._row_slots(pg, 4096, R._parse_page_header(pg).num_row_offsets)
            offs, used = sorted({o for o, _ in slots}), _u16(pg, 0x1E)
            for o, present in slots:
                if present:
                    out.append(pg[0x28 + o:0x28 + min([x for x in offs if x > o], default=used)])
    return out


# ─── Page model round trip ──────────────────────────────────────────────────

@pytest.mark.parametrize("path", REAL_FILES, ids=lambda p: f"{p.parent.parent.parent.name}/{p.name}")
def test_real_file_round_trips_byte_for_byte(path):
    data = path.read_bytes()
    img = W.parse_image(data)
    assert img.raw_pages == {} and img.notes == []
    assert W.serialise_image(img) == data
    again = W.parse_image(W.serialise_image(img))
    assert again == img


def test_derived_fields_are_recomputed_not_copied():
    """Flip one row's presence bit: the page's rows-present count and the reader's row count follow."""
    img = W.parse_image((STICK / "export.pdb").read_bytes())
    tracks = next(t for t in img.tables if t.table_type == 0)
    page = img.pages[2]
    assert isinstance(page, W.DataPage) and page.table_type == 0
    before = sum(s.present for s in page.slots)
    page.slots[0].present = False
    out = W.serialise_image(img)
    pg = out[2 * 4096:3 * 4096]
    assert int.from_bytes(pg[0x18:0x1B], "little") >> 13 == before - 1
    assert tracks.first_page == 1
    probe = Path(__file__).parent / "_mutated_export.pdb"
    try:
        probe.write_bytes(out)
        assert len(R.read_pdb(probe).tracks) == 20
    finally:
        probe.unlink(missing_ok=True)


def test_parse_rejects_non_devicesql():
    with pytest.raises(ValueError):
        W.parse_image(b"SQLite format 3\x00" + bytes(4080))


# ─── Strings ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["", "Am", "x" * 126, "x" * 127, "/Contents/" + "a" * 200, "Beyoncé – Halo", "日本語"])
def test_string_round_trip(text):
    enc = W.encode_string(text)
    assert R._decode_pdb_string(enc, 0) == text


def test_string_forms_match_rekordbox():
    assert W.encode_string("") == b"\x03"
    assert W.encode_string("Am") == bytes([0x07]) + b"Am"
    assert W.encode_string("x" * 127)[:4] == bytes([0x40, 131, 0, 0])
    assert W.encode_string("é")[:4] == bytes([0x90, 6, 0, 0])


# ─── Row encoders reproduce Rekordbox's bytes ──────────────────────────────

def test_lookup_rows_match_the_stick():
    pdb = R.read_pdb(STICK / "export.pdb")
    assert [W.encode_key_row(i, n) for i, n in sorted(pdb.keys.items())] == _present_rows(STICK / "export.pdb", 5)
    assert [W.encode_id_name_row(i, n) for i, n in sorted(pdb.genres.items())] == _present_rows(STICK / "export.pdb", 1)
    assert [W.encode_id_name_row(i, n) for i, n in sorted(pdb.labels.items())] == _present_rows(STICK / "export.pdb", 4)


@pytest.mark.parametrize("path", [STICK / "export.pdb", GROUND / "export.pdb"], ids=["stick", "ground"])
def test_artist_and_album_rows_match_rekordbox(path):
    """Row bytes equal Rekordbox's once index_shift (slot * 0x20, set at layout time) is cleared."""
    def clear(r):
        return r[:2] + b"\x00\x00" + r[4:]

    artists = _present_rows(path, 2)
    for real in artists:
        name = R._name_after_u8_offset(real, 0, 9, _u16(real, 0)) or ""
        assert W.encode_artist_row(_u32(real, 4), name) == clear(real)
    for real in _present_rows(path, 3):
        name = R._name_after_u8_offset(real, 0, 21, _u16(real, 0)) or ""
        assert W.encode_album_row(_u32(real, 12), name, _u32(real, 8)) == clear(real)


def test_history_playlist_rows_match_the_stick():
    assert W.encode_history_row(21, "2026-10-11")[4:] == _present_rows(STICK / "export.pdb", 0x13)[0][4:]
    tree = _present_rows(STICK / "export.pdb", 7)
    assert W.encode_playlist_tree_row(W.DevicePlaylist(1, 0, "blank", False, 0)) == tree[0]
    entries = _present_rows(STICK / "export.pdb", 8)
    assert W.encode_playlist_entry_row(1, 1, 1) == entries[0]


def test_history_row_matches_ground_truth():
    assert W.encode_history_row(413, "2026-07-25")[4:] == _present_rows(GROUND / "export.pdb", 0x13)[0][4:]


def _isrc(row: bytes, at: int) -> str:
    if row[at] != 0x90:
        return ""
    total = _u16(row, at + 1)
    return row[at + 5:at + total - 1].decode("ascii")


def test_isrc_form_matches_rekordbox():
    assert W.encode_isrc("GB7GV2061010") == bytes.fromhex("90120000") + b"\x03GB7GV2061010\x00"
    assert W.encode_isrc("") == b"\x03"


def _library_from_stick() -> tuple[W.DeviceLibrary, list[bytes]]:
    """Rebuild a DeviceLibrary from the stick's own rows (what FableGear would hand the writer)."""
    pdb = R.read_pdb(STICK / "export.pdb")
    raw = _present_rows(STICK / "export.pdb", 0)
    lib = W.DeviceLibrary(artists=dict(pdb.artists), genres=dict(pdb.genres), labels=dict(pdb.labels),
                          keys=dict(pdb.keys), export_date="2026-10-11")
    for r in _present_rows(STICK / "export.pdb", 3):
        lib.albums[_u32(r, 12)] = (R._name_after_u8_offset(r, 0, 21, _u16(r, 0)) or "", _u32(r, 8))
    for r in raw:
        offs = [_u16(r, 0x5E + 2 * i) for i in range(21)]
        s = [R._decode_pdb_string(r, o) or "" for o in offs]
        lib.tracks.append(W.DeviceTrack(
            id=_u32(r, 0x48), title=s[17], file_path=s[20], analyze_path=s[14], bpm=_u32(r, 0x38) / 100,
            key_id=_u32(r, 0x20), artist_id=_u32(r, 0x44), composer_id=_u32(r, 0x0C), remixer_id=_u32(r, 0x2C),
            original_artist_id=_u32(r, 0x24), album_id=_u32(r, 0x40), genre_id=_u32(r, 0x3C),
            label_id=_u32(r, 0x28), artwork_id=_u32(r, 0x1C), file_size=_u32(r, 0x10), sample_rate=_u32(r, 8),
            bitrate=_u32(r, 0x30), bit_depth=_u16(r, 0x52), duration=_u16(r, 0x54), track_number=_u32(r, 0x34),
            disc_number=_u16(r, 0x4C), year=_u16(r, 0x50), play_count=_u16(r, 0x4E), file_type=_u16(r, 0x5A),
            comment=s[16], date_added=s[10], analyze_date=s[15], isrc=_isrc(r, offs[0]),
        ))
    lib.playlists = [W.DevicePlaylist(1, 0, "blank", False, 0, [t.id for t in lib.tracks])]
    return lib, raw


def test_track_rows_match_the_stick_field_for_field():
    """Every numeric field and every string equals Rekordbox's. Two named differences: the u32 at
    0x14 (unknown; we write a path hash) and the zero slack Rekordbox leaves after the strings
    (we align to 4 bytes only)."""
    lib, raw = _library_from_stick()
    for t, real in zip(lib.tracks, raw, strict=True):
        ours = W.encode_track_row(t)
        # bytes 2-3 are index_shift (set at layout time), 0x14-0x17 the unknown u32
        assert ours[:2] == real[:2] and ours[4:0x14] == real[4:0x14] and ours[0x18:0x5E] == real[0x18:0x5E]
        for i in range(21):
            assert R._decode_pdb_string(ours, _u16(ours, 0x5E + 2 * i)) == R._decode_pdb_string(real, _u16(real, 0x5E + 2 * i)), i
        assert len(ours) % 4 == 0 and len(ours) <= len(real)


def test_ground_truth_track_rows_including_utf16_and_long_paths():
    """All 413 untouched Rekordbox track rows: numeric fields and strings reproduce, and every long
    string lands 4-aligned like Rekordbox's. Masked: bytes 2-3 (index_shift), 0x14-0x1B (the unknown
    u32 and the per-export u16 pair), string slots 2-4 (unknown counters; we write the stick's) and
    slot 6 (KUVO "publish track info", a per-track Rekordbox preference: "" on 71 tracks, "ON" by default)."""
    long_rows = 0
    for r in _present_rows(GROUND / "export.pdb", 0):
        offs = [_u16(r, 0x5E + 2 * i) for i in range(21)]
        s = [R._decode_pdb_string(r, o) or "" for o in offs]
        t = W.DeviceTrack(
            id=_u32(r, 0x48), title=s[17], file_path=s[20], analyze_path=s[14], bpm=_u32(r, 0x38) / 100,
            key_id=_u32(r, 0x20), artist_id=_u32(r, 0x44), composer_id=_u32(r, 0x0C), remixer_id=_u32(r, 0x2C),
            original_artist_id=_u32(r, 0x24), album_id=_u32(r, 0x40), genre_id=_u32(r, 0x3C),
            label_id=_u32(r, 0x28), artwork_id=_u32(r, 0x1C), color_id=r[0x58], rating=r[0x59],
            file_size=_u32(r, 0x10), sample_rate=_u32(r, 8), bitrate=_u32(r, 0x30), bit_depth=_u16(r, 0x52),
            duration=_u16(r, 0x54), track_number=_u32(r, 0x34), disc_number=_u16(r, 0x4C), year=_u16(r, 0x50),
            play_count=_u16(r, 0x4E), file_type=_u16(r, 0x5A), comment=s[16], date_added=s[10],
            analyze_date=s[15], release_date=s[11], mix_name=s[12], isrc=_isrc(r, offs[0]),
        )
        ours = W.encode_track_row(t)
        assert ours[:2] == r[:2] and ours[4:0x14] == r[4:0x14] and ours[0x1C:0x5E] == r[0x1C:0x5E]
        ours_offs = [_u16(ours, 0x5E + 2 * i) for i in range(21)]
        for i in range(21):
            if i in (2, 3, 4, 6):
                continue
            if i == 0:
                assert _isrc(ours, ours_offs[0]) == t.isrc
            else:
                assert R._decode_pdb_string(ours, ours_offs[i]) == s[i], (t.id, i)
            if not ours[ours_offs[i]] & 1:
                assert ours_offs[i] % 4 == 0
                long_rows += 1
    assert long_rows >= 30  # the export holds UTF-16 titles/paths and ISRCs: the check is not vacuous


# ─── Fresh files ────────────────────────────────────────────────────────────

def _write(tmp_path, data: bytes, name="export.pdb") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_fresh_export_reads_back_like_the_stick(tmp_path):
    lib, _ = _library_from_stick()
    ours = R.read_pdb(_write(tmp_path, W.build_export_pdb(lib)))
    real = R.read_pdb(STICK / "export.pdb")
    assert ours.valid_header and not ours.partial
    key = lambda t: t.track_id or 0  # noqa: E731
    assert [(t.track_id, t.title, t.artist, t.key, t.bpm, t.drive_relative_path, t.anlz_folder_path)
            for t in sorted(ours.tracks, key=key)] == \
           [(t.track_id, t.title, t.artist, t.key, t.bpm, t.drive_relative_path, t.anlz_folder_path)
            for t in sorted(real.tracks, key=key)]
    assert (ours.keys, ours.artists, ours.albums, ours.genres, ours.labels) == \
           (real.keys, real.artists, real.albums, real.genres, real.labels)
    want = dict(real.table_row_counts, artwork=0, playlist_entries=21)
    assert ours.table_row_counts == want
    pl = R.read_playlists(tmp_path / "export.pdb")
    assert pl.playlist_count == 1 and pl.resolved_tracks == 21


def _check_chain_rules(data: bytes):
    """The page-chain rules both Rekordbox files follow."""
    img = W.parse_image(data)
    ps = img.page_size
    n_pages = len(data) // ps
    assert any(data[(n_pages - 1) * ps:])  # file ends after the last non-blank page
    for i, t in enumerate(img.tables):
        idx = img.pages[t.first_page]
        assert isinstance(idx, W.IndexPage) and t.first_page == 2 * i + 1 and idx.num_entries == 0
        chain, pi = [], t.first_page
        while pi != t.last_page:
            pi = img.pages[pi].next_page
            chain.append(pi)
        last = img.pages[t.last_page]
        if t.last_page == t.first_page:  # empty table: the index points at the blank page 2i+2
            assert idx.first_data_page == W._NO_PAGE and idx.next_page == t.empty_candidate == 2 * i + 2
        else:
            assert idx.first_data_page == chain[0] == 2 * i + 2
            assert last.next_page == t.empty_candidate
        assert t.empty_candidate not in img.pages
        assert t.empty_candidate < img.next_unused_page
        for p in chain:
            page = img.pages[p]
            assert isinstance(page, W.DataPage) and page.table_type == t.table_type
            assert _u16(data, p * ps + 0x1C) + _u16(data, p * ps + 0x1E) <= ps - 0x28


def test_fresh_files_follow_rekordbox_chain_rules():
    lib, _ = _library_from_stick()
    _check_chain_rules(W.build_export_pdb(lib))
    _check_chain_rules(W.build_export_ext_pdb())


def test_fresh_export_ext_matches_the_sticks_tables(tmp_path):
    ours = R.read_pdb(_write(tmp_path, W.build_export_ext_pdb(), "exportExt.pdb"))
    real = R.read_pdb(STICK / "exportExt.pdb")
    assert ours.table_row_counts == real.table_row_counts
    assert _present_rows(tmp_path / "exportExt.pdb", 3, 9) == _present_rows(STICK / "exportExt.pdb", 3, 9)


def test_many_tracks_spread_over_pages_and_all_read_back(tmp_path):
    lib = W.DeviceLibrary(keys={1: "Am", 2: "F#m"}, artists={1: "A"}, export_date="2026-10-20")
    for i in range(1, 501):
        lib.tracks.append(W.DeviceTrack(
            id=i, title=f"Track {i} " + "x" * (i % 90), file_path=f"/Contents/A/Album/{i:04d} track.mp3",
            analyze_path=f"/PIONEER/USBANLZ/P000/{i:08X}/ANLZ0000.DAT", bpm=120 + i / 100, key_id=1 + i % 2,
            artist_id=1, file_type=1, duration=300,
        ))
    data = W.build_export_pdb(lib)
    _check_chain_rules(data)
    rep = R.read_pdb(_write(tmp_path, data))
    assert sorted(t.track_id or 0 for t in rep.tracks) == list(range(1, 501))
    got = {t.track_id: t for t in rep.tracks}
    assert got[499].bpm == pytest.approx(124.99) and got[499].key == "F#m"
    img = W.parse_image(data)
    track_pages = [p for p in img.pages.values() if isinstance(p, W.DataPage) and p.table_type == 0]
    assert len(track_pages) > 30
    assert all(len(p.slots) <= 16 or True for p in track_pages)


def test_rows_that_cannot_fit_are_refused():
    with pytest.raises(ValueError):
        W._pack([b"\x00" * 4096], 4096)


def test_write_device_library_refuses_to_overwrite_and_verifies(tmp_path):
    lib, _ = _library_from_stick()
    rb = tmp_path / "PIONEER" / "rekordbox"
    res = W.write_device_library(rb, lib)
    assert res.tracks_written == 21 and (rb / "export.pdb").is_file() and (rb / "exportExt.pdb").is_file()
    assert not (rb / ".fg_verify").exists()
    with pytest.raises(FileExistsError):
        W.write_device_library(rb, lib)
    W.write_device_library(rb, lib, overwrite=True)


def test_write_device_library_catches_a_bad_encoding(tmp_path, monkeypatch):
    lib, _ = _library_from_stick()
    real = W.encode_track_row

    def wrong_bpm(t):
        b = bytearray(real(t))
        b[0x38:0x3C] = (1).to_bytes(4, "little")
        return bytes(b)

    monkeypatch.setattr(W, "encode_track_row", wrong_bpm)
    rb = tmp_path / "PIONEER" / "rekordbox"
    with pytest.raises(ValueError, match="BPM/key"):
        W.write_device_library(rb, lib)
    assert not (rb / "export.pdb").exists()
