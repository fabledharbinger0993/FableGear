"""
library_browser.scanner — Database-first library scanning.

Uses the FableGear database for instant library browsing instead of
slow filesystem scanning. Provides significant performance improvements
for all three library views.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from datetime import datetime

log = logging.getLogger(__name__)

try:
    from fablegear_database import FableGearDatabase, ContentRecord
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    log.warning("FableGear database not available, falling back to filesystem scanning")


class LibraryScanner:
    """
    Database-first library scanner for library browser.
    
    Uses the FableGear database for instant queries instead of slow
    filesystem scanning. Falls back to filesystem scanning if database
    is not available.
    """
    
    def __init__(self):
        """Initialize the library scanner."""
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._db: Optional[FableGearDatabase] = None
        
        if DATABASE_AVAILABLE:
            try:
                self._db = FableGearDatabase()
                log.info("Library scanner using database-first approach")
            except Exception as exc:
                log.warning("Failed to initialize database: %s", exc)
                self._db = None
    
    def scan_local_files(
        self,
        roots: List[Path],
        progress_callback: Optional[callable] = None,
    ) -> List[Path]:
        """
        Get audio files from database (instant) or filesystem (fallback).
        
        Args:
            roots: Root paths to scan (used for filesystem fallback)
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of audio file paths
        """
        if self._db:
            # Database-first approach: instant query
            try:
                all_records = self._db.get_all_content(limit=100000)
                file_paths = [Path(record.file_path) for record in all_records]
                log.info("Retrieved %d files from database (instant)", len(file_paths))
                return file_paths
            except Exception as exc:
                log.error("Database query failed, falling back to filesystem: %s", exc)
        
        # Fallback to filesystem scanning
        return self._scan_filesystem(roots, progress_callback)
    
    def _scan_filesystem(
        self,
        roots: List[Path],
        progress_callback: Optional[callable] = None,
    ) -> List[Path]:
        """
        Fallback filesystem scanning (slow).
        
        Args:
            roots: Root paths to scan
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of audio file paths
        """
        all_files = []
        total_scanned = 0
        audio_extensions = {
            ".mp3", ".wav", ".aiff", ".aif", ".aifc", ".flac", 
            ".m4a", ".m4p", ".ogg", ".opus"
        }
        
        for root in roots:
            if self._cancel_event.is_set():
                break
            
            if not root.exists():
                log.warning("Root path does not exist: %s", root)
                continue
            
            for file_path in self._walk_audio_files(root, audio_extensions):
                if self._cancel_event.is_set():
                    break
                
                all_files.append(file_path)
                total_scanned += 1
                
                if progress_callback and total_scanned % 100 == 0:
                    progress_callback(total_scanned, len(all_files))
        
        log.info("Scanned %d audio files from %d roots (filesystem)", len(all_files), len(roots))
        return all_files
    
    def _walk_audio_files(self, root: Path, audio_extensions: Set[str]) -> List[Path]:
        """
        Walk directory tree and collect audio files.
        
        Args:
            root: Root path to walk
            audio_extensions: Set of audio file extensions
            
        Returns:
            List of audio file paths
        """
        audio_files = []
        
        try:
            for item in root.rglob("*"):
                if self._cancel_event.is_set():
                    break
                
                if item.is_file() and item.suffix.lower() in audio_extensions:
                    # Skip system files and hidden files
                    if not item.name.startswith(".") and not item.name.startswith("._"):
                        audio_files.append(item)
                        
        except PermissionError:
            log.warning("Permission denied: %s", root)
        except Exception as exc:
            log.error("Error scanning %s: %s", root, exc)
        
        return audio_files
    
    def scan_rekordbox_database(self) -> List[Dict[str, Any]]:
        """
        Get track information from FableGear database (instant).
        
        Returns:
            List of track dictionaries
        """
        if self._db:
            try:
                # Get all records from database
                all_records = self._db.get_all_content(limit=100000)
                
                tracks = []
                for record in all_records:
                    track_data = {
                        "id": str(record.id),
                        "title": record.title or "",
                        "artist": record.artist or "",
                        "album": record.album or "",
                        "file_path": record.file_path,
                        "duration": record.duration or 0.0,
                        "bpm": record.bpm,
                        "key": record.key,
                        "file_status": "corrupted" if record.is_corrupted else "valid",
                    }
                    tracks.append(track_data)
                
                log.info("Retrieved %d tracks from database (instant)", len(tracks))
                return tracks
                
            except Exception as exc:
                log.error("Database query failed: %s", exc)
        
        # Fallback to Rekordbox database scanning
        return self._scan_rekordbox_legacy()
    
    def _scan_rekordbox_legacy(self) -> List[Dict[str, Any]]:
        """
        Fallback to legacy Rekordbox database scanning.
        
        Returns:
            List of track dictionaries
        """
        try:
            from db_connection import read_db
            from config import LOCAL_DB
            
            tracks = []
            with read_db() as db:
                content_rows = db.get_content().all()
                
                for row in content_rows:
                    track_data = {
                        "id": str(row.ID),
                        "title": getattr(row, "Title", ""),
                        "artist": getattr(row, "Artist", ""),
                        "album": getattr(row, "Album", ""),
                        "file_path": getattr(row, "FolderPath", ""),
                        "duration": getattr(row, "Length", 0.0),
                        "bpm": getattr(row, "AverageBpm", None),
                        "key": getattr(row, "Tonality", None),
                    }
                    tracks.append(track_data)
            
            log.info("Scanned %d tracks from Rekordbox database (legacy)", len(tracks))
            return tracks
            
        except Exception as exc:
            log.error("Failed to scan Rekordbox database: %s", exc)
            return []
    
    def check_file_status(self, file_path: Path) -> str:
        """
        Check the status of a file from database.
        
        Args:
            file_path: Path to check
            
        Returns:
            File status: "valid", "missing", "corrupted"
        """
        if self._db:
            try:
                record = self._db.get_content_by_path(str(file_path))
                if record:
                    if record.is_corrupted:
                        return "corrupted"
                    return "valid"
            except Exception:
                pass
        
        # Fallback to filesystem check
        return self._check_file_filesystem(file_path)
    
    def _check_file_filesystem(self, file_path: Path) -> str:
        """
        Fallback filesystem file status check.
        
        Args:
            file_path: Path to check
            
        Returns:
            File status: "valid", "missing", "corrupted"
        """
        if not file_path.exists():
            return "missing"
        
        if not file_path.is_file():
            return "corrupted"
        
        if file_path.stat().st_size == 0:
            return "corrupted"
        
        return "valid"
    
    def get_file_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        Get file metadata from database (instant) or extract from file.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Dictionary with file metadata
        """
        if self._db:
            try:
                record = self._db.get_content_by_path(str(file_path))
                if record:
                    return {
                        "file_path": str(file_path),
                        "file_name": record.file_name,
                        "file_size": record.file_size,
                        "modified_date": record.modified_date,
                        "duration": record.duration,
                        "format": record.format,
                        "bit_rate": record.bit_rate,
                        "sample_rate": record.sample_rate,
                        "artist": record.artist,
                        "album": record.album,
                        "title": record.title,
                        "bpm": record.bpm,
                        "key": record.key,
                        "genre": record.genre,
                    }
            except Exception:
                pass
        
        # Fallback to file metadata extraction
        return self._extract_file_metadata(file_path)
    
    def _extract_file_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        Fallback file metadata extraction.
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Dictionary with file metadata
        """
        try:
            from mutagen import File as MutagenFile
            
            metadata = {
                "file_path": str(file_path),
                "file_name": file_path.name,
                "file_size": file_path.stat().st_size,
                "modified_date": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
            }
            
            audio_file = MutagenFile(file_path)
            if audio_file:
                if hasattr(audio_file, 'info'):
                    info = audio_file.info
                    metadata['duration'] = getattr(info, 'length', None)
                    metadata['bit_rate'] = getattr(info, 'bitrate', None)
                    metadata['sample_rate'] = getattr(info, 'sample_rate', None)
                
                # Extract basic tags
                tags = audio_file.tags or {}
                metadata['artist'] = tags.get('artist', [None])[0]
                metadata['album'] = tags.get('album', [None])[0]
                metadata['title'] = tags.get('title', [None])[0]
                metadata['genre'] = tags.get('genre', [None])[0]
            
            return metadata
            
        except Exception as exc:
            log.error("Failed to extract metadata from %s: %s", file_path, exc)
            return {
                "file_path": str(file_path),
                "file_name": file_path.name,
                "file_size": file_path.stat().st_size,
                "modified_date": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
            }
    
    def cancel(self) -> None:
        """Cancel the current scanning operation."""
        with self._lock:
            self._cancel_event.set()
    
    def reset(self) -> None:
        """Reset the scanner state."""
        with self._lock:
            self._cancel_event.clear()