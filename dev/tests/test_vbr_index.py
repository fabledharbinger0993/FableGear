"""PVBR seek-index builder: tag envelope and the three content classes measured in the
untouched Rekordbox export (PCM -> all zeros; CBR MP3 -> zero index + sample count;
VBR MP3 -> 400 byte offsets + sample count)."""

import struct
import sys
from itertools import pairwise
from pathlib import Path

_CHOP_SHOP = Path(__file__).resolve().parents[2] / "fablegear" / "chop_shop"
if str(_CHOP_SHOP) not in sys.path:
    sys.path.insert(0, str(_CHOP_SHOP))

import vbr_index
from vbr_index import PVBR_ENTRIES, build_pvbr


def _frame(bitrate_index: int) -> bytes:
    """One MPEG1 Layer III 44.1 kHz frame (header + zero payload)."""
    bitrate = vbr_index._BITRATES_V1_L3[bitrate_index]
    size = (144000 * bitrate) // 44100
    header = bytes([0xFF, 0xFB, (bitrate_index << 4) | 0x00, 0x00])
    return header + b"\x00" * (size - 4)


def _id3v2(size: int) -> bytes:
    body_size = size - 10
    return b"ID3\x03\x00\x00" + bytes(
        [(body_size >> 21) & 0x7F, (body_size >> 14) & 0x7F, (body_size >> 7) & 0x7F, body_size & 0x7F]
    ) + b"\x00" * body_size


def _parse(blob: bytes):
    assert blob[:4] == b"PVBR"
    len_header, len_tag, u1 = struct.unpack(">III", blob[4:16])
    assert (len_header, len_tag, u1) == (16, 1620, 0)
    assert len(blob) == len_tag
    words = struct.unpack(f">{PVBR_ENTRIES + 1}I", blob[16:])
    return list(words[:-1]), words[-1]


def test_non_mp3_gets_all_zero_tag(tmp_path):
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)
    idx, final = _parse(build_pvbr(wav))
    assert idx == [0] * PVBR_ENTRIES
    assert final == 0


def test_missing_file_gets_all_zero_tag(tmp_path):
    idx, final = _parse(build_pvbr(tmp_path / "missing.mp3"))
    assert idx == [0] * PVBR_ENTRIES
    assert final == 0


def test_mp3_extension_without_mp3_frames_gets_zeros(tmp_path):
    fake = tmp_path / "fake.mp3"
    fake.write_bytes(b"not an mpeg stream at all" * 10)
    idx, final = _parse(build_pvbr(fake))
    assert idx == [0] * PVBR_ENTRIES
    assert final == 0


def test_cbr_mp3_zero_index_and_sample_count(tmp_path):
    n_frames = 500
    mp3 = tmp_path / "cbr.mp3"
    mp3.write_bytes(_id3v2(2048) + _frame(9) * n_frames)
    idx, final = _parse(build_pvbr(mp3))
    assert idx == [0] * PVBR_ENTRIES
    assert final == n_frames * 1152


def test_vbr_mp3_monotonic_offsets_and_sample_count(tmp_path):
    id3_size = 1769  # the size seen on the real VBR capture's first entry
    frames = [_frame(9) if i % 2 == 0 else _frame(14) for i in range(600)]
    mp3 = tmp_path / "vbr.mp3"
    mp3.write_bytes(_id3v2(id3_size) + b"".join(frames))
    idx, final = _parse(build_pvbr(mp3))
    assert final == 600 * 1152
    assert idx[0] == id3_size  # first audio frame, just past the ID3v2 tag
    assert all(a <= b for a, b in pairwise(idx))  # monotone through the file
    assert idx[-1] > idx[0]
    assert idx[-1] < mp3.stat().st_size


def test_zero_tag_matches_rekordbox_pcm_capture(tmp_path):
    """Our all-zero tag is byte-identical to the PVBR Rekordbox writes for PCM audio."""
    root = Path(__file__).resolve().parent / "fixtures" / "rekordbox_ground_truth_export" / "PIONEER" / "USBANLZ"
    import waveform_generator as wg
    for dat in sorted(root.glob("P*/*/ANLZ0000.DAT")):
        data = dat.read_bytes()
        ppth = wg._extract_tag(data, "PPTH")
        path = ppth[16:].decode("utf-16-be", "replace").rstrip("\x00")
        if path.lower().endswith((".aiff", ".aif", ".wav")):
            real_pvbr = wg._extract_tag(data, "PVBR")
            assert real_pvbr == build_pvbr(tmp_path / "any.wav")
            return
    raise AssertionError("no PCM track found in the ground-truth fixture")


def test_trailing_id3v1_tag_ends_walk_cleanly(tmp_path):
    n_frames = 300
    mp3 = tmp_path / "tagged.mp3"
    mp3.write_bytes(_frame(9) * n_frames + b"TAG" + b"\x00" * 125)
    idx, final = _parse(build_pvbr(mp3))
    assert final == n_frames * 1152
    assert idx == [0] * PVBR_ENTRIES
