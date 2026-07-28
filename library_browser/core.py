"""
library_browser.core — Core library browser classes and data structures.

Defines the fundamental data structures and main browser interface
for the three-view library system.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Set


class ViewMode(Enum):
    """Library view modes."""
    REKORDBOX = "rekordbox"      # Database-centric view
    LOCAL = "local"              # Filesystem-centric view
    INTEGRATED = "integrated"    # Three-column comparison view


@dataclass
class TrackData:
    """Data structure for a track in Rekordbox View."""
    id: str                              # Track ID from database
    title: str                           # Track title
    artist: str                          # Artist name
    album: str                           # Album name
    file_path: Path                      # Full file path
    duration: float                      # Duration in seconds
    bpm: Optional[float] = None          # BPM value
    key: Optional[str] = None            # Musical key
    bit_rate: Optional[int] = None       # Bit rate
    sample_rate: Optional[int] = None    # Sample rate
    file_size: Optional[int] = None      # File size in bytes
    date_added: Optional[str] = None     # Date added to Rekordbox
    play_count: int = 0                  # Play count
    rating: int = 0                      # Rating (0-5)
    genre: Optional[str] = None          # Genre
    label: Optional[str] = None          # Record label
    file_status: str = "unknown"         # "valid", "missing", "corrupted"
    playlist_ids: List[str] = field(default_factory=list)  # Playlist memberships
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "file_path": str(self.file_path),
            "duration": self.duration,
            "bpm": self.bpm,
            "key": self.key,
            "bit_rate": self.bit_rate,
            "sample_rate": self.sample_rate,
            "file_size": self.file_size,
            "date_added": self.date_added,
            "play_count": self.play_count,
            "rating": self.rating,
            "genre": self.genre,
            "label": self.label,
            "file_status": self.file_status,
            "playlist_ids": self.playlist_ids,
        }


@dataclass
class FileData:
    """Data structure for a file in Local View."""
    file_path: Path                      # Full file path
    file_name: str                       # Filename only
    file_size: int                       # File size in bytes
    duration: Optional[float] = None    # Duration in seconds
    format: Optional[str] = None         # Audio format (mp3, wav, etc.)
    bit_rate: Optional[int] = None       # Bit rate
    sample_rate: Optional[int] = None    # Sample rate
    modified_date: Optional[str] = None  # Last modified date
    artist: Optional[str] = None         # Artist from metadata
    album: Optional[str] = None          # Album from metadata
    title: Optional[str] = None          # Title from metadata
    bpm: Optional[float] = None          # BPM from metadata
    key: Optional[str] = None            # Key from metadata
    in_rekordbox: bool = False          # Whether file is in Rekordbox
    is_duplicate: bool = False           # Whether file is a duplicate
    drive: Optional[str] = None          # Drive identifier
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "file_path": str(self.file_path),
            "file_name": self.file_name,
            "file_size": self.file_size,
            "duration": self.duration,
            "format": self.format,
            "bit_rate": self.bit_rate,
            "sample_rate": self.sample_rate,
            "modified_date": self.modified_date,
            "artist": self.artist,
            "album": self.album,
            "title": self.title,
            "bpm": self.bpm,
            "key": self.key,
            "in_rekordbox": self.in_rekordbox,
            "is_duplicate": self.is_duplicate,
            "drive": self.drive,
        }


@dataclass
class IntegratedData:
    """Data structure for Integrated View three-column layout."""
    rekordbox_path: str                 # Path as seen by Rekordbox
    local_files: List[FileData] = field(default_factory=list)  # Matching local files
    orphan_files: List[FileData] = field(default_factory=list)  # Files not in Rekordbox
    sync_status: str = "unknown"         # "synced", "mismatched", "broken"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "rekordbox_path": self.rekordbox_path,
            "local_files": [f.to_dict() for f in self.local_files],
            "orphan_files": [f.to_dict() for f in self.orphan_files],
            "sync_status": self.sync_status,
        }


class LibraryBrowser:
    """
    Main library browser interface.
    
    Provides unified access to the three view modes and handles
    view switching, caching, and data management.
    """
    
    def __init__(self, database: Optional[Any] = None, cache_dir: Optional[Path] = None):
        """
        Initialize the library browser.

        Args:
            database: Optional FableGearDatabase. When provided, the Local view
                renders database-first (instant) rather than re-scanning disk.
            cache_dir: Optional cache directory override for the view cache.
        """
        self._database = database
        self._cache_dir = cache_dir
        self._current_mode = ViewMode.LOCAL if database is not None else ViewMode.REKORDBOX
        self._rekordbox_view = None
        self._local_view = None
        self._integrated_view = None
        self._cache = None

        # Initialize views lazily when needed
        self._initialize_views()

    def _initialize_views(self):
        """Initialize view objects."""
        from library_browser.rekordbox_view import RekordboxView
        from library_browser.local_view import LocalView
        from library_browser.integrated_view import IntegratedView
        from library_browser.cache import ViewCache

        self._rekordbox_view = RekordboxView()
        self._local_view = LocalView(database=self._database)
        self._integrated_view = IntegratedView()
        self._cache = ViewCache(cache_dir=self._cache_dir)

    def load_local(self, limit: int = 1_000_000, offset: int = 0) -> None:
        """
        Switch to the database-first Local view and load it from the database.

        Returns nothing; results are read via get_files()/get_tracks().
        """
        if self._database is None:
            raise ValueError("LibraryBrowser has no database; construct it with one")
        self._current_mode = ViewMode.LOCAL
        self._local_view.load_from_database(limit=limit, offset=offset)
    
    def set_view_mode(self, mode: ViewMode) -> None:
        """
        Set the current view mode.
        
        Args:
            mode: View mode to switch to
        """
        self._current_mode = mode
    
    def get_view_mode(self) -> ViewMode:
        """Get the current view mode."""
        return self._current_mode
    
    def get_tracks(self, limit: int = 1000, offset: int = 0) -> List[TrackData]:
        """
        Get tracks from the current view.
        
        Args:
            limit: Maximum number of tracks to return
            offset: Number of tracks to skip
            
        Returns:
            List of track data
        """
        if self._current_mode == ViewMode.REKORDBOX:
            return self._rekordbox_view.get_tracks(limit, offset)
        elif self._current_mode == ViewMode.LOCAL:
            # Convert FileData to TrackData for Local View
            files = self._local_view.get_files(limit, offset)
            return self._file_data_to_track_data(files)
        else:
            return []
    
    def get_files(self, limit: int = 1000, offset: int = 0) -> List[FileData]:
        """
        Get files from the current view.
        
        Args:
            limit: Maximum number of files to return
            offset: Number of files to skip
            
        Returns:
            List of file data
        """
        if self._current_mode == ViewMode.LOCAL:
            return self._local_view.get_files(limit, offset)
        elif self._current_mode == ViewMode.REKORDBOX:
            # Convert TrackData to FileData for Rekordbox View
            tracks = self._rekordbox_view.get_tracks(limit, offset)
            return self._track_data_to_file_data(tracks)
        else:
            return []
    
    def get_integrated_view(self, limit: int = 1000, offset: int = 0) -> List[IntegratedData]:
        """
        Get integrated view data.
        
        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip
            
        Returns:
            List of integrated data
        """
        if self._current_mode == ViewMode.INTEGRATED:
            return self._integrated_view.get_integrated_data(limit, offset)
        return []
    
    def search(self, query: str, search_fields: List[str] = None) -> List[Any]:
        """
        Search across the current view.
        
        Args:
            query: Search query string
            search_fields: Fields to search in (None = all fields)
            
        Returns:
            List of matching tracks or files
        """
        if self._current_mode == ViewMode.REKORDBOX:
            return self._rekordbox_view.search(query, search_fields)
        elif self._current_mode == ViewMode.LOCAL:
            return self._local_view.search(query, search_fields)
        elif self._current_mode == ViewMode.INTEGRATED:
            return self._integrated_view.search(query, search_fields)
        return []
    
    def sort(self, field: str, ascending: bool = True) -> None:
        """
        Sort the current view by the specified field.
        
        Args:
            field: Field to sort by
            ascending: Sort direction
        """
        if self._current_mode == ViewMode.REKORDBOX:
            self._rekordbox_view.sort(field, ascending)
        elif self._current_mode == ViewMode.LOCAL:
            self._local_view.sort(field, ascending)
        elif self._current_mode == ViewMode.INTEGRATED:
            self._integrated_view.sort(field, ascending)
    
    def filter(self, filters: Dict[str, Any]) -> None:
        """
        Apply filters to the current view.
        
        Args:
            filters: Dictionary of field:value filters
        """
        if self._current_mode == ViewMode.REKORDBOX:
            self._rekordbox_view.filter(filters)
        elif self._current_mode == ViewMode.LOCAL:
            self._local_view.filter(filters)
        elif self._current_mode == ViewMode.INTEGRATED:
            self._integrated_view.filter(filters)
    
    def refresh(self) -> None:
        """Refresh the current view data."""
        if self._current_mode == ViewMode.REKORDBOX:
            self._rekordbox_view.refresh()
        elif self._current_mode == ViewMode.LOCAL:
            self._local_view.refresh()
        elif self._current_mode == ViewMode.INTEGRATED:
            self._integrated_view.refresh()
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics for the current view.
        
        Returns:
            Dictionary with view statistics
        """
        if self._current_mode == ViewMode.REKORDBOX:
            return self._rekordbox_view.get_statistics()
        elif self._current_mode == ViewMode.LOCAL:
            return self._local_view.get_statistics()
        elif self._current_mode == ViewMode.INTEGRATED:
            return self._integrated_view.get_statistics()
        return {}
    
    def _track_data_to_file_data(self, tracks: List[TrackData]) -> List[FileData]:
        """Convert TrackData objects to FileData objects."""
        files = []
        for track in tracks:
            file_data = FileData(
                file_path=track.file_path,
                file_name=track.file_path.name,
                file_size=track.file_size or 0,
                duration=track.duration,
                artist=track.artist,
                album=track.album,
                title=track.title,
                bpm=track.bpm,
                key=track.key,
                in_rekordbox=True,
            )
            files.append(file_data)
        return files
    
    def _file_data_to_track_data(self, files: List[FileData]) -> List[TrackData]:
        """Convert FileData objects to TrackData objects."""
        tracks = []
        for file in files:
            track_data = TrackData(
                id="",  # No database ID for local files
                title=file.title or file.file_name,
                artist=file.artist or "Unknown",
                album=file.album or "Unknown",
                file_path=file.file_path,
                duration=file.duration or 0.0,
                bpm=file.bpm,
                key=file.key,
                file_size=file.file_size,
                file_status="valid" if file.file_path.exists() else "missing",
            )
            tracks.append(track_data)
        return tracks