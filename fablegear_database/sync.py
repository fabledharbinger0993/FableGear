"""
fablegear_database.sync — Database/filesystem reconciliation.

Keeps the FableGear database aligned with what is actually on disk. A
reconcile pass:

- imports new files and re-imports changed ones (delegated to FileImporter,
  which already does scanner discovery + size/mtime change detection + a bulk
  upsert),
- detects files that moved — same content hash, new path — and *relinks* the
  existing record in place so its playlist/cue/fingerprint associations are
  preserved (this is the "never lose the connection to the file" guarantee),
- flags or removes records whose files are genuinely gone.

The scanner is injectable (via the importer) so this module stays testable
without the app config.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import ContentRecord, FableGearDatabase
from .importer import FileImporter

log = logging.getLogger(__name__)

_ALL = 1_000_000  # effectively-unbounded limit for "get everything"


class DatabaseSync:
    """Reconciles the database against the filesystem."""

    def __init__(
        self,
        database: FableGearDatabase,
        importer: Optional[FileImporter] = None,
        scanner_module: Optional[Any] = None,
    ):
        """
        Args:
            database: FableGear database instance
            importer: FileImporter to reuse (created if omitted)
            scanner_module: Scanner injected into the importer (for testing)
        """
        self.database = database
        self.importer = importer or FileImporter(database, scanner_module=scanner_module)

    def reconcile(
        self,
        root_paths: List[Path],
        *,
        remove_missing: bool = False,
        detect_moves: bool = True,
    ) -> Dict[str, Any]:
        """
        Bring the database in line with the filesystem under ``root_paths``.

        Args:
            root_paths: Directories to reconcile against
            remove_missing: Delete records whose files are gone (default: just
                flag them processing_status='missing')
            detect_moves: Relink records whose file moved instead of treating
                the move as a delete + add

        Returns:
            Dictionary with reconciliation statistics (including a ``moves``
            list of {id, from, to} for any relinked files)
        """
        # 1. New + changed files. The importer walks via the scanner, skips
        #    unchanged files, and writes everything in one bulk transaction.
        import_stats = self.importer.import_files([Path(p) for p in root_paths])

        # 2. Records whose file is no longer at the recorded path.
        stale = self.find_stale_records()

        moves: List[Dict[str, str]] = []
        if detect_moves and stale:
            moves = self._detect_moves(stale)
            moved_ids = {m["id"] for m in moves}
            stale = [r for r in stale if r.id not in moved_ids]

        removed = 0
        marked_missing = 0
        if remove_missing:
            for record in stale:
                if record.id is not None and self.database.delete_content(record.id):
                    self.database.log_operation(
                        "sync_remove", record.file_path, status="ok",
                    )
                    removed += 1
        else:
            for record in stale:
                if record.id is not None:
                    self.database.update_content(
                        record.id, {"processing_status": "missing"}
                    )
                    self.database.log_operation(
                        "sync_missing", record.file_path, status="ok",
                    )
                    marked_missing += 1

        for move in moves:
            self.database.log_operation(
                "sync_move", move["to"], status="ok",
                metadata={"from": move["from"], "record_id": move["id"]},
            )

        stats = {
            "imported_new": import_stats["new_files"],
            "imported_updated": import_stats["updated_files"],
            "skipped": import_stats["skipped_files"],
            "moved": len(moves),
            "missing": marked_missing,
            "removed": removed,
            "moves": moves,
            "errors": list(import_stats["errors"]),
        }

        self.database.log_operation(
            "sync_reconcile",
            metadata={
                "new": stats["imported_new"],
                "updated": stats["imported_updated"],
                "moved": stats["moved"],
                "missing": stats["missing"],
                "removed": stats["removed"],
                "roots": [str(p) for p in root_paths],
            },
        )

        log.info(
            "Reconcile: %d new, %d updated, %d moved, %d missing, %d removed",
            stats["imported_new"], stats["imported_updated"], stats["moved"],
            stats["missing"], stats["removed"],
        )
        return stats

    def _detect_moves(self, stale: List[ContentRecord]) -> List[Dict[str, str]]:
        """
        Relink stale records to files that moved (matched by content hash).

        After the import step a moved file has been re-imported as a fresh
        record at its new path. For each stale record we look for an on-disk
        record with the same hash; when found we delete that fresh duplicate
        and relink the original record onto the new path, so the original row
        (and everything attached to it) survives the move.
        """
        stale_ids = {r.id for r in stale}

        # On-disk, non-stale records grouped by hash — these are move targets.
        by_hash: Dict[str, List[ContentRecord]] = {}
        for record in self.database.get_all_content(limit=_ALL):
            if record.id in stale_ids:
                continue
            if record.file_hash and Path(record.file_path).exists():
                by_hash.setdefault(record.file_hash, []).append(record)

        moves: List[Dict[str, str]] = []
        for record in stale:
            if not record.file_hash:
                continue
            candidates = by_hash.get(record.file_hash)
            if not candidates:
                continue
            target = candidates.pop(0)  # consume so two files don't claim it
            old_path, new_path = record.file_path, target.file_path

            # Delete the duplicate first to free the UNIQUE(file_path) slot,
            # then move the original record onto the new path.
            if target.id is not None:
                self.database.delete_content(target.id)
            if record.id is not None:
                self.database.relink_content(record.id, new_path)
                moves.append({"id": record.id, "from": old_path, "to": new_path})

        return moves

    def find_stale_records(self) -> List[ContentRecord]:
        """Return records that reference a file which no longer exists."""
        stale = [
            record
            for record in self.database.get_all_content(limit=_ALL)
            if not Path(record.file_path).exists()
        ]
        log.info("Found %d stale database records", len(stale))
        return stale

    def find_orphaned_files(self, root_paths: List[Path]) -> List[Path]:
        """
        Return files present on disk but absent from the database.

        Args:
            root_paths: Directories to scan
        """
        scanner = self.importer._get_scanner()
        on_disk = set()
        for root in root_paths:
            root = Path(root)
            if not root.is_dir():
                continue
            for track in scanner.scan_directory(root):
                on_disk.add(str(track.path))

        known = set(self.database.get_path_index().keys())
        orphaned = sorted(on_disk - known)
        log.info("Found %d orphaned files", len(orphaned))
        return [Path(p) for p in orphaned]

    def cleanup_stale_records(self, dry_run: bool = False) -> int:
        """
        Delete records whose files no longer exist.

        Args:
            dry_run: If True, only count what would be deleted

        Returns:
            Number of records deleted (or that would be)
        """
        stale = self.find_stale_records()
        if dry_run:
            log.info("Would remove %d stale records (dry run)", len(stale))
            return len(stale)

        removed = 0
        for record in stale:
            if record.id is None:
                continue
            try:
                if self.database.delete_content(record.id):
                    self.database.log_operation(
                        "cleanup_stale", record.file_path, status="ok",
                    )
                    removed += 1
            except Exception as exc:
                log.error("Failed to remove stale record %s: %s", record.id, exc)

        if removed:
            self.database.log_operation(
                "cleanup_stale",
                metadata={"removed": removed, "dry_run": False},
            )

        log.info("Removed %d stale records", removed)
        return removed
