"""
library_browser.integrated_view — Three-column integrated library view.

Provides a three-column layout comparing Rekordbox database entries
with local filesystem state for visual sync operations.
"""

import logging
from pathlib import Path
from typing import Any

from library_browser.core import IntegratedData

log = logging.getLogger(__name__)


class IntegratedView:
    """
    Three-column integrated library view.

    Shows Rekordbox database paths, linked local files, and orphan
    local files in a three-column layout for sync operations.
    """

    def __init__(self):
        """Initialize the Integrated view."""
        self._integrated_data: list[IntegratedData] = []
        self._filtered_data: list[IntegratedData] = []
        self._filters: dict[str, Any] = {}
        self._sort_field = "rekordbox_path"
        self._sort_ascending = True
        self._roots: list[Path] = []

    def load_data(self, roots: list[Path]) -> None:
        """
        Load integrated view data from database and filesystem.

        Args:
            roots: Root paths to scan
        """
        try:
            from library_browser.local_view import LocalView
            from library_browser.rekordbox_view import RekordboxView
            from library_browser.scanner import LibraryScanner

            self._roots = roots
            LibraryScanner()

            # Load Rekordbox data
            rekordbox_view = RekordboxView()
            rekordbox_view.load_data()
            rekordbox_tracks = rekordbox_view.get_tracks(limit=10000)  # Get all tracks

            # Load local file data
            local_view = LocalView()
            local_view.load_data(roots)
            local_files = local_view.get_files(limit=10000)  # Get all files

            # Create path lookup for local files
            local_file_map = {str(f.file_path): f for f in local_files}

            # Build integrated data
            self._integrated_data = []

            # Group Rekordbox tracks by folder path
            rekordbox_path_map = {}
            for track in rekordbox_tracks:
                folder_path = str(track.file_path.parent)
                if folder_path not in rekordbox_path_map:
                    rekordbox_path_map[folder_path] = []
                rekordbox_path_map[folder_path].append(track)

            # Process each Rekordbox path
            for rekordbox_path, tracks in rekordbox_path_map.items():
                integrated_entry = IntegratedData(
                    rekordbox_path=rekordbox_path,
                )

                # Find matching local files
                local_files = []
                for track in tracks:
                    file_path = str(track.file_path)
                    if file_path in local_file_map:
                        local_files.append(local_file_map[file_path])

                integrated_entry.local_files = local_files

                # Determine sync status
                if len(local_files) == len(tracks):
                    integrated_entry.sync_status = "synced"
                elif len(local_files) > 0:
                    integrated_entry.sync_status = "mismatched"
                else:
                    integrated_entry.sync_status = "broken"

                self._integrated_data.append(integrated_entry)

            # Add orphan files (local files not in Rekordbox)
            rekordbox_file_paths = {str(t.file_path) for t in rekordbox_tracks}
            orphan_files = [
                f for f in local_files
                if str(f.file_path) not in rekordbox_file_paths
            ]

            if orphan_files:
                # Group orphans by parent directory
                orphan_path_map = {}
                for orphan in orphan_files:
                    parent_path = str(orphan.file_path.parent)
                    if parent_path not in orphan_path_map:
                        orphan_path_map[parent_path] = []
                    orphan_path_map[parent_path].append(orphan)

                # Add orphan entries
                for orphan_path, files in orphan_path_map.items():
                    orphan_entry = IntegratedData(
                        rekordbox_path=orphan_path,
                        local_files=[],
                        orphan_files=files,
                        sync_status="orphan",
                    )
                    self._integrated_data.append(orphan_entry)

            self._apply_filters_and_sort()
            log.info("Loaded %d integrated entries", len(self._integrated_data))

        except Exception as exc:
            log.error("Failed to load Integrated view data: %s", exc)

    def get_integrated_data(self, limit: int = 1000, offset: int = 0) -> list[IntegratedData]:
        """
        Get integrated view data.

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip

        Returns:
            List of integrated data
        """
        return self._filtered_data[offset:offset + limit]

    def search(self, query: str, search_fields: list[str] | None = None) -> list[IntegratedData]:
        """
        Search integrated view by query string.

        Args:
            query: Search query
            search_fields: Fields to search in (None = all fields)

        Returns:
            List of matching integrated entries
        """
        query_lower = query.lower()

        if not search_fields:
            search_fields = ["rekordbox_path"]

        results = []
        for entry in self._integrated_data:
            for field in search_fields:
                value = getattr(entry, field, "")
                if value and query_lower in str(value).lower():
                    results.append(entry)
                    break

        return results

    def sort(self, field: str, ascending: bool = True) -> None:
        """
        Sort integrated view by the specified field.

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
        total_entries = len(self._integrated_data)
        synced = sum(1 for e in self._integrated_data if e.sync_status == "synced")
        mismatched = sum(1 for e in self._integrated_data if e.sync_status == "mismatched")
        broken = sum(1 for e in self._integrated_data if e.sync_status == "broken")
        orphan = sum(1 for e in self._integrated_data if e.sync_status == "orphan")

        total_local_files = sum(len(e.local_files) + len(e.orphan_files) for e in self._integrated_data)

        return {
            "total_entries": total_entries,
            "synced": synced,
            "mismatched": mismatched,
            "broken": broken,
            "orphan": orphan,
            "total_local_files": total_local_files,
            "health_percentage": (synced / total_entries * 100) if total_entries > 0 else 0,
        }

    def _apply_filters_and_sort(self) -> None:
        """Apply current filters and sorting to the integrated data list."""
        # Apply filters
        self._filtered_data = self._integrated_data.copy()

        for field, value in self._filters.items():
            self._filtered_data = [
                e for e in self._filtered_data
                if getattr(e, field, None) == value
            ]

        # Apply sorting
        reverse = not self._sort_ascending
        self._filtered_data.sort(
            key=lambda e: getattr(e, self._sort_field, ""),
            reverse=reverse
        )
