# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-07-13
"""
fablegear / chop_shop/anlz_reader.py

Read-only deep parser for Pioneer ANLZ analysis files
(``PIONEER/USBANLZ/.../ANLZ0000.DAT``, ``.EXT``, ``.2EX``). This is Phase B
("deep read") of the dual-format export campaign (see
``docs/dual_format_export.md``) — ``usb_inspector.py`` (Phase A) only counts
ANLZ track directories; this module walks each file's PMAI tag chain and
decodes the tags FableGear needs for export validation.

The tag chain is walked BY HAND, not through pyrekordbox/construct: CDJ-3000
3-band tags (PWV6/PWV7/PWVC, found in ``.2EX``) recursion-error construct's
``AnlzFile.parse_file()`` in pyrekordbox 0.4.4, so a single manual walker is
used for every file type instead of splitting the implementation between a
library path and a hand-rolled ``.2EX`` path.

Decode depth by tag (see ``docs/format_samples/DJMTGO_inspection.md`` and
``docs/dual_format_export.md`` for the evidence each of these rests on):

  DEMONSTRATED (full field decode, byte layout confirmed against real
  hardware evidence committed to this repo):
    PPTH            — file path (``len_path:u32be`` + UTF-16BE bytes)
    PQTZ            — beat grid (2×unknown u32 + ``len_beats:u32be`` +
                       per-beat ``[beat_no:u16be][tempo:u16be][time_ms:u32be]``)
    PWV6/PWV7/PWVC  — 3-band waveform preview/detail/color: header fields
                       (``len_entry_bytes``, ``len_entries``) are decoded;
                       raw entry/pixel bytes are counted but not
                       semantically decoded (out of scope — see campaign doc).

  SPECULATIVE (presence + tag size only — no byte-level spec verified
  against a fixture committed to this repo):
    PCOB, PCO2, PSSI, PQT2, PVBR, PWAV, PWV2, PWV3, PWV4, PWV5
    Community tools (crate-digger/dysentery, pyrekordbox) document richer
    structure for several of these, but this module does not reproduce
    field offsets it cannot verify locally. Treat any per-field claim about
    these tags elsewhere as UNVERIFIED until real hardware samples land in
    ``docs/format_samples/``.

Public interface:
    parse_anlz_file(path) -> AnlzFileReport
    read_anlz_set(anlz_dir) -> AnlzSetReport
"""

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

ANLZ_MAGIC = b"PMAI"

# Tags this module gives a full field-level decode, per the module docstring.
_FULL_DECODE_TAGS = {"PPTH", "PQTZ", "PWV6", "PWV7", "PWVC"}


@dataclass
class BeatGridEntry:
    """One entry from a PQTZ beat grid."""
    beat_no: int
    tempo_bpm: float
    time_ms: int


@dataclass
class WaveformTagInfo:
    """Header-level metadata for a 3-band/color waveform tag — counts only,
    no pixel-level decode (out of scope, see module docstring)."""
    len_entry_bytes: Optional[int] = None
    len_entries: Optional[int] = None
    entry_bytes_total: int = 0


@dataclass
class AnlzTagInfo:
    """One tag's position in the chain, as found by the manual walker."""
    fourcc: str
    offset: int
    len_header: int
    len_tag: int


@dataclass
class AnlzFileReport:
    """Everything ``parse_anlz_file`` learned about one ANLZ file."""
    path: str = ""
    exists: bool = False
    readable: bool = False
    file_size: int = 0
    len_header: Optional[int] = None
    len_file: Optional[int] = None
    tags: List[AnlzTagInfo] = field(default_factory=list)
    tags_present: List[str] = field(default_factory=list)
    ppth_path: Optional[str] = None
    beat_grid: List[BeatGridEntry] = field(default_factory=list)
    waveform_tags: Dict[str, WaveformTagInfo] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def has_beat_grid(self) -> bool:
        return bool(self.beat_grid)

    @property
    def has_waveform(self) -> bool:
        return bool(self.waveform_tags)


