# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
"""
chop_shop/waveform_generator.py

Generate Pioneer ANLZ waveform tags from audio, so a FableGear export shows a
real waveform on a CDJ instead of forcing re-analysis.

Tags produced (formats reverse-engineered from real Rekordbox exports captured
under tests/fixtures/rekordbox_ground_truth_export/, cross-checked with Deep
Symmetry's crate-digger spec — the same authority devicesql_reader/anlz_reader
cite):

  Monochrome (DEMONSTRATED byte layout — validated by round-tripping real tags):
    PWAV  .DAT  400-col fixed preview, 1 byte/col
    PWV2  .DAT  100-col fixed preview, 1 byte/col
    PWV3  .EXT  detail, 1 byte/col @ 150 cols/sec
      byte = (whiteness:3 << 5) | height:5      height 0-31, whiteness 0-7

  Color (layout per crate-digger; BEST-EFFORT — structurally correct, not yet
  hardware-validated for exact hue):
    PWV5  .EXT  color detail, 2 bytes/col @ 150 cols/sec
    PWV4  .EXT  color preview, 1200 cols x 6 bytes

  3-band / CDJ-3000 (.2EX) is emitted by build_3band_2ex() as a best-effort
  companion; PWV6/PWV7 3-band pixel semantics are the least-documented and are
  marked accordingly.

Every tag is returned as a complete ANLZ tag blob:
    fourcc(4) + len_header:u32be + len_tag:u32be + header_ext + body

Public interface:
    analyze_audio(path) -> WaveformData
    build_mono_tags(wf)  -> {"PWAV":bytes, "PWV2":bytes, "PWV3":bytes}
    build_color_tags(wf) -> {"PWV5":bytes, "PWV4":bytes}
    DETAIL_COLS_PER_SEC = 150
"""
import logging
import struct
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

DETAIL_COLS_PER_SEC = 150
_PREVIEW_COLS = 400      # PWAV
_TINY_COLS = 100         # PWV2
_COLOR_PREVIEW_COLS = 1200  # PWV4 / PWV6


@dataclass
class WaveformData:
    """Per-column arrays at 150 cols/sec, all length == n_cols."""
    n_cols: int
    height: np.ndarray      # 0..31 int
    whiteness: np.ndarray   # 0..7 int
    # low/mid/high band magnitudes, each normalised 0..1 (for color)
    low: np.ndarray
    mid: np.ndarray
    high: np.ndarray
    duration: float


def _tag(fourcc: str, header_ext: bytes, body: bytes, len_header: int) -> bytes:
    """Assemble one ANLZ tag. len_header counts the 12-byte generic prefix +
    header_ext; len_tag counts the whole tag (header + body)."""
    assert len_header == 12 + len(header_ext), (fourcc, len_header, len(header_ext))
    len_tag = len_header + len(body)
    return fourcc.encode("ascii") + struct.pack(">II", len_header, len_tag) + header_ext + body


