# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-07-13
"""
fablegear / chop_shop/devicesql_reader.py

Read-only reader for the DeviceSQL ``export.pdb`` / ``exportExt.pdb``
database (CDJ-3000 era). Phase B ("deep read") of the dual-format export
campaign — usb_inspector.py (Phase A) only validates 12 bytes of header
plausibility; this module parses the full 16-byte header, including
``next_unused_page``.

Header layout (DEMONSTRATED — matches usb_inspector.py's
``_check_devicesql`` and the real numbers recorded in
``docs/format_samples/DJMTGO_inspection.md``: page size 4096, 20 tables,
610,304 bytes):

    4 zero bytes + page_size:u32le + num_tables:u32le + next_unused_page:u32le

SCOPE LIMIT — read this before trusting ``PdbReport.tracks``:
DeviceSQL's page/row-group/string-table format beyond the 16-byte header
(table pointer array entry layout, page header layout, row-group bitmask,
string encoding within a row) is NOT byte-verified against any fixture
committed to this repo — ``docs/format_samples/DJMTGO_inspection.md``
documents only the header numbers, and no ``export.pdb`` binary is
committed under ``docs/format_samples/``. Per the campaign doc's Phase B
carve-out ("if a full page walk is too large this session, ship a
documented partial ... mark the rest UNVERIFIED"), this module intentionally
does NOT hand-roll a page/row walker from unverified memory of the
crate-digger/rekordcrate format — doing so risked presenting guessed byte
offsets as fact. It validates the header only; ``PdbReport.tracks`` is
always empty and ``PdbReport.partial`` is always True until a real
``export.pdb`` sample lands in this repo and a byte-verified row walker can
be built and tested against it.

Public interface:
    read_pdb(path) -> PdbReport
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

PDB_PAGE_SIZES = {512, 1024, 2048, 4096, 8192, 16384}
PDB_MAX_TABLES = 64


@dataclass
class TrackRow:
    """One recovered track<->ANLZ mapping row.

    Not yet populated by this module (see module docstring SCOPE LIMIT) —
    the shape is defined now so export_auditor.py and callers have a stable
    contract to code against once row-walking lands.
    """
    anlz_folder_path: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    drive_relative_path: Optional[str] = None
    bpm: Optional[float] = None
    key: Optional[str] = None


@dataclass
class PdbReport:
    """Everything ``read_pdb`` learned about one DeviceSQL database file."""
    path: str = ""
    exists: bool = False
    readable: bool = False
    file_size: int = 0
    valid_header: Optional[bool] = None
    page_size: Optional[int] = None
    num_tables: Optional[int] = None
    next_unused_page: Optional[int] = None
    tracks: List[TrackRow] = field(default_factory=list)
    partial: bool = True
    detail: str = ""
    notes: List[str] = field(default_factory=list)


def read_pdb(path: PathLike) -> PdbReport:
    """Validate a DeviceSQL ``export.pdb``/``exportExt.pdb`` header. Read-only.

    Parameters
    ----------
    path : str or Path

    Returns
    -------
    report : PdbReport
        Header fields and validity. ``tracks`` is always empty and
        ``partial`` always True in this Phase B build — see module
        docstring SCOPE LIMIT.
    """
    p = Path(path)
    report = PdbReport(path=str(p))
    if not p.is_file():
        report.detail = "file not found"
        return report
    report.exists = True
    report.file_size = p.stat().st_size

    try:
        with open(p, "rb") as fh:
            header = fh.read(16)
    except OSError as exc:
        report.detail = f"unreadable: {exc}"
        return report
    report.readable = True

    if len(header) < 16:
        report.valid_header = False
        report.detail = f"file too small for a PDB header ({len(header)} bytes)"
        return report

    zero_field = header[0:4]
    page_size = int.from_bytes(header[4:8], "little")
    num_tables = int.from_bytes(header[8:12], "little")
    next_unused_page = int.from_bytes(header[12:16], "little")

    if zero_field != b"\x00\x00\x00\x00":
        report.valid_header = False
        report.detail = "header bytes 0-3 are not zero — not DeviceSQL"
        return report
    if page_size not in PDB_PAGE_SIZES:
        report.valid_header = False
        report.detail = f"implausible page size {page_size}"
        return report
    if not 0 < num_tables <= PDB_MAX_TABLES:
        report.valid_header = False
        report.detail = f"implausible table count {num_tables}"
        return report

    report.valid_header = True
    report.page_size = page_size
    report.num_tables = num_tables
    report.next_unused_page = next_unused_page
    report.detail = (
        f"DeviceSQL header OK — page size {page_size}, {num_tables} tables, "
        f"next_unused_page {next_unused_page}, {report.file_size:,} bytes"
    )
    report.notes.append(
        "track/ANLZ-folder row mapping NOT extracted this phase — page/row-group "
        "layout beyond the header is unverified against any fixture in this repo "
        "(see module docstring). PdbReport.tracks is an empty stub."
    )

    logger.info(
        "Parsed PDB header %s: page_size=%d num_tables=%d next_unused_page=%d",
        p.name, page_size, num_tables, next_unused_page,
    )
    return report