@dataclass
class AnlzSetReport:
    """A track's full ANLZ set — ``.DAT`` (base) plus optional ``.EXT``/``.2EX``."""
    anlz_dir: str = ""
    dat: Optional[AnlzFileReport] = None
    ext: Optional[AnlzFileReport] = None
    two_ex: Optional[AnlzFileReport] = None
    notes: List[str] = field(default_factory=list)

    @property
    def track_path(self) -> Optional[str]:
        """The embedded audio file path, from whichever file carries PPTH."""
        for report in (self.dat, self.ext, self.two_ex):
            if report is not None and report.ppth_path:
                return report.ppth_path
        return None

    @property
    def beat_count(self) -> int:
        # .2EX is intentionally excluded: the real ANLZ0000.2EX samples this
        # module was verified against (see docs/dual_format_export.md) carry
        # only PPTH/PWV7/PWV6/PWVC — never PQTZ. .EXT carries PQT2 (2nd-gen
        # grid, presence/size only) rather than PQTZ. If a real .2EX sample
        # is ever found to carry PQTZ, add it to this loop.
        for report in (self.dat, self.ext):
            if report is not None and report.beat_grid:
                return len(report.beat_grid)
        return 0


# ─── Manual tag-chain walker ──────────────────────────────────────────────────

def _walk_tags(data: bytes, start: int) -> List[AnlzTagInfo]:
    """Walk the PMAI tag chain by hand, from ``start`` to the end of ``data``.

    Each tag is ``magic(4) + len_header:u32be + len_tag:u32be + body``, where
    ``len_tag`` is the total size of the tag (header included) — the next tag
    begins at ``offset + len_tag``. A tag whose declared ``len_tag`` would run
    past the end of the buffer, or whose ``len_header``/``len_tag`` are out of
    the plausible range (``12 <= len_header <= len_tag``), stops the walk
    rather than reading garbage or letting a downstream decoder slice with a
    negative/out-of-range length.
    """
    tags: List[AnlzTagInfo] = []
    offset = start
    n = len(data)
    while offset + 12 <= n:
        fourcc = data[offset:offset + 4].decode("ascii", errors="replace")
        len_header, len_tag = struct.unpack_from(">II", data, offset + 4)
        if len_tag < 12 or offset + len_tag > n or len_header < 12 or len_header > len_tag:
            logger.warning(
                "Tag chain stopped at offset %d: %r declares len_header=%d len_tag=%d "
                "(buffer has %d bytes remaining)",
                offset, fourcc, len_header, len_tag, n - offset,
            )
            break
        tags.append(AnlzTagInfo(fourcc=fourcc, offset=offset, len_header=len_header, len_tag=len_tag))
        offset += len_tag
    return tags


def _decode_ppth(data: bytes, tag: AnlzTagInfo) -> Optional[str]:
    """PPTH: ``len_path:u32be`` lives in the header-extension region
    (offset+12..offset+len_header, same slot PWV6/PWV7 use for their count
    fields) — NOT as a prefix inside the body. The body itself is exactly
    the UTF-16BE path bytes, no embedded length. Confirmed against real
    ANLZ0000.2EX/.EXT samples: len_header=16 (12 generic + 4-byte len_path),
    and len_path always equals len_tag - len_header exactly. An earlier
    version of this function read the length from the wrong offset (the
    first 4 body bytes, i.e. the first two path characters) and silently
    truncated every decoded path by two characters.
    """
    path_bytes = data[tag.offset + tag.len_header: tag.offset + tag.len_tag]
    try:
        return path_bytes.decode("utf-16-be").rstrip("\x00")
    except UnicodeDecodeError as exc:
        logger.warning("PPTH decode failed at offset %d: %s", tag.offset, exc)
        return None


def _decode_pqtz(data: bytes, tag: AnlzTagInfo) -> List[BeatGridEntry]:
    """PQTZ body: 2×unknown u32 + ``len_beats:u32be``, then per-beat entries."""
    body = data[tag.offset + tag.len_header: tag.offset + tag.len_tag]
    if len(body) < 12:
        return []
    _unknown1, _unknown2, len_beats = struct.unpack_from(">III", body, 0)
    entries: List[BeatGridEntry] = []
    entry_offset = 12
    for _ in range(len_beats):
        if entry_offset + 8 > len(body):
            logger.warning(
                "PQTZ declares %d beats but body truncated after %d entries",
                len_beats, len(entries),
            )
            break
        beat_no, tempo, time_ms = struct.unpack_from(">HHI", body, entry_offset)
        entries.append(BeatGridEntry(beat_no=beat_no, tempo_bpm=tempo / 100.0, time_ms=time_ms))
        entry_offset += 8
    return entries


