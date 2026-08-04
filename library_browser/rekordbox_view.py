"""
library_browser.rekordbox_view — Rekordbox database-centric view.

Displays tracks currently imported into Rekordbox with file health
indicators and DB-specific metadata.
"""

import logging
from typing import Any

from library_browser.core import TrackData

log = logging.getLogger(__name__)


class RekordboxView:
    """
    Rekordbox database-centric library view.

    Shows all tracks currently imported into Rekordbox with enhanced
    file health indicators and DB-specific metadata.
    """

    def __init__(self):
        """Initialize the Rekordbox view."""
        self._tracks: list[TrackData] = []
        self._filtered_tracks: list[TrackData] = []
        self._filters: dict[str, Any] = {}
        self._sort_field = "title"
        self._sort_ascending = True

    def load_data(self) -> None:
        """Load track data from Rekordbox database."""
        try:
            from library_browser.scanner import LibraryScanner

            scanner = LibraryScanner()
            db_tracks = scanner.scan_rekordbox_database()

            self._tracks = []
            for track_data in db_tracks:
                track = TrackData(
                    id=track_data["id"],
                    title=track_data["title"],
                    artist=track_data["artist"],
                    album=track_data["album"],
                    file_path=track_data["file_path"],
                    duration=track_data["duration"],
                    bpm=track_data["bpm"],
                    key=track_data["key"],
                )

                # Check file status
                track.file_status = scanner.check_file_status(track.file_path)

                self._tracks.append(track)

            self._apply_filters_and_sort()
            log.info("Loaded %d tracks from Rekordbox database", len(self._tracks))

        except Exception as exc:
            log.error("Failed to load Rekordbox view data: %s", exc)

    def get_tracks(self, limit: int = 1000, offset: int = 0) -> list[TrackData]:
        """
        Get tracks from the view.

        Args:
            limit: Maximum number of tracks to return
            offset: Number of tracks to skip

        Returns:
            List of track data
        """
        return self._filtered_tracks[offset:offset + limit]

    def search(self, query: str, search_fields: list[str] | None = None) -> list[TrackData]:
        """
        Search tracks by query string.

        Args:
            query: Search query
            search_fields: Fields to search in (None = all fields)

        Returns:
            List of matching tracks
        """
        query_lower = query.lower()

        if not search_fields:
            search_fields = ["title", "artist", "album", "genre"]

        results = []
        for track in self._tracks:
            for field in search_fields:
                value = getattr(track, field, "")
                if value and query_lower in str(value).lower():
                    results.append(track)
                    break

        return results

    def sort(self, field: str, ascending: bool = True) -> None:
        """
        Sort tracks by the specified field.

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
        self.load_data()

    def get_statistics(self) -> dict[str, Any]:
        """
        Get view statistics.

        Returns:
            Dictionary with view statistics
        """
        total_tracks = len(self._tracks)
        missing_files = sum(1 for t in self._tracks if t.file_status == "missing")
        corrupted_files = sum(1 for t in self._tracks if t.file_status == "corrupted")
        valid_files = total_tracks - missing_files - corrupted_files

        return {
            "total_tracks": total_tracks,
            "valid_files": valid_files,
            "missing_files": missing_files,
            "corrupted_files": corrupted_files,
            "health_percentage": (valid_files / total_tracks * 100) if total_tracks > 0 else 0,
        }

    def _apply_filters_and_sort(self) -> None:
        """Apply current filters and sorting to the track list."""
        # Apply filters
        self._filtered_tracks = self._tracks.copy()

        for field, value in self._filters.items():
            self._filtered_tracks = [
                t for t in self._filtered_tracks
                if getattr(t, field, None) == value
            ]

        # Apply sorting
        reverse = not self._sort_ascending
        self._filtered_tracks.sort(
            key=lambda t: getattr(t, self._sort_field, ""),
            reverse=reverse
        )
