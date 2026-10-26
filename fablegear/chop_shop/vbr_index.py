"""PVBR ANLZ tag builder: the VBR seek index Rekordbox writes into every ANLZ0000.DAT.

Layout (pyrekordbox ``PVBR`` struct, confirmed against all 412 tracks of
``dev/tests/fixtures/rekordbox_ground_truth_export``): fourcc ``PVBR``, len_header 16,
len_tag 1620; header extension one u32 (always 0); body 400 u32 index entries plus one
trailing u32. Measured content by track class:

- non-MP3 audio (WAV/AIFF): every u32 is zero (184 of 184 such tracks);
- constant-bitrate MP3: index entries all zero, trailing u32 = total sample count
  (always a multiple of the MP3 frame size, 1152);
- variable-bitrate MP3 (3 tracks): 400 monotonically increasing byte offsets into the
  file, the first at the start of audio data (just past the ID3v2 tag), plus the same
  trailing sample count.

The VBR entries are inferred, not documented: entry *i* is taken to be the byte offset
of the frame ``i/400`` of the way through the track, which fits the three captures
(first entry = ID3v2 size, monotone, last entry short of file end). Deep Symmetry's
format notes leave the tag body unexplained.

``build_pvbr`` never fails: anything unreadable or non-MP3 gets the all-zero tag,
which is exactly what Rekordbox writes for PCM audio.
"""

from __future__ import annotations

import struct
from pathlib import Path

PVBR_ENTRIES = 400
_PVBR_LEN_HEADER = 16
_PVBR_LEN_TAG = _PVBR_LEN_HEADER + 4 * (PVBR_ENTRIES + 1)

# bitrate (kbps) by header index, Layer III only
_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_SAMPLE_RATES = {  # by (version_bits, samplerate_index)
    3: (44100, 48000, 32000),   # MPEG1
    2: (22050, 24000, 16000),   # MPEG2
    0: (11025, 12000, 8000),    # MPEG2.5
}


def _id3v2_size(data: bytes) -> int:
    """Bytes to skip for a leading ID3v2 tag (0 if none)."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    footer = 10 if data[5] & 0x10 else 0
    return 10 + size + footer


def _walk_mp3_frames(data: bytes, start: int) -> tuple[list[int], int, bool] | None:
    """Walk Layer III frame headers from ``start``. Returns (frame offsets, total
    samples, is_cbr), or None if ``data`` does not look like an MP3 (no valid first
    frame, or fewer than 8 frames before losing sync)."""
    offsets: list[int] = []
    samples = 0
    bitrate_indices: set[int] = set()
    pos = start
    n = len(data)
    while pos + 4 <= n:
        b0, b1, b2 = data[pos], data[pos + 1], data[pos + 2]
        if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
            break
        version = (b1 >> 3) & 3          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5, 1=reserved
        layer = (b1 >> 1) & 3            # 1=Layer III
        br_idx = (b2 >> 4) & 0xF
        sr_idx = (b2 >> 2) & 3
        padding = (b2 >> 1) & 1
        if version == 1 or layer != 1 or br_idx in (0, 15) or sr_idx == 3:
            break
        bitrate = (_BITRATES_V1_L3 if version == 3 else _BITRATES_V2_L3)[br_idx]
        sample_rate = _SAMPLE_RATES[version][sr_idx]
        if version == 3:
            frame_size = (144000 * bitrate) // sample_rate + padding
            frame_samples = 1152
        else:
            frame_size = (72000 * bitrate) // sample_rate + padding
            frame_samples = 576
        offsets.append(pos)
        samples += frame_samples
        bitrate_indices.add(br_idx)
        pos += frame_size
    # Trailing ID3v1/APE tags end the walk cleanly; a sync loss after only a handful of
    # frames means this was never an MP3 stream worth indexing.
    if len(offsets) < 8:
        return None
    return offsets, samples, len(bitrate_indices) == 1


def _pvbr_tag(idx: list[int], final: int) -> bytes:
    assert len(idx) == PVBR_ENTRIES
    return (
        b"PVBR"
        + struct.pack(">III", _PVBR_LEN_HEADER, _PVBR_LEN_TAG, 0)
        + struct.pack(f">{PVBR_ENTRIES}I", *idx)
        + struct.pack(">I", final & 0xFFFFFFFF)
    )


def build_pvbr(audio_path) -> bytes:
    """The PVBR tag blob for one track; all zeros unless the file is a readable MP3."""
    zeros = _pvbr_tag([0] * PVBR_ENTRIES, 0)
    try:
        path = Path(audio_path)
        if path.suffix.lower() != ".mp3" or not path.is_file():
            return zeros
        data = path.read_bytes()
        walked = _walk_mp3_frames(data, _id3v2_size(data))
        if walked is None:
            return zeros
        offsets, samples, is_cbr = walked
        if is_cbr:
            return _pvbr_tag([0] * PVBR_ENTRIES, samples)
        nframes = len(offsets)
        idx = [offsets[(i * nframes) // PVBR_ENTRIES] for i in range(PVBR_ENTRIES)]
        return _pvbr_tag(idx, samples)
    except Exception:
        return zeros
