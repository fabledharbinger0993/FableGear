"""
fablegear_database.fingerprinter — the fingerprint edge of the Archive.

Wires the first producer→consumer edge of the tool graph and enforces the
persistence contract for it:

  • track tagger  → computes a Chromaprint fingerprint ONCE and persists it
                    into fg_content.acoustic_fingerprint (logged in
                    fg_processing_log).
  • duplicate     → reads fingerprints back from the Archive and only computes
    scanner         the misses, then groups by fingerprint — no full recompute.

Properties guaranteed here:
  • Read-first: get_unfingerprinted() is the work-list; anything already
    stored is never recomputed.
  • Persisted: every result is written to fg_content and logged.
  • Idempotent + resumable: re-running fingerprints only what is still
    missing, so an interrupted run resumes for free (the stored fingerprint
    IS the checkpoint).

The actual fingerprint function is injectable so this module stays importable
and unit-testable without fpcalc/audio; the default lazily reuses the existing
chop_shop.duplicate_detector.fingerprint_file (no parallel mechanism).
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .database import FableGearDatabase

log = logging.getLogger(__name__)


class LibraryFingerprinter:
    """Persists acoustic fingerprints into the Archive and reads them back."""

    def __init__(
        self,
        database: FableGearDatabase,
        fingerprint_fn: Optional[Callable[[Path], Optional[str]]] = None,
    ):
        """
        Args:
            database: FableGear database (the Archive)
            fingerprint_fn: path -> fingerprint str or None. Defaults to the
                canonical chop_shop.duplicate_detector.fingerprint_file
                (imported lazily). Injectable for testing.
        """
        self.database = database
        self._fingerprint_fn = fingerprint_fn

    def _fp(self, path: Path) -> Optional[str]:
        if self._fingerprint_fn is None:
            from chop_shop.duplicate_detector import fingerprint_file  # noqa: PLC0415
            self._fingerprint_fn = fingerprint_file
        return self._fingerprint_fn(path)

    def fingerprint_missing(
        self,
        limit: int = 1_000_000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Fingerprint every record that has no fingerprint yet, persisting each
        result and logging it. Safe to re-run and to interrupt.

        Returns stats: {missing, fingerprinted, failed, vanished}.
        """
        todo = self.database.get_unfingerprinted(limit=limit)
        stats = {"missing": len(todo), "fingerprinted": 0, "failed": 0, "vanished": 0}
        total = len(todo)

        for i, rec in enumerate(todo):
            path = Path(rec.file_path)
            try:
                if not path.exists():
                    stats["vanished"] += 1
                    self.database.log_operation(
                        "fingerprint", rec.file_path, status="vanished"
                    )
                    continue

                fp = self._fp(path)
                if fp:
                    self.database.update_content(rec.id, {
                        "acoustic_fingerprint": fp,
                        "fingerprint_quality": 100,
                        "processing_status": "fingerprinted",
                    })
                    self.database.log_operation("fingerprint", rec.file_path, status="ok")
                    stats["fingerprinted"] += 1
                else:
                    self.database.log_operation(
                        "fingerprint", rec.file_path, status="failed",
                        error_message="no fingerprint produced",
                    )
                    stats["failed"] += 1
            except Exception as exc:  # noqa: BLE001 — never abort the batch
                stats["failed"] += 1
                self.database.log_operation(
                    "fingerprint", rec.file_path, status="failed",
                    error_message=str(exc),
                )
                log.error("Fingerprint failed for %s: %s", rec.file_path, exc)
            finally:
                if progress_callback:
                    progress_callback(i + 1, total)

        log.info(
            "Fingerprint pass: %d fingerprinted, %d failed, %d vanished (of %d missing)",
            stats["fingerprinted"], stats["failed"], stats["vanished"], stats["missing"],
        )
        return stats

    def duplicate_groups(
        self,
        compute_missing: bool = True,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, List[List[int]]]:
        """
        Return duplicate groups read FROM the Archive.

        With compute_missing=True, any records still lacking a fingerprint are
        fingerprinted first (the misses only) — so this both consumes and
        contributes to the Archive. Groups are returned by exact content hash
        and by acoustic fingerprint.

        Returns: {"by_hash": [[id, ...], ...], "by_fingerprint": [[id, ...], ...]}
        """
        if compute_missing:
            self.fingerprint_missing(progress_callback=progress_callback)

        by_hash = [ids for _h, ids in self.database.find_duplicates_by_hash()]
        by_fp = [ids for _f, ids in self.database.find_duplicates_by_fingerprint()]
        return {"by_hash": by_hash, "by_fingerprint": by_fp}