def analyze_audio(path, block_seconds: int = 60) -> WaveformData:
    """Read audio (streamed by ~minute, downmixed to mono) and compute per-column
    waveform arrays at 150 cols/sec: peak height, a whiteness/brightness proxy,
    and low/mid/high band energy for color. Fully vectorised per block."""
    import soundfile as sf

    info = sf.info(str(path))
    sr = info.samplerate
    duration = info.frames / info.samplerate
    n_cols = max(1, int(round(duration * DETAIL_COLS_PER_SEC)))

    peak = np.zeros(n_cols, dtype=np.float32)
    band_low = np.zeros(n_cols, dtype=np.float32)
    band_mid = np.zeros(n_cols, dtype=np.float32)
    band_high = np.zeros(n_cols, dtype=np.float32)

    # Columns are 1/150 s. At 44100 Hz that's exactly 294 samples; other rates
    # may be fractional, so cut each block to a whole number of columns and
    # carry no remainder (block boundary == column boundary).
    spc = sr / DETAIL_COLS_PER_SEC
    cols_per_block = block_seconds * DETAIL_COLS_PER_SEC
    win_hann = None
    col = 0
    with sf.SoundFile(str(path)) as f:
        while col < n_cols:
            cols_here = min(cols_per_block, n_cols - col)
            frames = int(round(cols_here * spc))
            data = f.read(frames, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            mono = data.mean(axis=1)
            # trim to whole columns
            usable = int((len(mono) // spc)) if spc != int(spc) else len(mono) // int(spc)
            usable = min(usable, cols_here)
            if usable == 0:
                break
            width = int(round(spc))
            mat = np.zeros((usable, width), dtype=np.float32)
            for c in range(usable):
                a = int(round(c * spc)); b = a + width
                seg = mono[a:b]
                mat[c, :len(seg)] = seg[:width]
            peak[col:col + usable] = np.abs(mat).max(axis=1)
            if win_hann is None or len(win_hann) != width:
                win_hann = np.hanning(width).astype(np.float32)
            spec = np.abs(np.fft.rfft(mat * win_hann, axis=1))
            freqs = np.fft.rfftfreq(width, 1.0 / sr)
            lo_m = freqs < 200; mi_m = (freqs >= 200) & (freqs < 2000); hi_m = freqs >= 2000
            band_low[col:col + usable] = spec[:, lo_m].sum(axis=1)
            band_mid[col:col + usable] = spec[:, mi_m].sum(axis=1)
            band_high[col:col + usable] = spec[:, hi_m].sum(axis=1)
            col += usable

    # Normalise
    pk = peak.max() or 1.0
    height = np.clip(np.round((peak / pk) ** 0.6 * 31.0), 0, 31).astype(np.int16)
    # whiteness: high-frequency share -> brighter; silence -> 7 (matches RB)
    tot = band_low + band_mid + band_high
    with np.errstate(divide="ignore", invalid="ignore"):
        hf_share = np.where(tot > 0, band_high / tot, 0.0)
    whiteness = np.clip(np.round(hf_share * 7.0), 0, 7).astype(np.int16)
    whiteness[peak < (pk * 0.02)] = 7  # near-silence renders white, as in real files

    # Keep raw band magnitudes; builders normalise per-purpose (per-column hue
    # for color, global scale for 3-band heights).
    return WaveformData(
        n_cols=n_cols, height=height, whiteness=whiteness,
        low=band_low, mid=band_mid, high=band_high, duration=duration,
    )


# ── Color tags (PWV5 detail, PWV4 preview) ─────────────────────────────────
# PWV5 16-bit layout (reverse-engineered from ground truth, height field
# validated at r=0.92 vs PWV3): red(3)<<13 | green(3)<<10 | blue(3)<<7 |
# height(5)<<2. Colour maps the 3 frequency bands to RGB (high→red, mid→green,
# low→blue) — a plausible spectral colouring; exact Rekordbox hue is not
# byte-matched (no same-track ground truth), so this is best-effort on hue,
# exact on height/structure.

def _rgb3(wf: WaveformData):
    tot = wf.low + wf.mid + wf.high
    tot = np.where(tot > 0, tot, 1.0)
    r = np.clip(np.round(wf.high / tot * 7.0), 0, 7).astype(np.int32)
    g = np.clip(np.round(wf.mid / tot * 7.0), 0, 7).astype(np.int32)
    b = np.clip(np.round(wf.low / tot * 7.0), 0, 7).astype(np.int32)
    # near-silence renders white (matches ground truth 0xff80)
    sil = wf.height < 1
    r[sil] = g[sil] = b[sil] = 7
    return r, g, b


def build_color_tags(wf: WaveformData) -> dict:
    r, g, b = _rgb3(wf)
    h = wf.height.astype(np.int32)
    vals = (r << 13) | (g << 10) | (b << 7) | (h << 2)
    pwv5_body = vals.astype(">u2").tobytes()
    pwv5 = _tag("PWV5", struct.pack(">III", 2, wf.n_cols, 0x00960305), pwv5_body, 24)

    # PWV4 color preview, 1200 cols x 6 bytes. Layout not fully verified; emit
    # [height, height, red*, green*, blue*, 0] with colours scaled to 0-31,
    # which renders a plausible colour overview.
    idx = np.linspace(0, wf.n_cols, _COLOR_PREVIEW_COLS + 1).astype(int)
    pv = bytearray()
    for i in range(_COLOR_PREVIEW_COLS):
        a, e = idx[i], max(idx[i] + 1, idx[i + 1])
        hh = int(wf.height[a:e].max())
        rr = int(round(r[a:e].mean() * 31 / 7)); gg = int(round(g[a:e].mean() * 31 / 7)); bb = int(round(b[a:e].mean() * 31 / 7))
        pv += bytes((hh, hh, rr & 0x1f, gg & 0x1f, bb & 0x1f, 0))
    pwv4 = _tag("PWV4", struct.pack(">III", 6, _COLOR_PREVIEW_COLS, 0), bytes(pv), 24)
    return {"PWV5": pwv5, "PWV4": pwv4}


# ── 3-band tags for .2EX (PWV7 detail, PWV6 preview, PWVC summary) ──────────
# PWV7/PWV6 are 3 bytes/col = [low, mid, high] band heights 0-31 (validated:
# silence=[0,0,0], bass-heavy loud=[28,17,4]).

def _band_heights(wf: WaveformData):
    # scale each band by a shared reference so the loudest band peaks near 31
    ref = np.percentile(np.concatenate([wf.low, wf.mid, wf.high]), 99.5) or 1.0
    def sc(x):
        return np.clip(np.round(np.sqrt(np.clip(x / ref, 0, None)) * 31.0), 0, 31).astype(np.uint8)
    lo, mi, hi = sc(wf.low), sc(wf.mid), sc(wf.high)
    sil = wf.height < 1
    lo[sil] = mi[sil] = hi[sil] = 0
    return lo, mi, hi


def build_3band_tags(wf: WaveformData) -> dict:
    lo, mi, hi = _band_heights(wf)
    detail = np.empty((wf.n_cols, 3), dtype=np.uint8)
    detail[:, 0] = lo; detail[:, 1] = mi; detail[:, 2] = hi
    pwv7 = _tag("PWV7", struct.pack(">III", 3, wf.n_cols, 0x00960000), detail.tobytes(), 24)

    idx = np.linspace(0, wf.n_cols, _COLOR_PREVIEW_COLS + 1).astype(int)
    prev = bytearray()
    for i in range(_COLOR_PREVIEW_COLS):
        a, e = idx[i], max(idx[i] + 1, idx[i + 1])
        prev += bytes((int(lo[a:e].max()), int(mi[a:e].max()), int(hi[a:e].max())))
    pwv6 = _tag("PWV6", struct.pack(">II", 3, _COLOR_PREVIEW_COLS), bytes(prev), 20)

    # PWVC: 3 x u16 colour summary (observed constants ~ per-band references).
    pwvc = _tag("PWVC", b"\x00\x00", struct.pack(">HHH", 0x7f, 0x8c, 0x8e), 14)
    return {"PWV7": pwv7, "PWV6": pwv6, "PWVC": pwvc}


# ── ANLZ file assembly ─────────────────────────────────────────────────────

ANLZ_MAGIC = b"PMAI"


def _extract_tag(anlz_bytes: bytes, fourcc: str):
    """Return the raw blob of the first ``fourcc`` tag in an ANLZ file, or None."""
    if anlz_bytes[:4] != ANLZ_MAGIC:
        return None
    len_header = struct.unpack_from(">I", anlz_bytes, 4)[0]
    off = len_header
    n = len(anlz_bytes)
    while off + 12 <= n:
        fc = anlz_bytes[off:off + 4]
        lh, lt = struct.unpack_from(">II", anlz_bytes, off + 4)
        if lt < 12 or off + lt > n:
            break
        if fc == fourcc.encode("ascii"):
            return anlz_bytes[off:off + lt]
        off += lt
    return None


def inject_tags(anlz_path, tag_blobs) -> None:
    """Append tag blobs to an existing ANLZ file and patch the PMAI len_file
    header (offset 8, u32be) to the new total. Readers walk the tag chain to
    EOF, so appended tags are found in order."""
    from pathlib import Path as _P
    data = bytearray(_P(anlz_path).read_bytes())
    if data[:4] != ANLZ_MAGIC:
        raise ValueError(f"{anlz_path}: not a PMAI ANLZ file")
    for blob in tag_blobs:
        data += blob
    struct.pack_into(">I", data, 8, len(data))  # len_file
    _P(anlz_path).write_bytes(bytes(data))


def build_2ex(two_ex_path, ppth_blob: bytes, tag_blobs) -> None:
    """Create an ANLZ0000.2EX file: PMAI header + PPTH + the given 3-band tags."""
    from pathlib import Path as _P
    body = ppth_blob + b"".join(tag_blobs)
    len_header = 28
    header = ANLZ_MAGIC + struct.pack(">II", len_header, len_header + len(body)) + b"\x00" * (len_header - 12)
    _P(two_ex_path).write_bytes(header + body)


def estimate_first_beat_ms(path, bpm: float, probe_seconds: float = 40.0) -> float:
    """Estimate the phase offset of beat 1 (ms) so a synthesized constant grid
    lands on real downbeats instead of t=0. Runs librosa beat tracking (with the
    known BPM as a prior) over the first ``probe_seconds`` and returns the first
    detected beat time, folded into [0, one-beat). Returns 0.0 on any failure —
    a grid at the right tempo but t=0 phase is still usable."""
    try:
        import librosa
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=probe_seconds)
        if y.size == 0 or not bpm:
            return 0.0
        _, beats = librosa.beat.beat_track(y=y, sr=sr, start_bpm=float(bpm), units="time")
        if len(beats) == 0:
            return 0.0
        beat_ms = 60000.0 / float(bpm)
        return float((beats[0] * 1000.0) % beat_ms)
    except Exception as exc:  # noqa: BLE001
        log.warning("first-beat estimate failed (%s): %s", type(exc).__name__, exc)
        return 0.0


# ── Monochrome tags ─────────────────────────────────────────────────────────

def _mono_bytes(height: np.ndarray, whiteness: np.ndarray) -> bytes:
    return bytes(((int(w) & 7) << 5) | (int(h) & 31) for h, w in zip(height, whiteness))


def _downsample(height: np.ndarray, whiteness: np.ndarray, out_cols: int):
    """Max-pool height (and mean whiteness) down to out_cols columns."""
    n = len(height)
    idx = np.linspace(0, n, out_cols + 1).astype(int)
    h_out = np.zeros(out_cols, dtype=np.int16)
    w_out = np.zeros(out_cols, dtype=np.int16)
    for i in range(out_cols):
        a, b = idx[i], max(idx[i] + 1, idx[i + 1])
        h_out[i] = height[a:b].max() if b > a else 0
        w_out[i] = int(round(whiteness[a:b].mean())) if b > a else 0
    return h_out, w_out


def build_mono_tags(wf: WaveformData) -> dict:
    """PWAV (400) + PWV2 (100) previews for .DAT, PWV3 (n_cols) detail for .EXT."""
    # PWV3 detail
    pwv3_body = _mono_bytes(wf.height, wf.whiteness)
    pwv3 = _tag("PWV3", struct.pack(">III", 1, wf.n_cols, 0x00960000), pwv3_body, 24)

    # PWAV 400-col preview  (header: len:u32=400, unknown:u32=0x00010000)
    h400, w400 = _downsample(wf.height, wf.whiteness, _PREVIEW_COLS)
    pwav = _tag("PWAV", struct.pack(">II", _PREVIEW_COLS, 0x00010000),
                _mono_bytes(h400, w400), 20)

    # PWV2 100-col preview
    h100, w100 = _downsample(wf.height, wf.whiteness, _TINY_COLS)
    pwv2 = _tag("PWV2", struct.pack(">II", _TINY_COLS, 0x00010000),
                _mono_bytes(h100, w100), 20)

    return {"PWAV": pwav, "PWV2": pwv2, "PWV3": pwv3}
