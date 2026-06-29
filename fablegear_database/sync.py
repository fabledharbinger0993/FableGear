"""
fablegear_database.sync — Database/filesystem synchronization.

Handles keeping the FableGear database in sync with the physical
filesystem, detecting changes and maintaining consistency.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from datetime import datetime

from .database import FableGearDatabase, ContentRecord

log = logging.getLogger(__name__)


class DatabaseSync:
    """
    Synchronizes database with filesystem state.
    
    Detects changes in the filesystem and updates the database
    accordingly, ensuring the database remains the source of truth.
    """
    
    def __init__(self, database: FableGearDatabase):
        """
        Initialize the database sync manager.
        
        Args:
            database: FableGear database instance
        """
        self.database = database
    
    def sync_database_to_filesystem(
        self,
        verify_integrity: bool = True,
    ) -> Dict[str, Any]:
        """
        Sync database state with current filesystem state.
        
        Args:
            verify_integrity: Verify file existence and integrity
            
        Returns:
            Dictionary with sync statistics
        """
        stats = {
            "total_records": 0,
            "missing_files": 0,
            "corrupted_files": 0,
            "updated_records": 0,
            "errors": [],
        }
        
        try:
            # Get all records from database
            all_records = self.database.get_all_content(limit=100000)  # Get all
            stats["total_records"] = len(all_records)
            
            for record in all_records:
                file_path = Path(record.file_path)
                
                # Check if file exists
                if not file_path.exists():
                    # Mark as missing
                    self.database.update_content(record.id, {
                        "is_corrupted": True,
                        "processing_status": "missing"
                    })
                    stats["missing_files"] += 1
                    continue
                
                # Check file integrity if requested
                if verify_integrity:
                    if not self._verify_file_integrity(file_path, record):
                        self.database.update_content(record.id, {
                            "is_corrupted": True,
                            "processing_status": "corrupted"
                        })
                        stats["corrupted_files"] += 1
                        continue
                
                # Check if file has changed
                if self._file_has_changed(file_path, record):
                    # Update record with new metadata
                    from .importer import FileImporter
                    importer = FileImporter(self.database)
                    new_record = importer._create_content_record(file_path)
                    self.database.update_content(record.id, new_record.to_dict())
                    stats["updated_records"] += 1
            
            log.info(
                "Sync complete: %d missing, %d corrupted, %d updated",
                stats["missing_files"],
                stats["corrupted_files"],
                stats["updated_records"]
            )
            
        except Exception as exc:
            log.error("Database sync failed: %s", exc)
            stats["errors"].append(str(exc))
        
        return stats
    
    def find_orphaned_files(
        self,
        root_paths: List[Path],
    ) -> List[Path]:
        """
        Find files that exist in filesystem but not in database.
        
        Args:
            root_paths: Root directories to scan
            
        Returns:
            List of orphaned file paths
        """
        from .importer import FileImporter
        
        importer = FileImporter(self.database)
        
        # Scan filesystem for audio files
        all_files = set()
        for root in root_paths:
            files = importer._scan_audio_files(root)
            all_files.update(str(f) for f in files)
        
        # Get all file paths from database
        db_files = set()
        all_records = self.database.get_all_content(limit=100000)
        for record in all_records:
            db_files.add(record.file_path)
        
        # Find orphaned files
        orphaned = all_files - db_files
        
        log.info("Found %d orphaned files", len(orphaned))
        return [Path(f) for f in orphaned]
    
    def find_stale_records(self) -> List[ContentRecord]:
        """
        Find database records that reference non-existent files.
        
        Returns:
            List of stale ContentRecords
        """
        stale_records = []
        all_records = self.database.get_all_content(limit=100000)
        
        for record in all_records:
            file_path = Path(record.file_path)
            if not file_path.exists():
                stale_records.append(record)
        
        log.info("Found %d stale database records", len(stale_records))
        return stale_records
    
    def cleanup_stale_records(self, dry_run: bool = False) -> int:
        """
        Remove database records for non-existent files.
        
        Args:
            dry_run: If True, only report what would be deleted
            
        Returns:
            Number of records removed
        """
        stale_records = self.find_stale_records()
        
        if dry_run:
            log.info("Would remove %d stale records (dry run)", len(stale_records))
            return len(stale_records)
        
        removed_count = 0
        for record in stale_records:
            try:
                # Delete from database (would need delete method in FableGearDatabase)
                # For now, mark as corrupted
                self.database.update_content(record.id, {
                    "is_corrupted": True,
                    "processing_status": "deleted"
                })
                removed_count += 1
            except Exception as exc:
                log.error("Failed to remove stale record %d: %s", record.id, exc)
        
        log.info("Removed %d stale records", removed_count)
        return removed_count
    
    def _verify_file_integrity(self, file_path: Path, record: ContentRecord) -> bool:
        """
        Verify file integrity against database record.
        
        Args:
            file_path: File to verify
            record: Database record to compare against
            
        Returns:
            True if file is valid
        """
        try:
            # Check file size
            current_size = file_path.stat().st_size
            if current_size != record.file_size:
                return False
            
            # Check file hash if available
            if record.file_hash:
                from .importer import FileImporter
                importer = FileImporter(self.database)
                current_hash = importer._compute_file_hash(file_path)
                if current_hash != record.file_hash:
                    return False
            
            return True
            
        except Exception:
            return False
    
    def _file_has_changed(self, file_path: Path, record: ContentRecord) -> bool:
        """
        Check if file has changed since database record.
        
        Args:
            file_path: File to check
            record: Database record
            
        Returns:
            True if file has changed
        """
        try:
            # Check modification time
            current_mtime = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            if current_mtime != record.modified_date:
                return True
            
            # Check file size
            current_size = file_path.stat().st_size
            if current_size != record.file_size:
                return True
            
            return False
            
        except Exception:
            return True