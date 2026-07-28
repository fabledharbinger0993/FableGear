# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-06-12
"""
fablegear / chop_shop/usb_inspector.py

Read-only inspector for Pioneer DJ export media. Reports which device
database formats are present on a mounted drive and whether each looks
structurally sound:

  - DeviceSQL  (``PIONEER/rekordbox/export.pdb``)      — CDJ-3000 era
  - OneLibrary (``exportLibrary.db``, SQLite)           — OMNIS-DUO era
  - ANLZ analysis tree (``PIONEER/USBANLZ/``)
  - Player settings files (``MYSETTING.DAT`` etc.)

This is Phase A of the dual-format export campaign (see
``docs/dual_format_export.md``): before FableGear can *write* either
format, it must be able to *recognize and validate* both. Probe first,
write later.

DeviceSQL header heuristics follow the community reverse engineering of
the PDB format (Deep Symmetry's crate-digger / rekordcrate). pyrekordbox
parses master.db, not export.pdb — the PDB checks here are deliberately
shallow (header plausibility only) and make no claim of full parsing.

Public interface:
    inspect_usb(mount_root) -> UsbInspectionReport
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

SQLITE_MAGIC = b"SQLite format 3\x00"

# Plausibility bounds for the DeviceSQL header (crate-digger reverse eng.):
# bytes 0-3 are zero, bytes 4-7 are the page length (LE u32, a power of
# two — 4096 on every export observed in the wild), bytes 8-11 the table
# count (small positive integer).
PDB_PAGE_SIZES = {512, 1024, 2048, 4096, 8192, 16384}
PDB_MAX_TABLES = 64

ONELIBRARY_CANDIDATES = (
    "PIONEER/rekordbox/exportLibrary.db",
    "PIONEER/exportLibrary.db",
    "rekordbox/exportLibrary.db",
    "exportLibrary.db",
)

SETTINGS_FILES = (
    "PIONEER/MYSETTING.DAT",
    "PIONEER/MYSETTING2.DAT",
    "PIONEER/DEVSETTING.DAT",
    "PIONEER/DJMMYSETTING.DAT",
)


class NotAMountError(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(f"Not a directory or not accessible: {path}")


@dataclass
class FormatFinding:
    """One database format's presence and validation state."""

    present: bool = False
    path: Optional[str] = None
    valid: Optional[bool] = None     # None = present but unverifiable
    detail: str = ""


@dataclass
class UsbInspectionReport:
    """Everything ``inspect_usb`` learned about a mounted drive."""

    mount: str = ""
    has_pioneer_dir: bool = False
    devicesql: FormatFinding = field(default_factory=FormatFinding)
    onelibrary: FormatFinding = field(default_factory=FormatFinding)
    anlz_track_count: int = 0
    settings_files: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def cdj3000_ready(self) -> bool:
        """True if the original CDJ-3000 fleet can read this stick."""
        return bool(self.devicesql.present and self.devicesql.valid)

    @property
    def onelibrary_ready(self) -> bool:
        """True if OneLibrary devices (e.g. OMNIS-DUO) can read this stick."""
        return bool(self.onelibrary.present and self.onelibrary.valid is not False)

    @property
    def dual_format(self) -> bool:
        return self.cdj3000_ready and self.onelibrary_ready


def _check_devicesql(pdb_path: Path) -> FormatFinding:
    """Validate an ``export.pdb`` header against known DeviceSQL structure.

    Parameters
    ----------
    pdb_path : Path
        Path to a candidate ``export.pdb`` file.

    Returns
    -------
    finding : FormatFinding
        Presence, header plausibility, and a human-readable detail line.
    """
    finding = FormatFinding(present=True, path=str(pdb_path))
    try:
        with open(pdb_path, "rb") as fh:
            header = fh.read(12)
    except OSError as exc:
        finding.valid = None
        finding.detail = f"present but unreadable: {exc}"
        return finding

    if len(header) < 12:
        finding.valid = False
        finding.detail = f"file too small for a PDB header ({len(header)} bytes)"
        return finding

    zero_field = header[0:4]
    len_page = int.from_bytes(header[4:8], "little")
    num_tables = int.from_bytes(header[8:12], "little")

    if zero_field != b"\x00\x00\x00\x00":
        finding.valid = False
        finding.detail = "header bytes 0-3 are not zero — not DeviceSQL"
    elif len_page not in PDB_PAGE_SIZES:
        finding.valid = False
        finding.detail = f"implausible page size {len_page}"
    elif not 0 < num_tables <= PDB_MAX_TABLES:
        finding.valid = False
        finding.detail = f"implausible table count {num_tables}"
    else:
        finding.valid = True
        finding.detail = (
            f"DeviceSQL header OK — page size {len_page}, {num_tables} tables, "
            f"{pdb_path.stat().st_size:,} bytes"
        )
    return finding


