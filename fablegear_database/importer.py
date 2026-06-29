"""
fablegear_database.importer — File import and indexing system.

Handles importing physical files into the FableGear database with:
- Fast file hashing for change detection
- Metadata extraction from audio files
- Efficient bulk import operations
- Progress tracking and error handling
"""

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime

from .database import FableGearDatabase, ContentRecord

log = logging.getLogger(__name__)


class FileImporter:
    """
    Imports physical audio files into the FableGear database.
    
    Provides efficient file indexing with change detection,
    metadata extraction, and progress tracking.
    """
    
    def __init__(self, database: FableGearDatabase):
        """
        Initialize the file importer.
        
        Args:
            database: FableGear database instance
        """
        self.database = database
        self._audio_extensions = {
            ".mp3", ".wav", ".aiff", ".aif", ".aifc", ".flac",
            ".m4a", ".m4p", ".mp4", ".m4v", ".ogg", ".opus"
        }
    
    def import_files(
        self,
        root_paths: List[Path],
        progress_callback: Optional[Callable[[int, int], None]] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Import audio files from specified root paths.
        
        Args:
            root_paths: List of root directories to scan
            progress_callback: Optional callback for progress updates
            force_refresh: Force re-import of existing files
            
        Returns:
            Dictionary with import statistics
        """
        stats = {
            "total_files": 0,
            "new_files": 0,
            "updated_files": 0,
            "skipped_files": 0,
            "error_files": 0,
            "errors": [],
        }
        
        # Scan for audio files
        all_files = []
        for root in root_paths:
            if not root.exists():
                log.warning("Root path does not exist: %s", root)
                continue
            
            files = self._scan_audio_files(root)
            all_files.extend(files)
        
        stats["total_files"] = len(all_files)
        log.info("Found %d audio files to process", len(all_files))
        
        # Process each file
        for i, file_path in enumerate(all_files):
            try:
                # Check if file needs processing
                existing_record = self.database.get_content_by_path(str(file_path))
                
                if existing_record and not force_refresh:
                    # Check if file has changed
                    if not self._file_has_changed(file_path, existing_record):
                        stats["skipped_files"] += 1
                        if progress_callback:
                            progress_callback(i + 1, len(all_files))
                        continue
                
                # Extract metadata and import
                record = self._create_content_record(file_path)
                
                if existing_record:
                    # Update existing record
                    self.database.update_content(existing_record.id, record.to_dict())
                    stats["updated_files"] += 1
                else:
                    # Insert new record
                    self.database.insert_content(record)
                    stats["new_files"] += 1
                
                if progress_callback:
                    progress_callback(i + 1, len(all_files))
                    
            except Exception as exc:
                stats["error_files"] += 1
                stats["errors"].append(f"{file_path}: {exc}")
                log.error("Failed to import %s: %s", file_path, exc)
        
        log.info(
            "Import complete: %d new, %d updated, %d skipped, %d errors",
            stats["new_files"],
            stats["updated_files"],
            stats["skipped_files"],
            stats["error_files"]
        )
        
        return stats
    
    def _scan_audio_files(self, root: Path) -> List[Path]:
        """
        Scan directory for audio files.
        
        Args:
            root: Root directory to scan
            
        Returns:
            List of audio file paths
        """
        audio_files = []
        
        try:
            for item in root.rglob("*"):
                if item.is_file() and item.suffix.lower() in self._audio_extensions:
                    # Skip system files
                    if not item.name.startswith(".") and not item.name.startswith("._"):
                        audio_files.append(item)
                        
        except PermissionError:
            log.warning("Permission denied: %s", root)
        except Exception as exc:
            log.error("Error scanning %s: %s", root, exc)
        
        return audio_files
    
    def _file_has_changed(self, file_path: Path, record: ContentRecord) -> bool:
        """
        Check if a file has changed since last import.
        
        Args:
            file_path: File path to check
            record: Existing database record
            
        Returns:
            True if file has changed
        """
        try:
            # Check file size
            current_size = file_path.stat().st_size
            if current_size != record.file_size:
                return True
            
            # Check modification time
            current_mtime = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            if current_mtime != record.modified_date:
                return True
            
            # Check file hash if available
            if record.file_hash:
                current_hash = self._compute_file_hash(file_path)
                if current_hash != record.file_hash:
                    return True
            
            return False
            
        except Exception:
            return True  # Assume changed if check fails
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """
        Compute SHA-256 hash of file.
        
        Args:
            file_path: File to hash
            
        Returns:
            Hexadecimal hash string
        """
        sha256_hash = hashlib.sha256()
        
        try:
            with open(file_path, "rb") as f:
                # Read in chunks for memory efficiency
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
            
            return sha256_hash.hexdigest()
            
        except Exception as exc:
            log.error("Failed to compute hash for %s: %s", file_path, exc)
            return ""
    
    def _create_content_record(self, file_path: Path) -> ContentRecord:
        """
        Create a ContentRecord from a file.
        
        Args:
            file_path: File to process
            
        Returns:
            ContentRecord with extracted metadata
        """
        stat = file_path.stat()
        
        record = ContentRecord(
            file_path=str(file_path),
            file_name=file_path.name,
            file_size=stat.st_size,
            modified_date=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            format=file_path.suffix.lower().replace(".", ""),
            drive=self._get_drive_identifier(file_path),
        )
        
        # Extract metadata from file
        metadata = self._extract_metadata(file_path)
        record.title = metadata.get("title")
        record.artist = metadata.get("artist")
        record.album = metadata.get("album")
        record.bpm = metadata.get("bpm")
        record.key = metadata.get("key")
        record.genre = metadata.get("genre")
        record.year = metadata.get("year")
        record.track_number = metadata.get("track_number")
        record.disc_number = metadata.get("disc_number")
        record.comment = metadata.get("comment")
        record.duration = metadata.get("duration")
        record.bit_rate = metadata.get("bit_rate")
        record.sample_rate = metadata.get("sample_rate")
        
        # Compute file hash
        record.file_hash = self._compute_file_hash(file_path)
        
        return record
    
    def _extract_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from audio file.
        
        Args:
            file_path: File to extract metadata from
            
        Returns:
            Dictionary with extracted metadata
        """
        metadata = {}
        
        try:
            from mutagen import File as MutagenFile
            
            audio_file = MutagenFile(file_path)
            if audio_file:
                # Extract technical info
                if hasattr(audio_file, 'info'):
                    info = audio_file.info
                    metadata['duration'] = getattr(info, 'length', None)
                    metadata['bit_rate'] = getattr(info, 'bitrate', None)
                    metadata['sample_rate'] = getattr(info, 'sample_rate', None)
                
                # Extract tags
                tags = audio_file.tags or {}
                metadata['title'] = tags.get('TIT2', [None])[0] if 'TIT2' in tags else tags.get('title', [None])[0]
                metadata['artist'] = tags.get('TPE1', [None])[0] if 'TPE1' in tags else tags.get('artist', [None])[0]
                metadata['album'] = tags.get('TALB', [None])[0] if 'TALB' in tags else tags.get('album', [None])[0]
                metadata['genre'] = tags.get('TCON', [None])[0] if 'TCON' in tags else tags.get('genre', [None])[0]
                metadata['year'] = tags.get('TDRC', [None])[0] if 'TDRC' in tags else tags.get('date', [None])[0]
                metadata['comment'] = tags.get('COMM', [None])[0] if 'COMM' in tags else tags.get('comment', [None])[0]
                
                # Extract BPM and key if available
                if 'TBPM' in tags:
                    try:
                        metadata['bpm'] = float(tags['TBPM'][0])
                    except (ValueError, TypeError):
                        pass
                if 'TKEY' in tags:
                    metadata['key'] = tags['TKEY'][0]
                
        except Exception as exc:
            log.error("Failed to extract metadata from %s: %s", file_path, exc)
        
        return metadata
    
    def _get_drive_identifier(self, file_path: Path) -> str:
        """
        Get drive identifier for a file path.
        
        Args:
            file_path: File path
            
        Returns:
            Drive identifier string
        """
        try:
            parts = file_path.parts
            if len(parts) >= 2 and parts[0] == "/":
                return parts[1]  # e.g., "Volumes", "Music"
            return "local"
        except Exception:
            return "unknown"
    
    def update_fingerprint(self, file_path: Path, fingerprint: str, quality: int = 100) -> bool:
        """
        Update acoustic fingerprint for a file.
        
        Args:
            file_path: File to update
            fingerprint: Acoustic fingerprint string
            quality: Fingerprint quality score
            
        Returns:
            True if update succeeded
        """
        try:
            record = self.database.get_content_by_path(str(file_path))
            if record:
                updates = {
                    "acoustic_fingerprint": fingerprint,
                    "fingerprint_quality": quality,
                    "processing_status": "fingerprinted",
                }
                return self.database.update_content(record.id, updates)
            return False
            
        except Exception as exc:
            log.error("Failed to update fingerprint for %s: %s", file_path, exc)
            return False