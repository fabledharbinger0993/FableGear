"""
library_browser.local_view — Filesystem-centric library view.

Displays all audio files from connected drives with flexible sorting
and organization options.
"""

import logging
from pathlib import Path
from typing import Any

from library_browser.core import FileData

log = logging.getLogger(__name__)


class LocalView:
    """
    Filesystem-centric library view.

    Shows all audio files from connected drives in a shallow display
    with flexible sorting and organization options.
    """

    def __init__(self, database: Any | None = None):
        """
        Initialize the Local view.

        Args:
            database: Optional FableGearDatabase. When provided, the view reads
                from the database (database-first / instant) instead of
                re-scanning the filesystem.
        """
        self._database = database
        self._files: list[FileData] = []
        self._filtered_files: list[FileData] = []
        self._filters: dict[str, Any] = {}
        self._sort_field = "file_name"
        self._sort_ascending = True
        self._roots: list[Path] = []

    def load_from_database(self, limit: int = 1_000_000, offset: int = 0) -> None:
        """
        Populate the view directly from the FableGear database.

        This is the database-first path: no filesystem walk, no re-extraction
        of tags — every field is read from the rows the importer already wrote.

        Args:
            limit: Maximum number of records to load
            offset: Number of records to skip
        """
        if self._database is None:
            raise ValueError("LocalView has no database; pass one to load_from_database")

        records = self._database.get_all_content(
            limit=limit, offset=offset, order_by="file_name"
        )
        self._files = [self._record_to_file_data(rec) for rec in records]
        self._apply_filters_and_sort()
        log.info("Loaded %d files from database", len(self._files))

    @staticmethod
    def _record_to_file_data(rec: Any) -> FileData:
        """Map a database ContentRecord onto a FileData for display."""
        return FileData(
            file_path=Path(rec.file_path),
            file_name=rec.file_name,
            file_size=rec.file_size or 0,
            duration=rec.duration,
            format=rec.format,
            bit_rate=rec.bit_rate,
            sample_rate=rec.sample_rate,
            modified_date=rec.modified_date,
            artist=rec.artist,
            album=rec.album,
            title=rec.title,
            bpm=rec.bpm,
            key=rec.key,
            in_rekordbox=bool(rec.in_rekordbox),
            drive=rec.drive,
        )

    def load_data(self, roots: list[Path]) -> None:
        """
        Load file data from filesystem.

        Args:
            roots: Root paths to scan
        """
        try:
            from library_browser.scanner import LibraryScanner

            self._roots = roots
            scanner = LibraryScanner()

            file_paths = scanner.scan_local_files(roots)

            self._files = []
            for file_path in file_paths:
                metadata = scanner.get_file_metadata(file_path)

                file_data = FileData(
                    file_path=file_path,
                    file_name=metadata.get("file_name", file_path.name),
                    file_size=metadata.get("file_size", 0),
                    duration=metadata.get("duration"),
                    format=file_path.suffix.lower().replace(".", ""),
                    bit_rate=metadata.get("bit_rate"),
                    sample_rate=metadata.get("sample_rate"),
                    modified_date=metadata.get("modified_date"),
                    artist=metadata.get("artist"),
                    album=metadata.get("album"),
                    title=metadata.get("title"),
                    bpm=metadata.get("bpm"),
                    key=metadata.get("key"),
                    drive=self._get_drive_identifier(file_path),
                )

                # Check if file is in Rekordbox
                file_data.in_rekordbox = self._check_in_rekordbox(file_path)

                self._files.append(file_data)

            self._apply_filters_and_sort()
            log.info("Loaded %d files from %d roots", len(self._files), len(roots))

        except Exception as exc:
            log.error("Failed to load Local view data: %s", exc)

    def get_files(self, limit: int = 1000, offset: int = 0) -> list[FileData]:
        """
        Get files from the view.

        Args:
            limit: Maximum number of files to return
            offset: Number of files to skip

        Returns:
            List of file data
        """
        return self._filtered_files[offset:offset + limit]

    def search(self, query: str, search_fields: list[str] | None = None) -> list[FileData]:
        """
        Search files by query string.

        Args:
            query: Search query
            search_fields: Fields to search in (None = all fields)

        Returns:
            List of matching files
        """
        query_lower = query.lower()

        if not search_fields:
            search_fields = ["file_name", "artist", "album", "title"]

        results = []
        for file in self._files:
            for field in search_fields:
                value = getattr(file, field, "")
                if value and query_lower in str(value).lower():
                    results.append(file)
                    break

        return results

    def sort(self, field: str, ascending: bool = True) -> None:
        """
        Sort files by the specified field.

        Args:
            field: Field to sort by
            ascending: Sort direction
        """
        self._sort_field = field
        self._sort_ascending = ascending
        self._apply_filters_and_sort()

    def filter(self, filters: dict[str, Any]) -> None:
        """
        Apply filters to the view.

        Args:
            filters: Dictionary of field:value filters
        """
        self._filters = filters
        self._apply_filters_and_sort()

    def refresh(self) -> None:
        """Refresh the view data."""
        if self._roots:
            self.load_data(self._roots)

    def get_statistics(self) -> dict[str, Any]:
        """
        Get view statistics.

        Returns:
            Dictionary with view statistics
        """
        total_files = len(self._files)
        in_rekordbox = sum(1 for f in self._files if f.in_rekordbox)
        not_in_rekordbox = total_files - in_rekordbox

        # Count by format
        format_counts = {}
        for file in self._files:
            fmt = file.format or "unknown"
            format_counts[fmt] = format_counts.get(fmt, 0) + 1

        # Count by drive
        drive_counts = {}
        for file in self._files:
            drive = file.drive or "unknown"
            drive_counts[drive] = drive_counts.get(drive, 0) + 1

        return {
            "total_files": total_files,
            "in_rekordbox": in_rekordbox,
            "not_in_rekordbox": not_in_rekordbox,
            "format_counts": format_counts,
            "drive_counts": drive_counts,
        }

    def _get_drive_identifier(self, file_path: Path) -> str:
        """
        Get a drive identifier for a file path.

        Args:
            file_path: File path

        Returns:
            Drive identifier string
        """
        try:
            # On macOS, get the volume name
            parts = file_path.parts
            if len(parts) >= 2 and parts[0] == "/":
                return parts[1]  # e.g., "Volumes", "Music"
            return "local"
        except Exception:
            # Path.parts is a pure in-memory property and shouldn't raise;
            # this is just a defensive fallback for unexpected path shapes.
            return "unknown"

    def _check_in_rekordbox(self, file_path: Path) -> bool:
        """
        Check if a file is in the Rekordbox database.

        Args:
            file_path: File path to check

        Returns:
            True if file is in Rekordbox
        """
        try:
            from db_connection import read_db

            with read_db() as db:
                # Check if there's a content entry with this path
                rows = db.get_content(FolderPath=str(file_path)).all()
                return len(rows) > 0

        except Exception as exc:
            # Called per-file over the whole library, so this stays broad
            # and logs at debug (not warning/error) to avoid flooding the
            # log if Rekordbox's db is briefly locked/unavailable. Note the
            # caveat: a failed check here is indistinguishable from a
            # genuine "not in Rekordbox" to callers (in_rekordbox=False),
            # so a systemic failure (e.g. db locked for the whole scan)
            # would silently mislabel the entire library as out of sync.
            log.debug("Rekordbox lookup failed for %s: %s", file_path, exc)
            return False

    def _apply_filters_and_sort(self) -> None:
        """Apply current filters and sorting to the file list."""
        # Apply filters
        self._filtered_files = self._files.copy()

        for field, value in self._filters.items():
            self._filtered_files = [
                f for f in self._filtered_files
                if getattr(f, field, None) == value
            ]

        # Apply sorting
        reverse = not self._sort_ascending
        self._filtered_files.sort(
            key=lambda f: getattr(f, self._sort_field, ""),
            reverse=reverse
        )