def _check_onelibrary(db_path: Path) -> FormatFinding:
    """Validate an ``exportLibrary.db`` candidate as readable SQLite.

    A non-SQLite (possibly encrypted) file is reported honestly as
    present-but-unverified rather than guessed at.
    """
    finding = FormatFinding(present=True, path=str(db_path))
    try:
        with open(db_path, "rb") as fh:
            magic = fh.read(16)
    except OSError as exc:
        finding.valid = None
        finding.detail = f"present but unreadable: {exc}"
        return finding

    if magic != SQLITE_MAGIC:
        finding.valid = None
        finding.detail = "present but not plain SQLite (possibly encrypted) — UNVERIFIED"
        return finding

    try:
        uri = f"file:{db_path}?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True)
        try:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        finally:
            con.close()
        names = [r[0] for r in rows]
        finding.valid = True
        finding.detail = (
            f"SQLite OK — {len(names)} tables"
            + (f" (e.g. {', '.join(names[:4])}…)" if names else "")
        )
    except sqlite3.Error as exc:
        finding.valid = False
        finding.detail = f"SQLite magic present but database unreadable: {exc}"
    return finding


def inspect_usb(mount_root: PathLike) -> UsbInspectionReport:
    """Inspect a mounted drive for Pioneer export formats. Read-only.

    Parameters
    ----------
    mount_root : str or Path
        The mount point of the drive to inspect (e.g. ``/Volumes/MYUSB``).

    Returns
    -------
    report : UsbInspectionReport
        Format findings, ANLZ count, settings presence, and notes.

    Examples
    --------
    >>> report = inspect_usb("/Volumes/DJ_USB")     # doctest: +SKIP
    >>> report.dual_format                            # doctest: +SKIP
    False
    """
    root = Path(mount_root)
    if not root.is_dir():
        raise NotAMountError(root)

    report = UsbInspectionReport(mount=str(root))
    pioneer = root / "PIONEER"
    report.has_pioneer_dir = pioneer.is_dir()
    if not report.has_pioneer_dir:
        report.notes.append("No PIONEER/ directory — not a rekordbox export stick.")

    # DeviceSQL
    pdb = pioneer / "rekordbox" / "export.pdb"
    if pdb.is_file():
        report.devicesql = _check_devicesql(pdb)
    else:
        report.devicesql.detail = "export.pdb not found"

    # OneLibrary — fixed candidates first, then a shallow recursive sweep
    for rel in ONELIBRARY_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            report.onelibrary = _check_onelibrary(candidate)
            break
    if not report.onelibrary.present:
        try:
            hits = [p for p in root.glob("*/**/exportLibrary.db") if p.is_file()]
        except OSError:
            hits = []
        if hits:
            report.onelibrary = _check_onelibrary(hits[0])
            report.notes.append(
                f"exportLibrary.db found at non-standard location: {hits[0]}"
            )
        else:
            report.onelibrary.detail = "exportLibrary.db not found"

    # ANLZ analysis tree
    usbanlz = pioneer / "USBANLZ"
    if usbanlz.is_dir():
        report.anlz_track_count = sum(
            1 for _ in usbanlz.glob("*/*/ANLZ0000.DAT")
        )

    # Settings files
    report.settings_files = [
        rel for rel in SETTINGS_FILES if (root / rel).is_file()
    ]

    logger.info(
        "Inspected %s: devicesql=%s onelibrary=%s anlz=%d",
        root, report.devicesql.valid, report.onelibrary.valid,
        report.anlz_track_count,
    )
    return report