def _decode_waveform_header(data: bytes, tag: AnlzTagInfo) -> WaveformTagInfo:
    """PWV6/PWV7/PWVC header fields — counts only, no pixel decode.

    Layout confirmed against ``docs/format_samples/DJMTGO_inspection.md``:
    12-byte generic prefix, then up to two u32be fields (``len_entry_bytes``,
    ``len_entries``) filling the rest of ``len_header``, then raw entry bytes
    filling the rest of ``len_tag``. PWVC's header is too short to carry
    either field (it's a small fixed-size color summary) — both stay None
    and only the raw byte count is recorded.
    """
    info = WaveformTagInfo()
    post_generic = data[tag.offset + 12: tag.offset + tag.len_header]
    if len(post_generic) >= 4:
        (info.len_entry_bytes,) = struct.unpack_from(">I", post_generic, 0)
    if len(post_generic) >= 8:
        (info.len_entries,) = struct.unpack_from(">I", post_generic, 4)
    info.entry_bytes_total = tag.len_tag - tag.len_header
    return info


# ─── Public interface ─────────────────────────────────────────────────────────

def parse_anlz_file(path: PathLike) -> AnlzFileReport:
    """Parse one ANLZ file's PMAI tag chain. Read-only.

    Parameters
    ----------
    path : str or Path
        Path to an ``ANLZ0000.DAT`` / ``.EXT`` / ``.2EX`` file.

    Returns
    -------
    report : AnlzFileReport
        Tag chain, decoded PPTH/PQTZ/waveform-header fields, and notes.
        Never raises for a missing/malformed file — failures are recorded
        in ``report.notes`` (same "shallow claim" honesty as usb_inspector.py).
    """
    p = Path(path)
    report = AnlzFileReport(path=str(p))
    if not p.is_file():
        report.notes.append("file not found")
        return report
    report.exists = True
    report.file_size = p.stat().st_size

    try:
        data = p.read_bytes()
    except OSError as exc:
        report.notes.append(f"unreadable: {exc}")
        return report
    report.readable = True

    if len(data) < 12 or data[0:4] != ANLZ_MAGIC:
        report.notes.append("missing PMAI magic — not an ANLZ file")
        return report

    len_header, len_file = struct.unpack_from(">II", data, 4)
    report.len_header = len_header
    report.len_file = len_file
    if len_header < 12 or len_header > len(data):
        report.notes.append(f"implausible header length {len_header}")
        return report
    if len_file < len_header or len_file > len(data):
        report.notes.append(f"implausible file length {len_file} (actual {len(data)})")
        return report

    tags = _walk_tags(data, len_header)
    report.tags = tags
    report.tags_present = [t.fourcc for t in tags]

    for tag in tags:
        if tag.fourcc == "PPTH":
            report.ppth_path = _decode_ppth(data, tag)
        elif tag.fourcc == "PQTZ":
            report.beat_grid = _decode_pqtz(data, tag)
        elif tag.fourcc in ("PWV6", "PWV7", "PWVC"):
            report.waveform_tags[tag.fourcc] = _decode_waveform_header(data, tag)

    speculative = sorted(set(report.tags_present) - _FULL_DECODE_TAGS)
    if speculative:
        report.notes.append(
            "presence/size only (no byte-level spec verified in this repo): "
            + ", ".join(speculative)
        )

    logger.info(
        "Parsed %s: %d tags (%s), beat_grid=%d, ppth=%s",
        p.name, len(tags), ",".join(report.tags_present),
        len(report.beat_grid), bool(report.ppth_path),
    )
    return report


def read_anlz_set(anlz_dir: PathLike) -> AnlzSetReport:
    """Parse a track's full ANLZ set from its per-track directory.

    Parameters
    ----------
    anlz_dir : str or Path
        Directory expected to contain ``ANLZ0000.DAT`` and optionally
        ``ANLZ0000.EXT`` / ``ANLZ0000.2EX`` — the layout usb_inspector.py
        counts via ``USBANLZ/*/*/ANLZ0000.DAT``.

    Returns
    -------
    report : AnlzSetReport
    """
    d = Path(anlz_dir)
    report = AnlzSetReport(anlz_dir=str(d))
    if not d.is_dir():
        report.notes.append("not a directory")
        return report

    dat_path = d / "ANLZ0000.DAT"
    ext_path = d / "ANLZ0000.EXT"
    two_ex_path = d / "ANLZ0000.2EX"

    if dat_path.is_file():
        report.dat = parse_anlz_file(dat_path)
    else:
        report.notes.append("ANLZ0000.DAT not found")

    if ext_path.is_file():
        report.ext = parse_anlz_file(ext_path)
    if two_ex_path.is_file():
        report.two_ex = parse_anlz_file(two_ex_path)

    return report
