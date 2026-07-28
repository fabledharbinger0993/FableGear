# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-07-13
"""
fablegear / chop_shop/pioneer_settings.py

Read-only parser for Pioneer/rekordbox player & mixer settings files
(``MYSETTING.DAT``, ``MYSETTING2.DAT``, ``DEVSETTING.DAT``,
``DJMMYSETTING.DAT``). Phase B ("deep read") of the dual-format export
campaign — usb_inspector.py (Phase A) only records which settings files are
present; this module actually parses their contents.

pyrekordbox 0.4.4 already implements a correct binary parser for all four
recognized filenames (``pyrekordbox.mysettings.read_mysetting_file``) — this
module uses it directly rather than re-deriving the format, per the campaign
doc's "USE IT where it works; hand-parse only what it can't" instruction.

For a file whose name pyrekordbox doesn't recognize, this module falls back
to a hand-parsed structural plausibility check against the layout documented
in ``docs/dual_format_export.md``: ``len_strings:u32(LE)=0x60``, a
brand/product/version string block, then ``data_len:u32``, a
``78 56 34 12`` (LE ``0x12345678``) sentinel, an ``entry_count:u32``, opaque
setting bytes, and a trailing ``checksum:u16``. This fallback is
DEMONSTRATED only at the level of "does the header look like this format" —
it does not decode individual settings (that's what the pyrekordbox path is
for).

Public interface:
    read_settings_file(path) -> SettingsFileReport
    read_settings_tree(pioneer_dir) -> List[SettingsFileReport]
"""

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Filenames pyrekordbox.mysettings recognizes (mirrors usb_inspector.py's
# SETTINGS_FILES basenames exactly).
KNOWN_SETTINGS_FILENAMES = (
    "MYSETTING.DAT", "MYSETTING2.DAT", "DEVSETTING.DAT", "DJMMYSETTING.DAT",
)

_LEN_STRINGS_EXPECTED = 0x60
_SENTINEL = b"\x78\x56\x34\x12"  # LE 0x12345678
_BRAND_MARKERS = (b"PIONEER DJ", b"PioneerDJ", b"PIONEER")


@dataclass
class SettingsFileReport:
    """Everything ``read_settings_file`` learned about one settings file."""
    path: str = ""
    filename: str = ""
    present: bool = False
    readable: bool = False
    valid: Optional[bool] = None
    parsed_via: str = ""  # "pyrekordbox" | "hand-parsed header" | ""
    brand: Optional[str] = None
    entry_count: Optional[int] = None
    settings: Dict[str, str] = field(default_factory=dict)
    detail: str = ""
    notes: List[str] = field(default_factory=list)


def _read_via_pyrekordbox(path: Path) -> Optional[SettingsFileReport]:
    """Try the real pyrekordbox parser.

    Returns None if the filename isn't one pyrekordbox recognizes (caller
    falls through to the hand-parsed plausibility check) — never for a
    recognized-but-malformed file, which is reported as ``valid=False``
    instead of silently falling back.
    """
    if path.name not in KNOWN_SETTINGS_FILENAMES:
        return None

    report = SettingsFileReport(path=str(path), filename=path.name, present=True)
    try:
        from pyrekordbox.mysettings import read_mysetting_file
    except ImportError as exc:
        # A recognized filename with no pyrekordbox available must NOT
        # silently fall through to the hand-parsed header check — that
        # fallback is only for filenames pyrekordbox doesn't recognize at
        # all, and reporting valid=True from it here would misrepresent a
        # library-unavailable condition as a successful parse.
        report.readable = True
        report.valid = None
        report.parsed_via = "pyrekordbox"
        report.detail = f"pyrekordbox not available: {exc}"
        return report

    try:
        parsed = read_mysetting_file(path)
    except Exception as exc:  # pyrekordbox raises assorted parse errors, not one type
        report.readable = True
        report.valid = False
        report.parsed_via = "pyrekordbox"
        report.detail = f"pyrekordbox failed to parse: {exc}"
        return report

    report.readable = True
    report.valid = True
    report.parsed_via = "pyrekordbox"
    report.settings = {k: str(v) for k, v in dict(parsed).items()}
    report.detail = f"parsed via pyrekordbox — {len(report.settings)} settings"
    return report


def _hand_parse_header(path: Path) -> SettingsFileReport:
    """Structural plausibility check for a settings file pyrekordbox doesn't
    recognize. Does not decode individual settings — see module docstring."""
    report = SettingsFileReport(
        path=str(path), filename=path.name, present=True, parsed_via="hand-parsed header",
    )
    try:
        data = path.read_bytes()
    except OSError as exc:
        report.detail = f"unreadable: {exc}"
        return report
    report.readable = True

    min_size = 4 + _LEN_STRINGS_EXPECTED + 4 + 4 + 2
    if len(data) < min_size:
        report.valid = False
        report.detail = f"file too small for a settings header ({len(data)} bytes)"
        return report

    (len_strings,) = struct.unpack_from("<I", data, 0)
    if len_strings != _LEN_STRINGS_EXPECTED:
        report.valid = False
        report.detail = f"implausible len_strings {len_strings:#x} (expected {_LEN_STRINGS_EXPECTED:#x})"
        return report

    strings_block = data[4:4 + len_strings]
    brand = next((m.decode("ascii") for m in _BRAND_MARKERS if m in strings_block), None)
    has_rekordbox = b"rekordbox" in strings_block

    search_start = 4 + len_strings + 4  # skip past data_len
    window = data[search_start:search_start + 64]
    sentinel_pos = window.find(_SENTINEL)

    if brand is None or not has_rekordbox or sentinel_pos == -1:
        report.valid = False
        report.detail = (
            f"header missing expected markers (brand={brand!r}, "
            f"rekordbox={has_rekordbox}, sentinel_found={sentinel_pos != -1})"
        )
        return report

    entry_count_offset = search_start + sentinel_pos + len(_SENTINEL)
    if entry_count_offset + 4 <= len(data):
        (entry_count,) = struct.unpack_from("<I", data, entry_count_offset)
        report.entry_count = entry_count

    report.brand = brand
    report.valid = True
    report.detail = f"settings header OK (hand-parsed) — brand={brand!r}, entry_count={report.entry_count}"
    report.notes.append("structural plausibility only — individual settings not decoded")
    return report


def read_settings_file(path: PathLike) -> SettingsFileReport:
    """Parse one Pioneer settings ``.DAT`` file. Read-only.

    Prefers pyrekordbox's real parser for the four filenames it recognizes;
    falls back to a hand-parsed structural plausibility check otherwise.

    Parameters
    ----------
    path : str or Path

    Returns
    -------
    report : SettingsFileReport
    """
    p = Path(path)
    if not p.is_file():
        return SettingsFileReport(path=str(p), filename=p.name, present=False, detail="file not found")

    report = _read_via_pyrekordbox(p)
    if report is None:
        report = _hand_parse_header(p)

    logger.info(
        "Parsed settings file %s via %s: valid=%s",
        p.name, report.parsed_via or "none", report.valid,
    )
    return report


def read_settings_tree(pioneer_dir: PathLike) -> List[SettingsFileReport]:
    """Parse every known settings file found under a PIONEER/ dir.

    Parameters
    ----------
    pioneer_dir : str or Path
        The mount root or its ``PIONEER/`` directory — both the direct file
        and one level of ``PIONEER/`` nesting are checked, mirroring
        usb_inspector.py's ``SETTINGS_FILES`` relative paths.

    Returns
    -------
    reports : List[SettingsFileReport]
        One entry per known filename that exists on disk. Filenames that
        aren't present are omitted, not reported as failures — absence of a
        settings file is normal on export media that hasn't been used on a
        real player yet.
    """
    root = Path(pioneer_dir)
    reports: List[SettingsFileReport] = []
    for filename in KNOWN_SETTINGS_FILENAMES:
        for candidate in (root / filename, root / "PIONEER" / filename):
            if candidate.is_file():
                reports.append(read_settings_file(candidate))
                break
    return reports
