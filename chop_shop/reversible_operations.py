"""
chop_shop.reversible_operations — Reversible operations framework for Chop Shop.

Provides a framework for making Chop Shop file editing operations
reversible through the file backup system, ensuring no decision is
permanent and all operations can be safely undone.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

from chop_shop.file_backup import FileBackupManager, ReversibleFileOperation

log = logging.getLogger(__name__)


@dataclass
class OperationResult:
    """Result of a reversible operation."""
    success: bool
    operation_id: str
    backup_id: Optional[str] = None
    original_path: Optional[str] = None
    modified_path: Optional[str] = None
    error_message: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ReversibleOperationManager:
    """
    Manages reversible file operations for Chop Shop tools.
    
    Provides a unified interface for making file editing operations
    reversible through automatic backup and rollback capabilities.
    """
    
    def __init__(self, backup_manager: Optional[FileBackupManager] = None):
        """
        Initialize reversible operation manager.
        
        Args:
            backup_manager: File backup manager (default: create new)
        """
        self.backup_manager = backup_manager or FileBackupManager()
        self._operation_history: List[OperationResult] = []
    
    def execute_reversible_operation(
        self,
        file_path: Path,
        operation: Callable[[Path], bool],
        operation_type: str,
        metadata: Optional[Dict[str, Any]] = None,
        auto_confirm: bool = False,
    ) -> OperationResult:
        """
        Execute a reversible file operation.
        
        Args:
            file_path: File to operate on
            operation: Function that performs the operation (returns True on success)
            operation_type: Type of operation (e.g., "tag", "normalize", "convert")
            metadata: Additional metadata
            auto_confirm: Whether to auto-confirm successful operations
            
        Returns:
            OperationResult with operation details
        """
        from datetime import datetime
        
        operation_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        result = OperationResult(
            success=False,
            operation_id=operation_id,
            original_path=str(file_path),
            metadata=metadata or {},
        )
        
        try:
            # Create reversible context
            with ReversibleFileOperation(
                file_path,
                operation_type,
                self.backup_manager,
                metadata,
            ) as reversible_op:
                # Execute the operation
                success = operation(file_path)
                
                if success:
                    result.success = True
                    result.backup_id = reversible_op._backup_id
                    
                    if auto_confirm:
                        reversible_op.confirm()
                    
                    log.info("Operation %s completed successfully", operation_id)
                else:
                    result.error_message = "Operation function returned False"
                    log.warning("Operation %s failed: %s", operation_id, result.error_message)
            
            self._operation_history.append(result)
            return result
            
        except Exception as exc:
            result.error_message = str(exc)
            log.error("Operation %s failed: %s", operation_id, exc)
            self._operation_history.append(result)
            return result
    
    def undo_operation(self, operation_id: str) -> bool:
        """
        Undo a previous operation.
        
        Args:
            operation_id: ID of operation to undo
            
        Returns:
            True if undo succeeded
        """
        operation = self._get_operation(operation_id)
        if not operation:
            log.error("Operation not found: %s", operation_id)
            return False
        
        if not operation.backup_id:
            log.error("Operation %s has no backup, cannot undo", operation_id)
            return False
        
        try:
            success = self.backup_manager.restore_backup(operation.backup_id)
            if success:
                log.info("Undid operation %s", operation_id)
            return success
            
        except Exception as exc:
            log.error("Failed to undo operation %s: %s", operation_id, exc)
            return False
    
    def confirm_operation(self, operation_id: str) -> bool:
        """
        Confirm an operation (mark backup as safe to cleanup).
        
        Args:
            operation_id: ID of operation to confirm
            
        Returns:
            True if confirmation succeeded
        """
        operation = self._get_operation(operation_id)
        if not operation:
            return False
        
        if not operation.backup_id:
            return False
        
        return self.backup_manager.confirm_backup(operation.backup_id)
    
    def get_operation(self, operation_id: str) -> Optional[OperationResult]:
        """
        Get an operation by ID.
        
        Args:
            operation_id: ID of operation
            
        Returns:
            OperationResult or None if not found
        """
        return self._get_operation(operation_id)
    
    def _get_operation(self, operation_id: str) -> Optional[OperationResult]:
        """Get operation by ID."""
        for operation in reversed(self._operation_history):
            if operation.operation_id == operation_id:
                return operation
        return None
    
    def get_recent_operations(self, limit: int = 10) -> List[OperationResult]:
        """
        Get recent operations.
        
        Args:
            limit: Maximum number of operations to return
            
        Returns:
            List of recent operations
        """
        return self._operation_history[-limit:]
    
    def can_undo(self) -> bool:
        """Check if there are operations to undo."""
        return any(op.backup_id for op in self._operation_history)


class TagOperation:
    """Reversible tagging operation."""
    
    @staticmethod
    def tag_file(file_path: Path, tags: Dict[str, Any]) -> bool:
        """
        Tag a file with metadata.
        
        Args:
            file_path: File to tag
            tags: Dictionary of tags to set
            
        Returns:
            True if tagging succeeded
        """
        try:
            from mutagen import File as MutagenFile
            
            audio_file = MutagenFile(file_path)
            if not audio_file:
                return False
            
            # Set tags
            if audio_file.tags is None:
                from mutagen.id3 import ID3
                audio_file.tags = ID3()
            
            for key, value in tags.items():
                audio_file.tags[key] = value
            
            audio_file.save()
            return True
            
        except Exception as exc:
            log.error("Failed to tag file %s: %s", file_path, exc)
            return False


class NormalizeOperation:
    """Reversible audio normalization operation."""
    
    @staticmethod
    def normalize_file(
        file_path: Path,
        target_lufs: float = -8.0,
        output_path: Optional[Path] = None,
    ) -> bool:
        """
        Normalize audio file to target LUFS.
        
        Args:
            file_path: File to normalize
            target_lufs: Target LUFS level
            output_path: Output path (default: overwrite original)
            
        Returns:
            True if normalization succeeded
        """
        try:
            # This would implement actual audio normalization
            # For now, placeholder
            log.info("Normalizing %s to %f LUFS", file_path, target_lufs)
            return True
            
        except Exception as exc:
            log.error("Failed to normalize file %s: %s", file_path, exc)
            return False


class ConvertOperation:
    """Reversible format conversion operation."""
    
    @staticmethod
    def convert_file(
        file_path: Path,
        target_format: str,
        output_path: Optional[Path] = None,
        quality: str = "high",
    ) -> bool:
        """
        Convert audio file to target format.
        
        Args:
            file_path: File to convert
            target_format: Target format (e.g., "mp3", "flac")
            output_path: Output path (default: same directory, new extension)
            quality: Conversion quality
            
        Returns:
            True if conversion succeeded
        """
        try:
            # This would implement actual audio conversion
            # For now, placeholder
            log.info("Converting %s to %s (quality: %s)", file_path, target_format, quality)
            return True
            
        except Exception as exc:
            log.error("Failed to convert file %s: %s", file_path, exc)
            return False


class RenameOperation:
    """Reversible file renaming operation."""
    
    @staticmethod
    def rename_file(
        file_path: Path,
        new_name: str,
        pattern: Optional[str] = None,
    ) -> bool:
        """
        Rename a file according to pattern.
        
        Args:
            file_path: File to rename
            new_name: New filename
            pattern: Renaming pattern used
            
        Returns:
            True if rename succeeded
        """
        try:
            new_path = file_path.parent / new_name
            file_path.rename(new_path)
            return True
            
        except Exception as exc:
            log.error("Failed to rename file %s: %s", file_path, exc)
            return False


# Convenience functions for common reversible operations

def reversible_tag(
    file_path: Path,
    tags: Dict[str, Any],
    manager: Optional[ReversibleOperationManager] = None,
) -> OperationResult:
    """
    Perform reversible file tagging.
    
    Args:
        file_path: File to tag
        tags: Tags to set
        manager: Operation manager (default: create new)
        
    Returns:
        OperationResult
    """
    manager = manager or ReversibleOperationManager()
    
    def tag_operation(path: Path) -> bool:
        return TagOperation.tag_file(path, tags)
    
    return manager.execute_reversible_operation(
        file_path,
        tag_operation,
        "tag",
        metadata={"tags": tags},
    )


def reversible_normalize(
    file_path: Path,
    target_lufs: float = -8.0,
    manager: Optional[ReversibleOperationManager] = None,
) -> OperationResult:
    """
    Perform reversible audio normalization.
    
    Args:
        file_path: File to normalize
        target_lufs: Target LUFS level
        manager: Operation manager (default: create new)
        
    Returns:
        OperationResult
    """
    manager = manager or ReversibleOperationManager()
    
    def normalize_operation(path: Path) -> bool:
        return NormalizeOperation.normalize_file(path, target_lufs)
    
    return manager.execute_reversible_operation(
        file_path,
        normalize_operation,
        "normalize",
        metadata={"target_lufs": target_lufs},
    )


def reversible_convert(
    file_path: Path,
    target_format: str,
    quality: str = "high",
    manager: Optional[ReversibleOperationManager] = None,
) -> OperationResult:
    """
    Perform reversible format conversion.
    
    Args:
        file_path: File to convert
        target_format: Target format
        quality: Conversion quality
        manager: Operation manager (default: create new)
        
    Returns:
        OperationResult
    """
    manager = manager or ReversibleOperationManager()
    
    def convert_operation(path: Path) -> bool:
        return ConvertOperation.convert_file(path, target_format, quality=quality)
    
    return manager.execute_reversible_operation(
        file_path,
        convert_operation,
        "convert",
        metadata={"target_format": target_format, "quality": quality},
    )