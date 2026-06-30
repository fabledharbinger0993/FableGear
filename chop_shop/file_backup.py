"""
chop_shop.file_backup — File-level backup system for Chop Shop operations.

Provides comprehensive file backup and recovery for Chop Shop operations,
ensuring no file editing operations are permanent and can be safely reversed.
"""

import logging
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class FileBackupRecord:
    """Record of a file backup operation."""
    backup_id: str
    original_path: str
    backup_path: str
    operation_type: str
    timestamp: str
    file_hash: str
    file_size: int
    confirmed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "backup_id": self.backup_id,
            "original_path": self.original_path,
            "backup_path": self.backup_path,
            "operation_type": self.operation_type,
            "timestamp": self.timestamp,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "confirmed": self.confirmed,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileBackupRecord":
        """Create FileBackupRecord from dictionary."""
        return cls(**data)


class FileBackupManager:
    """
    Manages file-level backups for Chop Shop operations.
    
    Ensures that all file editing operations in Chop Shop have
    backup copies available for safe reversal.
    """
    
    def __init__(self, backup_dir: Optional[Path] = None, max_age_days: int = 30):
        """
        Initialize file backup manager.
        
        Args:
            backup_dir: Directory for backups (default: ~/.fablegear/file_backups)
            max_age_days: Maximum age of backups in days
        """
        self.backup_dir = backup_dir or Path.home() / ".fablegear" / "file_backups"
        self.max_age_days = max_age_days
        self._backup_records: Dict[str, FileBackupRecord] = {}
        
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._load_backup_records()
    
    def _load_backup_records(self) -> None:
        """Load backup records from file."""
        records_file = self.backup_dir / "backup_records.json"
        if not records_file.exists():
            return
        
        try:
            import json
            with open(records_file, "r") as f:
                data = json.load(f)
            
            self._backup_records = {
                item["backup_id"]: FileBackupRecord.from_dict(item)
                for item in data
            }
            
            log.info("Loaded %d backup records", len(self._backup_records))
            
        except Exception as exc:
            log.error("Failed to load backup records: %s", exc)
            self._backup_records = {}
    
    def _save_backup_records(self) -> None:
        """Save backup records to file."""
        try:
            import json
            records_file = self.backup_dir / "backup_records.json"
            
            data = [record.to_dict() for record in self._backup_records.values()]
            with open(records_file, "w") as f:
                json.dump(data, f, indent=2)
            
        except Exception as exc:
            log.error("Failed to save backup records: %s", exc)
    
    def create_backup(
        self,
        file_path: Path,
        operation_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Create a backup of a file before editing.
        
        Args:
            file_path: File to backup
            operation_type: Type of operation (e.g., "tag", "normalize", "convert")
            metadata: Additional metadata
            
        Returns:
            Backup ID or None if backup failed
        """
        if not file_path.exists():
            log.error("File does not exist: %s", file_path)
            return None
        
        try:
            # Generate backup ID
            backup_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            
            # Create backup path
            timestamp = datetime.now().strftime("%Y%m%d")
            backup_subdir = self.backup_dir / timestamp
            backup_subdir.mkdir(exist_ok=True)
            
            backup_path = backup_subdir / f"{file_path.name}_{backup_id}{file_path.suffix}"
            
            # Copy file to backup location
            shutil.copy2(file_path, backup_path)
            
            # Calculate file hash
            file_hash = self._calculate_file_hash(file_path)
            file_size = file_path.stat().st_size
            
            # Create backup record
            record = FileBackupRecord(
                backup_id=backup_id,
                original_path=str(file_path),
                backup_path=str(backup_path),
                operation_type=operation_type,
                timestamp=datetime.now().isoformat(),
                file_hash=file_hash,
                file_size=file_size,
                confirmed=False,
                metadata=metadata or {},
            )
            
            self._backup_records[backup_id] = record
            self._save_backup_records()
            
            log.info("Created backup %s for %s", backup_id, file_path)
            return backup_id
            
        except Exception as exc:
            log.error("Failed to create backup for %s: %s", file_path, exc)
            return None
    
    def restore_backup(self, backup_id: str, confirm: bool = False) -> bool:
        """
        Restore a file from backup.
        
        Args:
            backup_id: ID of backup to restore
            confirm: Whether user has confirmed the restore
            
        Returns:
            True if restore succeeded
        """
        record = self._backup_records.get(backup_id)
        if not record:
            log.error("Backup not found: %s", backup_id)
            return False
        
        try:
            original_path = Path(record.original_path)
            backup_path = Path(record.backup_path)
            
            if not backup_path.exists():
                log.error("Backup file not found: %s", backup_path)
                return False
            
            # Verify file hash matches original
            current_hash = self._calculate_file_hash(backup_path)
            if current_hash != record.file_hash:
                log.error("Backup file hash mismatch, possible corruption")
                return False
            
            # Create backup of current file before restore
            if original_path.exists():
                current_backup_id = self.create_backup(
                    original_path,
                    operation_type="pre_restore",
                    metadata={"original_backup_id": backup_id}
                )
                if not current_backup_id:
                    log.warning("Failed to create pre-restore backup")
            
            # Restore from backup
            shutil.copy2(backup_path, original_path)
            
            # Mark as confirmed
            if confirm:
                record.confirmed = True
                self._save_backup_records()
            
            log.info("Restored file from backup %s: %s", backup_id, original_path)
            return True
            
        except Exception as exc:
            log.error("Failed to restore backup %s: %s", backup_id, exc)
            return False
    
    def get_backup(self, backup_id: str) -> Optional[FileBackupRecord]:
        """
        Get a backup record.
        
        Args:
            backup_id: ID of backup
            
        Returns:
            FileBackupRecord or None if not found
        """
        return self._backup_records.get(backup_id)
    
    def get_backups_for_file(self, file_path: Path) -> List[FileBackupRecord]:
        """
        Get all backups for a specific file.
        
        Args:
            file_path: File path
            
        Returns:
            List of backup records
        """
        file_str = str(file_path)
        return [
            record for record in self._backup_records.values()
            if record.original_path == file_str
        ]
    
    def confirm_backup(self, backup_id: str) -> bool:
        """
        Confirm a backup (mark as safe to cleanup).
        
        Args:
            backup_id: ID of backup to confirm
            
        Returns:
            True if confirmation succeeded
        """
        record = self._backup_records.get(backup_id)
        if not record:
            return False
        
        record.confirmed = True
        self._save_backup_records()
        
        log.info("Confirmed backup %s", backup_id)
        return True
    
    def delete_backup(self, backup_id: str) -> bool:
        """
        Delete a backup and its record.
        
        Args:
            backup_id: ID of backup to delete
            
        Returns:
            True if deletion succeeded
        """
        record = self._backup_records.get(backup_id)
        if not record:
            return False
        
        try:
            backup_path = Path(record.backup_path)
            if backup_path.exists():
                backup_path.unlink()
            
            del self._backup_records[backup_id]
            self._save_backup_records()
            
            log.info("Deleted backup %s", backup_id)
            return True
            
        except Exception as exc:
            log.error("Failed to delete backup %s: %s", backup_id, exc)
            return False
    
    def cleanup_old_backups(self) -> int:
        """
        Delete old backups that are confirmed and past max age.
        
        Returns:
            Number of backups deleted
        """
        deleted_count = 0
        cutoff_time = datetime.now().timestamp() - (self.max_age_days * 24 * 60 * 60)
        
        backup_ids_to_delete = []
        
        for backup_id, record in self._backup_records.items():
            # Only delete confirmed backups
            if not record.confirmed:
                continue
            
            # Check age
            try:
                backup_time = datetime.fromisoformat(record.timestamp).timestamp()
                if backup_time < cutoff_time:
                    backup_ids_to_delete.append(backup_id)
            except Exception:
                continue
        
        # Delete old backups
        for backup_id in backup_ids_to_delete:
            if self.delete_backup(backup_id):
                deleted_count += 1
        
        log.info("Cleaned up %d old backups", deleted_count)
        return deleted_count
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get backup statistics.
        
        Returns:
            Dictionary with backup statistics
        """
        total_backups = len(self._backup_records)
        confirmed_backups = sum(1 for r in self._backup_records.values() if r.confirmed)
        unconfirmed_backups = total_backups - confirmed_backups
        
        # Calculate total size
        total_size = 0
        for record in self._backup_records.values():
            backup_path = Path(record.backup_path)
            if backup_path.exists():
                total_size += backup_path.stat().st_size
        
        # Count by operation type
        operation_counts = {}
        for record in self._backup_records.values():
            op_type = record.operation_type
            operation_counts[op_type] = operation_counts.get(op_type, 0) + 1
        
        return {
            "total_backups": total_backups,
            "confirmed_backups": confirmed_backups,
            "unconfirmed_backups": unconfirmed_backups,
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "operation_counts": operation_counts,
        }
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """
        Calculate SHA-256 hash of file.
        
        Args:
            file_path: File to hash
            
        Returns:
            Hexadecimal hash string
        """
        sha256_hash = hashlib.sha256()
        
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
            
            return sha256_hash.hexdigest()
            
        except Exception as exc:
            log.error("Failed to calculate hash for %s: %s", file_path, exc)
            return ""


class ReversibleFileOperation:
    """
    Context manager for reversible file operations.
    
    Ensures that file operations have backups and can be safely
    rolled back if needed.
    """
    
    def __init__(
        self,
        file_path: Path,
        operation_type: str,
        backup_manager: FileBackupManager,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize reversible file operation.
        
        Args:
            file_path: File to operate on
            operation_type: Type of operation
            backup_manager: File backup manager instance
            metadata: Additional metadata
        """
        self.file_path = file_path
        self.operation_type = operation_type
        self.backup_manager = backup_manager
        self.metadata = metadata or {}
        self._backup_id = None
        self._completed = False
    
    def __enter__(self) -> "ReversibleFileOperation":
        """Create backup before operation."""
        self._backup_id = self.backup_manager.create_backup(
            self.file_path,
            self.operation_type,
            self.metadata,
        )
        
        if not self._backup_id:
            raise RuntimeError(f"Failed to create backup for {self.file_path}")
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Handle operation completion or failure."""
        if exc_type is not None:
            # Operation failed, restore from backup
            if self._backup_id:
                log.warning("Operation failed, restoring from backup")
                self.backup_manager.restore_backup(self._backup_id)
        else:
            # Operation succeeded, mark as completed
            self._completed = True
    
    def confirm(self) -> None:
        """Confirm the operation (mark backup as safe to cleanup)."""
        if self._backup_id:
            self.backup_manager.confirm_backup(self._backup_id)
    
    def rollback(self) -> bool:
        """
        Manually rollback the operation.
        
        Returns:
            True if rollback succeeded
        """
        if self._backup_id:
            return self.backup_manager.restore_backup(self._backup_id)
        return False