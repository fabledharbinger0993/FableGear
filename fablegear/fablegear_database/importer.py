"""
fablegear_database.importer — File import and indexing system.

Bridges the filesystem and the FableGear database. Walking and metadata
extraction are delegated to the app's canonical ``scanner`` module (the same
multi-format extractor — ID3, Vorbis, MP4 atoms, damaged-MP3 recovery — used
everywhere else), so the importer never has to reimplement tag parsing.

Design notes:
- Files are written in batches of ``_COMMIT_BATCH_SIZE`` as the scan
  progresses, rather than one bulk transaction after everything has been
  discovered. A crash, rate limit, or force-quit partway through a long
  import loses at most the current batch, not the whole run — verified
  against real rekordbox behavior (which commits every track immediately;
  batching here trades a little of that immediacy for far fewer fsyncs on
  a library-sized scan).
- Change detection (file size + mtime) lets a re-scan skip unchanged files
  without re-hashing them.
- ``scanner`` is imported lazily / injectable so this module (and the
  ``fablegear_database`` package) stays importable without the app config.
"""

import hashlib
import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .database import ContentRecord, FableGearDatabase

log = logging.getLogger(__name__)

# Error substrings (emitted by scanner.extract_metadata) that indicate a file
# we could not actually read — as opposed to a merely tag-less file.
_CORRUPT_ERROR_MARKERS = ("mutagen open failed", "stat failed", "returned None")

# How many records accumulate before a bulk_upsert_content() flush. Small
# enough that a crash mid-import loses a bounded slice of work, large enough
# that a library-sized scan isn't paying a full fsync per file.
_COMMIT_BATCH_SIZE = 200


def _json_safe_text(value: object) -> str:
    """Coerce to a string that survives Flask's ensure_ascii JSON encoding.

    Filenames on non-UTF-8 volumes get decoded with surrogateescape, which
    embeds lone surrogate codepoints in the resulting str. Those raise
    UnicodeEncodeError deep inside json.dumps the moment this text reaches
    a jsonify()'d response (e.g. the onboarding import-status poll), which
    takes down that endpoint with no indication of why. Scrub before it
    lands in stats["errors"].
    """
    return str(value).encode("utf-8", "replace").decode("utf-8")


