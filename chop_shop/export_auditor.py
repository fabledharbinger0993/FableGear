# -*- coding: utf-8 -*-
# Author: FableGear (Claude + Marshall Guthrie)
# Date:   2026-07-13
"""
fablegear / chop_shop/export_auditor.py

Read-only integration brain for Phase B ("deep read") of the dual-format
export campaign (see ``docs/dual_format_export.md``). Walks a mounted
``PIONEER/`` tree, calls the three deep-read parsers —
``anlz_reader``, ``pioneer_settings``, ``devicesql_reader`` — plus
``usb_inspector`` (Phase A), cross-checks the findings against each other
and against the FableGear archive, and persists a durable audit record
through ``FableGearDatabase.log_operation`` / ``bulk_log_operations``.

This module writes NOTHING to any Pioneer file or to rekordbox's database.
The only writes are to FableGear's own archive DB (operation logs), exactly
like every other chop_shop tool's ``archive=`` parameter.

Encryption note: ``exportLibrary.db`` (OneLibrary/OMNIS-DUO) is SQLCipher
with an unknown key, and ``ak.dat``/``nn.dat``/``gcred.dat`` are opaque
token blobs. This auditor DETECTS and REPORTS their presence/size only —
it never attempts decryption (see ``docs/dual_format_export.md`` §Encryption).

Public interface:
    audit_export(mount_root, archive=None) -> ExportAuditReport
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from anlz_reader import read_anlz_set, AnlzSetReport
from devicesql_reader import read_pdb, PdbReport
from pioneer_settings import read_settings_tree, SettingsFileReport
from usb_inspector import inspect_usb, UsbInspectionReport, NotAMountError

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Batch size for archive writes. Falls back to a sane default if config.py's
# system-tiered value isn't available in a given context — config.py raises
# (not just ImportError) when FableGear hasn't been through `cli.py setup`
# yet, which this read-only auditor should not require just to run its tests.
try:
    from config import ARCHIVE_CHUNK_SIZE as _ARCHIVE_CHUNK_SIZE
except Exception:  # noqa: BLE001 - config.py's unconfigured-state error isn't a fixed type
    _ARCHIVE_CHUNK_SIZE = 250

# Token blobs of unknown role — presence/size reported, never opened.
_OPAQUE_TOKEN_FILES = ("ak.dat", "nn.dat", "gcred.dat")


@dataclass
class AnlzAuditSummary:
    """Aggregate stats over every ANLZ set found under USBANLZ/."""
    tracks_scanned: int = 0
    dat_missing: int = 0
    with_ppth: int = 0
    with_beat_grid: int = 0
    with_waveform: int = 0
    total_beats: int = 0


@dataclass
class LibraryCrossMatch:
    """Best-effort cross-link between ANLZ-embedded paths and the archive.

    ANLZ ``PPTH`` paths are drive-relative (``/Contents/...``), not the
    archive's absolute ``file_path`` — an exact match only succeeds when the
    archive happens to store the same relative form, so a low match rate is
    expected and does not by itself indicate a problem. See
    ``docs/dual_format_export.md`` "This path rewrite is the crux."
    """
    anlz_tracks_with_path: int = 0
    matched_in_archive: int = 0
    note: str = "PPTH is drive-relative; exact match against archive.file_path is best-effort."


@dataclass
class EncryptionFinding:
    """Presence/size record for an opaque or encrypted file. Never decoded."""
    name: str
    present: bool
    path: Optional[str] = None
    size: Optional[int] = None
    note: str = ""


@dataclass
class ExportAuditReport:
    """Consolidated Phase B findings for one mounted export."""
    mount: str = ""
    usb_inspection: Optional[UsbInspectionReport] = None
    anlz_sets: List[AnlzSetReport] = field(default_factory=list)
    anlz_summary: AnlzAuditSummary = field(default_factory=AnlzAuditSummary)
    settings_files: List[SettingsFileReport] = field(default_factory=list)
    pdb_report: Optional[PdbReport] = None
    library_cross_match: LibraryCrossMatch = field(default_factory=LibraryCrossMatch)
    encryption_findings: List[EncryptionFinding] = field(default_factory=list)
    archive_logged: bool = False
    notes: List[str] = field(default_factory=list)


def _scan_anlz_tree(root: Path) -> List[AnlzSetReport]:
    """Walk PIONEER/USBANLZ/*/*/ for per-track ANLZ sets (same glob shape
    usb_inspector.py uses to count tracks)."""
    usbanlz = root / "PIONEER" / "USBANLZ"
    if not usbanlz.is_dir():
        return []
    sets: List[AnlzSetReport] = []
    for dat_path in sorted(usbanlz.glob("*/*/ANLZ0000.DAT")):
        sets.append(read_anlz_set(dat_path.parent))
    return sets


def _summarize_anlz(sets: List[AnlzSetReport]) -> AnlzAuditSummary:
    summary = AnlzAuditSummary(tracks_scanned=len(sets))
    for s in sets:
        if s.dat is None:
            summary.dat_missing += 1
            continue
        if s.track_path:
            summary.with_ppth += 1
        if s.beat_count:
            summary.with_beat_grid += 1
            summary.total_beats += s.beat_count
        if any(r is not None and r.has_waveform for r in (s.dat, s.ext, s.two_ex)):
            summary.with_waveform += 1
    return summary


def _find_encryption_artifacts(root: Path, usb_inspection: UsbInspectionReport) -> List[EncryptionFinding]:
    findings: List[EncryptionFinding] = []

    if usb_inspection.onelibrary.present:
        size = None
        if usb_inspection.onelibrary.path:
            try:
                size = Path(usb_inspection.onelibrary.path).stat().st_size
            except OSError as exc:
                logger.warning("Could not stat %s: %s", usb_inspection.onelibrary.path, exc)
        findings.append(EncryptionFinding(
            name="exportLibrary.db",
            present=True,
            path=usb_inspection.onelibrary.path,
            size=size,
            note=_onelibrary_note(usb_inspection.onelibrary.valid),
        ))

    # PIONEER/ is where these token files actually live on a real export —
    # scoping the glob there (falling back to the mount root only if PIONEER/
    # doesn't exist) avoids an unbounded recursive walk of the whole USB
    # stick. Only the first match is ever used, so stop at it instead of
    # materializing every hit.
    search_root = root / "PIONEER" if (root / "PIONEER").is_dir() else root
    for name in _OPAQUE_TOKEN_FILES:
        hit = next(search_root.glob(f"**/{name}"), None)
        if hit is not None:
            try:
                size = hit.stat().st_size
            except OSError as exc:
                logger.warning("Could not stat %s: %s", hit, exc)
                size = None
            findings.append(EncryptionFinding(
                name=name, present=True, path=str(hit), size=size,
                note="opaque base64/binary token blob of unknown role — contents NOT read.",
            ))
        else:
            findings.append(EncryptionFinding(name=name, present=False, note="not found"))

    return findings


def _onelibrary_note(valid: Optional[bool]) -> str:
    if valid is None:
        return "SQLCipher-suspected (not plain SQLite) — decryption NOT attempted."
    if valid is False:
        return "present but failed validation for a reason other than encryption (see usb_inspector detail)."
    return "readable SQLite — not encrypted."


def _cross_match_library(anlz_sets: List[AnlzSetReport], archive) -> LibraryCrossMatch:
    match = LibraryCrossMatch()
    if archive is None:
        return match
    for s in anlz_sets:
        path = s.track_path
        if not path:
            continue
        match.anlz_tracks_with_path += 1
        try:
            record = archive.get_content_by_path(path)
        except Exception as exc:
            logger.warning("Archive lookup failed for %s: %s", path, exc)
            continue
        if record is not None:
            match.matched_in_archive += 1
    return match


def _persist(report: ExportAuditReport, archive) -> None:
    """Write the audit findings through the archive's audit log.

    Follows relocator.py's batching pattern: chunked bulk_log_operations for
    the per-track rows, then one summary row via log_operation.
    """
    if archive is None:
        return

    anlz_ops = []
    for s in report.anlz_sets:
        status = "ok" if s.dat is not None else "error"
        anlz_ops.append({
            "operation_type": "anlz_read",
            "file_path": s.anlz_dir,
            "status": status,
            "metadata": {
                "track_path": s.track_path,
                "beat_count": s.beat_count,
                "tags_present": {
                    "dat": s.dat.tags_present if s.dat else [],
                    "ext": s.ext.tags_present if s.ext else [],
                    "two_ex": s.two_ex.tags_present if s.two_ex else [],
                },
            },
        })
    if anlz_ops:
        archive.bulk_log_operations(anlz_ops, chunk_size=_ARCHIVE_CHUNK_SIZE)

    settings_ops = [
        {
            "operation_type": "settings_read",
            "file_path": sf.path,
            # valid=None (present but unverifiable/unreadable) is distinct
            # from valid=False (verified invalid) — collapsing both into
            # "error" would lose the same distinction the CLI's "⚠" mark
            # makes, so it gets its own "unknown" status here.
            "status": "ok" if sf.valid else ("unknown" if sf.valid is None else "error"),
            "metadata": {"parsed_via": sf.parsed_via, "brand": sf.brand, "detail": sf.detail},
        }
        for sf in report.settings_files
    ]
    if settings_ops:
        archive.bulk_log_operations(settings_ops, chunk_size=_ARCHIVE_CHUNK_SIZE)

    if report.pdb_report is not None:
        archive.log_operation(
            "pdb_read",
            file_path=report.pdb_report.path,
            status="ok" if report.pdb_report.valid_header else "error",
            metadata={
                "page_size": report.pdb_report.page_size,
                "num_tables": report.pdb_report.num_tables,
                "partial": report.pdb_report.partial,
                "detail": report.pdb_report.detail,
            },
        )

    archive.log_operation(
        "export_audit",
        file_path=report.mount,
        status="ok",
        metadata={
            "dual_format": report.usb_inspection.dual_format if report.usb_inspection else None,
            "anlz_tracks_scanned": report.anlz_summary.tracks_scanned,
            "anlz_with_beat_grid": report.anlz_summary.with_beat_grid,
            "anlz_with_waveform": report.anlz_summary.with_waveform,
            "settings_files_found": len(report.settings_files),
            "pdb_present": report.pdb_report is not None,
            "pdb_valid_header": report.pdb_report.valid_header if report.pdb_report else None,
            "library_cross_match": {
                "anlz_tracks_with_path": report.library_cross_match.anlz_tracks_with_path,
                "matched_in_archive": report.library_cross_match.matched_in_archive,
            },
            "encryption_findings": [
                {"name": f.name, "present": f.present, "size": f.size, "path": f.path, "note": f.note}
                for f in report.encryption_findings
            ],
        },
    )
    report.archive_logged = True


def audit_export(mount_root: PathLike, archive=None) -> ExportAuditReport:
    """Audit a mounted Pioneer export tree. Read-only w.r.t. the export media.

    Parameters
    ----------
    mount_root : str or Path
        Mount point of the drive to audit (e.g. ``/Volumes/GIGSTICK``).
    archive : FableGearDatabase, optional
        When provided, findings are persisted via ``log_operation`` /
        ``bulk_log_operations``. When ``None``, the audit still runs and
        returns a full report — it just isn't recorded (same convention as
        every other chop_shop tool's ``archive=`` parameter).

    Returns
    -------
    report : ExportAuditReport

    Raises
    ------
    NotAMountError
        If ``mount_root`` is not a directory (propagated from
        ``usb_inspector.inspect_usb``).
    """
    root = Path(mount_root)
    usb_inspection = inspect_usb(root)  # raises NotAMountError if invalid

    report = ExportAuditReport(mount=str(root), usb_inspection=usb_inspection)

    report.anlz_sets = _scan_anlz_tree(root)
    report.anlz_summary = _summarize_anlz(report.anlz_sets)

    report.settings_files = read_settings_tree(root)

    pdb_path = root / "PIONEER" / "rekordbox" / "export.pdb"
    if pdb_path.is_file():
        report.pdb_report = read_pdb(pdb_path)
        if report.pdb_report.partial:
            report.notes.append(
                "PDB track row extraction failed for this file — see devicesql_reader.py HONESTY LIMIT."
            )

    report.encryption_findings = _find_encryption_artifacts(root, usb_inspection)
    report.library_cross_match = _cross_match_library(report.anlz_sets, archive)

    if archive is None:
        report.notes.append("archive not provided — findings were not persisted")
    else:
        _persist(report, archive)

    logger.info(
        "Export audit complete: %s — %d ANLZ sets, %d settings files, pdb=%s, archive_logged=%s",
        root, len(report.anlz_sets), len(report.settings_files),
        bool(report.pdb_report), report.archive_logged,
    )
    return report
