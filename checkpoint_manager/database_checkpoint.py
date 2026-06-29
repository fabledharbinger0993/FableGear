"""
checkpoint_manager.database_checkpoint — Database-level checkpoint integration.

Integrates the checkpoint system with the FableGear database for
database-level state snapshots and recovery.
"""

import logging
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from checkpoint_manager.base import CheckpointManager, CheckpointValidationError
from checkpoint_manager.schema import CheckpointSchema

try:
    from fablegear_database import FableGearDatabase
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

log = logging.getLogger(__name__)


class DatabaseCheckpoint(CheckpointManager):
    """
    Checkpoint manager for database operations.
    
    Provides database-level checkpointing for Record Room tools,
    enabling safe database state snapshots and recovery.
    """
    
    def __init__(
        self,
        tool_name: str,
        roots: List[Path],
        config: Dict[str, Any],
        checkpoint_interval: int = 50,
        manual_checkpoint_enabled: bool = True,
    ):
        """
        Initialize database checkpoint manager.
        
        Args:
            tool_name: Name of the tool
            roots: Root paths being processed
            config: Tool configuration
            checkpoint_interval: Operations between checkpoints
            manual_checkpoint_enabled: Allow manual checkpoints
        """
        super().__init__(tool_name, roots, config, checkpoint_interval, manual_checkpoint_enabled)
        
        self._db: Optional[FableGearDatabase] = None
        if DATABASE_AVAILABLE:
            try:
                self._db = FableGearDatabase()
            except Exception as exc:
                log.warning("Failed to initialize database for checkpointing: %s", exc)
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """
        Validate database checkpoint data.
        
        Args:
            data: Checkpoint data to validate
            
        Returns:
            List of validation errors
        """
        errors = CheckpointSchema.validate(data)
        
        # Validate database-specific fields
        tool_state = data.get("tool_state", {})
        
        if "database_backup_path" in tool_state:
            backup_path = Path(tool_state["database_backup_path"])
            if not backup_path.exists():
                errors.append(f"Database backup file not found: {backup_path}")
        
        if "transaction_log" in tool_state:
            if not isinstance(tool_state["transaction_log"], list):
                errors.append("Transaction log must be a list")
        
        return errors
    
    def get_state_summary(self) -> Dict[str, Any]:
        """
        Get summary of current database state.
        
        Returns:
            Dictionary with state summary
        """
        summary = {
            "tool_name": self.tool_name,
            "operation_count": self._operation_count,
            "has_checkpoint": self.has_checkpoint(),
            "roots": [str(r) for r in self.roots],
            "database_available": self._db is not None,
        }
        
        if self._db:
            try:
                stats = self._db.get_statistics()
                summary["database_stats"] = stats
            except Exception as exc:
                log.error("Failed to get database statistics: %s", exc)
        
        return summary
    
    def save_checkpoint(self, state: Dict[str, Any]) -> bool:
        """
        Save checkpoint with database state.
        
        Args:
            state: Tool state to persist
            
        Returns:
            True if save succeeded
        """
        # Create database backup before saving checkpoint
        if self._db:
            try:
                backup_path = self._db.create_backup()
                state["tool_state"]["database_backup_path"] = str(backup_path)
                log.info("Created database backup for checkpoint: %s", backup_path)
            except Exception as exc:
                log.error("Failed to create database backup: %s", exc)
                return False
        
        # Save using parent class
        return super().save_checkpoint(state)
    
    def load_checkpoint(self) -> Dict[str, Any]:
        """
        Load checkpoint and restore database state.
        
        Returns:
            Checkpoint data or empty dict if no checkpoint
        """
        data = super().load_checkpoint()
        
        if not data:
            return {}
        
        # Restore database from backup if available
        tool_state = data.get("tool_state", {})
        backup_path = tool_state.get("database_backup_path")
        
        if backup_path and self._db:
            try:
                success = self._db.restore_backup(Path(backup_path))
                if success:
                    log.info("Restored database from checkpoint backup: %s", backup_path)
                else:
                    log.warning("Failed to restore database from backup")
            except Exception as exc:
                log.error("Failed to restore database: %s", exc)
        
        return data


class ImportCheckpoint(DatabaseCheckpoint):
    """Checkpoint manager for import operations."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="import",
            roots=roots,
            config=config,
            checkpoint_interval=100,  # Save every 100 files
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate import checkpoint data."""
        errors = super().validate_checkpoint(data)
        
        tool_state = data.get("tool_state", {})
        
        if "imported_files" in tool_state:
            if not isinstance(tool_state["imported_files"], list):
                errors.append("imported_files must be a list")
        
        if "failed_files" in tool_state:
            if not isinstance(tool_state["failed_files"], list):
                errors.append("failed_files must be a list")
        
        return errors


class DuplicatesCheckpoint(DatabaseCheckpoint):
    """Checkpoint manager for duplicate detection operations."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="duplicates",
            roots=roots,
            config=config,
            checkpoint_interval=25,  # Save every 25 fingerprint batches
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate duplicates checkpoint data."""
        errors = super().validate_checkpoint(data)
        
        tool_state = data.get("tool_state", {})
        
        if "fingerprinted_files" in tool_state:
            if not isinstance(tool_state["fingerprinted_files"], int):
                errors.append("fingerprinted_files must be an integer")
        
        if "duplicate_groups" in tool_state:
            if not isinstance(tool_state["duplicate_groups"], list):
                errors.append("duplicate_groups must be a list")
        
        return errors


class LibraryExportCheckpoint(DatabaseCheckpoint):
    """Checkpoint manager for library export operations."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="library_export",
            roots=roots,
            config=config,
            checkpoint_interval=50,  # Save every 50 exported tracks
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate library export checkpoint data."""
        errors = super().validate_checkpoint(data)
        
        tool_state = data.get("tool_state", {})
        
        if "exported_files" in tool_state:
            if not isinstance(tool_state["exported_files"], list):
                errors.append("exported_files must be a list")
        
        if "export_format" in tool_state:
            if not isinstance(tool_state["export_format"], str):
                errors.append("export_format must be a string")
        
        return errors