class FileImporter:
    """
    Imports physical audio files into the FableGear database.

    Uses the canonical scanner for discovery + metadata, computes a content
    hash for change detection and duplicate finding, and writes everything in
    a single bulk transaction.
    """

    def __init__(
        self,
        database: FableGearDatabase,
        scanner_module: Any | None = None,
    ):
        """
        Initialize the file importer.

        Args:
            database: FableGear database instance
            scanner_module: Object exposing ``scan_directory(root) -> iter`` of
                TrackInfo-like records. Defaults to the app's ``scanner`` module
                (imported lazily on first use). Injectable for testing.
        """
        self.database = database
        self._scanner = scanner_module

    def _get_scanner(self) -> Any:
        """Lazily import the app scanner module if one was not injected."""
        if self._scanner is None:
            import scanner as _scanner
            self._scanner = _scanner
        return self._scanner

    def import_files(
        self,
        root_paths: list[Path],
        progress_callback: Callable[[int, int], None] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        Import audio files from the given root directories.

        Args:
            root_paths: Directories to scan
            progress_callback: Optional callback(processed, total)
            force_refresh: Re-import every file even if unchanged

        Returns:
            Dictionary with import statistics
        """
        stats: dict[str, Any] = {
            "total_files": 0,
            "new_files": 0,
            "updated_files": 0,
            "skipped_files": 0,
            "error_files": 0,
            "errors": [],
        }

        scanner = self._get_scanner()

        valid_roots: list[Path] = []
        for root in root_paths:
            root = Path(root)
            if not root.is_dir():
                log.warning("Root path is not a directory: %s", root)
                continue
            valid_roots.append(root)

        # Cheap pre-count (no metadata extraction) so callers get a real
        # X / total from the first progress tick, rather than only finding
        # out the total once the whole scan has already finished.
        total = sum(scanner.count_scannable_files(root) for root in valid_roots)
        stats["total_files"] = total
        log.info("Found %d audio files to process", total)

        def _track_stream():
            for root in valid_roots:
                yield from scanner.scan_directory(root)

        return self._import_tracks(
            _track_stream(), stats, total,
            progress_callback=progress_callback,
            force_refresh=force_refresh,
            source_label=[str(p) for p in root_paths],
        )

    def import_paths(
        self,
        file_paths: list[Path],
        progress_callback: Callable[[int, int], None] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        Import specific audio files (not directories) into the database.

        Used by the Record Room's drag-to-import: the split view hands over
        individual novelty tracks rather than a scan root.
        """
        stats: dict[str, Any] = {
            "total_files": 0,
            "new_files": 0,
            "updated_files": 0,
            "skipped_files": 0,
            "error_files": 0,
            "errors": [],
        }

        scanner = self._get_scanner()
        audio_exts = getattr(scanner, "AUDIO_EXTENSIONS", None) or set()
        tracks = []
        for fp in file_paths:
            fp = Path(fp)
            if not fp.is_file():
                stats["error_files"] += 1
                stats["errors"].append(_json_safe_text(f"{fp}: not a file"))
                continue
            if audio_exts and fp.suffix.lower() not in audio_exts:
                stats["error_files"] += 1
                stats["errors"].append(_json_safe_text(f"{fp}: not an audio file"))
                continue
            try:
                tracks.append(scanner.extract_metadata(fp))
            except Exception as exc:
                stats["error_files"] += 1
                stats["errors"].append(_json_safe_text(f"{fp}: {exc}"))

        stats["total_files"] = len(file_paths)
        return self._import_tracks(
            tracks, stats, len(file_paths),
            progress_callback=progress_callback,
            force_refresh=force_refresh,
            source_label=[str(p) for p in file_paths],
        )

    def _import_tracks(
        self,
        tracks: Iterable[Any],
        stats: dict[str, Any],
        total: int,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        force_refresh: bool = False,
        source_label: list[str] | None = None,
    ) -> dict[str, Any]:
        """Shared pipeline: change-detect, hash, upsert in batches, log.

        ``tracks`` may be a one-shot generator (import_files streams straight
        off scan_directory) — accumulated records are flushed to the database
        every _COMMIT_BATCH_SIZE instead of once at the very end, so a crash
        or interruption partway through only loses the current batch.
        """
        # One query for change detection instead of a lookup per file.
        existing = self.database.get_path_index()

        batch: list[ContentRecord] = []
        processed = 0
        for track in tracks:
            processed += 1
            try:
                path = Path(track.path)
                path_str = str(path)
                stat = path.stat()
                size = track.file_size if track.file_size is not None else stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()

                prev = existing.get(path_str)
                if prev is not None and not force_refresh:
                    prev_size, prev_mtime, _prev_hash = prev
                    if prev_size == size and prev_mtime == mtime:
                        stats["skipped_files"] += 1
                        continue

                file_hash = self._compute_file_hash(path)
                record = self._track_to_record(track, size, mtime, file_hash)
                batch.append(record)

                if prev is not None:
                    stats["updated_files"] += 1
                else:
                    stats["new_files"] += 1

                if len(batch) >= _COMMIT_BATCH_SIZE:
                    self.database.bulk_upsert_content(batch)
                    batch = []

            except Exception as exc:
                stats["error_files"] += 1
                stats["errors"].append(_json_safe_text(f"{getattr(track, 'path', '?')}: {exc}"))
                log.error("Failed to import %s: %s", getattr(track, "path", "?"), exc)
            finally:
                if progress_callback:
                    progress_callback(processed, total)

        if batch:
            self.database.bulk_upsert_content(batch)

        self.database.log_operation(
            "import",
            metadata={
                "new": stats["new_files"],
                "updated": stats["updated_files"],
                "skipped": stats["skipped_files"],
                "errors": stats["error_files"],
                "roots": source_label or [],
            },
        )

        log.info(
            "Import complete: %d new, %d updated, %d skipped, %d errors",
            stats["new_files"], stats["updated_files"],
            stats["skipped_files"], stats["error_files"],
        )
        return stats

    def _track_to_record(
        self,
        track: Any,
        size: int,
        mtime: str,
        file_hash: str,
    ) -> ContentRecord:
        """Map a scanner TrackInfo onto a database ContentRecord."""
        path = Path(track.path)
        file_type = getattr(track, "file_type", None) or path.suffix.lstrip(".")

        return ContentRecord(
            file_path=str(path),
            file_name=path.name,
            file_size=size,
            modified_date=mtime,
            format=(file_type or "").lower() or None,
            title=getattr(track, "title", None),
            artist=getattr(track, "artist", None),
            album=getattr(track, "album", None),
            genre=getattr(track, "genre", None),
            year=getattr(track, "year", None),
            track_number=getattr(track, "track_number", None),
            # FableGear DB stores raw float BPM; Rekordbox stores ×100 int. Cross-DB code must transform.
            bpm=getattr(track, "bpm", None),
            key=getattr(track, "key", None),
            duration=getattr(track, "duration_seconds", None),
            bit_rate=getattr(track, "bitrate", None),
            sample_rate=getattr(track, "sample_rate", None),
            drive=self._drive_for_path(path),
            file_hash=file_hash,
            last_scanned=datetime.now().isoformat(),
            is_corrupted=self._is_corrupt(getattr(track, "errors", None)),
            processing_status="scanned",
        )

    @staticmethod
    def _drive_for_path(path: Path) -> str:
        """
        Return a stable drive identifier for a path.

        On macOS external media live under /Volumes/<NAME>/..., so the drive is
        the volume name — not the literal string "Volumes" (the previous bug,
        which collapsed every external drive into one identifier). Anything not
        under /Volumes is treated as the internal/local drive.
        """
        parts = Path(path).parts
        if len(parts) >= 3 and parts[1] == "Volumes":
            return parts[2]
        return "local"

    @staticmethod
    def _is_corrupt(errors: list[str] | None) -> bool:
        """True only for read failures, not merely missing tags."""
        if not errors:
            return False
        return any(
            marker in err
            for err in errors
            for marker in _CORRUPT_ERROR_MARKERS
        )

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute the SHA-256 hash of a file, streamed in chunks."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as exc:
            log.error("Failed to compute hash for %s: %s", file_path, exc)
            return ""

    def update_fingerprint(
        self, file_path: Path, fingerprint: str, quality: int = 100
    ) -> bool:
        """
        Attach an acoustic fingerprint to an already-imported file.

        Args:
            file_path: File to update
            fingerprint: Acoustic fingerprint string
            quality: Fingerprint quality score

        Returns:
            True if a row was updated
        """
        try:
            record = self.database.get_content_by_path(str(file_path))
            if record and record.id is not None:
                return self.database.update_content(
                    record.id,
                    {
                        "acoustic_fingerprint": fingerprint,
                        "fingerprint_quality": quality,
                        "processing_status": "fingerprinted",
                    },
                )
            return False
        except Exception as exc:
            log.error("Failed to update fingerprint for %s: %s", file_path, exc)
            return False
