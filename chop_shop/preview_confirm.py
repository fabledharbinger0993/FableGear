"""
chop_shop.preview_confirm — Preview and confirm workflow for destructive operations.

Provides a workflow for destructive operations where users can preview
changes before committing them, ensuring no accidental modifications.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from chop_shop.file_backup import FileBackupManager, ReversibleFileOperation
from chop_shop.reversible_operations import ReversibleOperationManager

log = logging.getLogger(__name__)


class ConfirmAction(Enum):
    """User confirmation choices."""
    CONFIRM = "confirm"
    SKIP = "skip"
    CANCEL_ALL = "cancel_all"
    MODIFIED = "modified"  # User modified the preview


@dataclass
class PreviewResult:
    """Result of previewing an operation."""
    operation_id: str
    file_path: str
    operation_type: str
    preview_data: Dict[str, Any]
    backup_id: Optional[str] = None
    user_choice: Optional[ConfirmAction] = None
    user_modifications: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "operation_id": self.operation_id,
            "file_path": self.file_path,
            "operation_type": self.operation_type,
            "preview_data": self.preview_data,
            "backup_id": self.backup_id,
            "user_choice": self.user_choice.value if self.user_choice else None,
            "user_modifications": self.user_modifications,
            "metadata": self.metadata,
        }


class PreviewConfirmWorkflow:
    """
    Preview and confirm workflow for destructive operations.
    
    Provides a safe workflow where users can preview changes before
    committing them, with options to skip, modify, or cancel operations.
    """
    
    def __init__(
        self,
        backup_manager: Optional[FileBackupManager] = None,
        operation_manager: Optional[ReversibleOperationManager] = None,
    ):
        """
        Initialize preview/confirm workflow.
        
        Args:
            backup_manager: File backup manager
            operation_manager: Reversible operation manager
        """
        self.backup_manager = backup_manager or FileBackupManager()
        self.operation_manager = operation_manager or ReversibleOperationManager()
        self._previews: List[PreviewResult] = []
        self._cancelled = False
    
    def preview_operation(
        self,
        file_path: Path,
        operation_type: str,
        preview_function: Callable[[Path], Dict[str, Any]],
        operation_function: Callable[[Path], bool],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PreviewResult:
        """
        Preview an operation without committing it.
        
        Args:
            file_path: File to operate on
            operation_type: Type of operation
            preview_function: Function that generates preview data
            operation_function: Function that performs the actual operation
            metadata: Additional metadata
            
        Returns:
            PreviewResult with preview data
        """
        from datetime import datetime
        
        operation_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        try:
            # Generate preview data
            preview_data = preview_function(file_path)
            
            result = PreviewResult(
                operation_id=operation_id,
                file_path=str(file_path),
                operation_type=operation_type,
                preview_data=preview_data,
                metadata=metadata or {},
            )
            
            self._previews.append(result)
            log.info("Generated preview for operation %s", operation_id)
            
            return result
            
        except Exception as exc:
            log.error("Failed to generate preview for %s: %s", file_path, exc)
            # Return error result
            return PreviewResult(
                operation_id=operation_id,
                file_path=str(file_path),
                operation_type=operation_type,
                preview_data={"error": str(exc)},
                metadata=metadata or {},
            )
    
    def confirm_operation(
        self,
        preview_result: PreviewResult,
        operation_function: Callable[[Path], bool],
        user_modifications: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Commit a previewed operation.
        
        Args:
            preview_result: Preview result to commit
            operation_function: Function that performs the operation
            user_modifications: User modifications to preview data
            
        Returns:
            True if operation succeeded
        """
        if self._cancelled:
            log.info("Workflow cancelled, skipping operation")
            return False
        
        try:
            file_path = Path(preview_result.file_path)
            
            # Apply user modifications if provided
            if user_modifications:
                preview_result.user_modifications = user_modifications
            
            # Execute the operation with backup
            result = self.operation_manager.execute_reversible_operation(
                file_path,
                operation_function,
                preview_result.operation_type,
                metadata=preview_result.metadata,
                auto_confirm=False,  # Don't auto-confirm, let user review
            )
            
            if result.success:
                preview_result.backup_id = result.backup_id
                preview_result.user_choice = ConfirmAction.CONFIRM
                log.info("Confirmed and executed operation %s", preview_result.operation_id)
                return True
            else:
                preview_result.user_choice = ConfirmAction.SKIP
                log.warning("Operation failed: %s", result.error_message)
                return False
                
        except Exception as exc:
            log.error("Failed to confirm operation %s: %s", preview_result.operation_id, exc)
            preview_result.user_choice = ConfirmAction.SKIP
            return False
    
    def skip_operation(self, preview_result: PreviewResult) -> None:
        """
        Skip a previewed operation.
        
        Args:
            preview_result: Preview result to skip
        """
        preview_result.user_choice = ConfirmAction.SKIP
        log.info("Skipped operation %s", preview_result.operation_id)
    
    def cancel_workflow(self) -> None:
        """Cancel the entire workflow."""
        self._cancelled = True
        log.info("Workflow cancelled by user")
    
    def get_pending_previews(self) -> List[PreviewResult]:
        """
        Get previews that haven't been confirmed or skipped.
        
        Returns:
            List of pending PreviewResults
        """
        return [
            result for result in self._previews
            if result.user_choice is None
        ]
    
    def get_confirmed_operations(self) -> List[PreviewResult]:
        """
        Get operations that were confirmed.
        
        Returns:
            List of confirmed PreviewResults
        """
        return [
            result for result in self._previews
            if result.user_choice == ConfirmAction.CONFIRM
        ]
    
    def get_skipped_operations(self) -> List[PreviewResult]:
        """
        Get operations that were skipped.
        
        Returns:
            List of skipped PreviewResults
        """
        return [
            result for result in self._previews
            if result.user_choice == ConfirmAction.SKIP
        ]
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get workflow summary.
        
        Returns:
            Dictionary with workflow statistics
        """
        return {
            "total_previews": len(self._previews),
            "confirmed": len(self.get_confirmed_operations()),
            "skipped": len(self.get_skipped_operations()),
            "pending": len(self.get_pending_previews()),
            "cancelled": self._cancelled,
        }


# Preview functions for common operations

def preview_tag_operation(file_path: Path) -> Dict[str, Any]:
    """
    Generate preview data for tagging operation.
    
    Args:
        file_path: File to preview
        
    Returns:
        Dictionary with preview data
    """
    try:
        from mutagen import File as MutagenFile
        
        audio_file = MutagenFile(file_path)
        if not audio_file:
            return {"error": "Could not read file"}
        
        current_tags = {}
        if audio_file.tags:
            for key, value in audio_file.tags.items():
                current_tags[key] = str(value) if value else None
        
        return {
            "current_tags": current_tags,
            "file_size": file_path.stat().st_size,
            "format": file_path.suffix,
        }
        
    except Exception as exc:
        return {"error": str(exc)}


def preview_normalize_operation(file_path: Path, target_lufs: float = -8.0) -> Dict[str, Any]:
    """
    Generate preview data for normalization operation.
    
    Args:
        file_path: File to preview
        target_lufs: Target LUFS level
        
    Returns:
        Dictionary with preview data
    """
    try:
        # This would implement actual LUFS analysis
        # For now, placeholder
        return {
            "current_lufs": -12.5,  # Placeholder
            "target_lufs": target_lufs,
            "gain_needed": target_lufs - (-12.5),
            "file_size": file_path.stat().st_size,
            "format": file_path.suffix,
        }
        
    except Exception as exc:
        return {"error": str(exc)}


def preview_convert_operation(
    file_path: Path,
    target_format: str,
    quality: str = "high",
) -> Dict[str, Any]:
    """
    Generate preview data for conversion operation.
    
    Args:
        file_path: File to preview
        target_format: Target format
        quality: Conversion quality
        
    Returns:
        Dictionary with preview data
    """
    try:
        # This would implement actual conversion preview
        # For now, placeholder
        return {
            "current_format": file_path.suffix,
            "target_format": target_format,
            "quality": quality,
            "estimated_size": file_path.stat().st_size,  # Placeholder
            "file_size": file_path.stat().st_size,
        }
        
    except Exception as exc:
        return {"error": str(exc)}


# Convenience workflow functions

def tag_with_preview(
    file_path: Path,
    tags: Dict[str, Any],
    workflow: Optional[PreviewConfirmWorkflow] = None,
) -> PreviewResult:
    """
    Tag a file with preview and confirm workflow.
    
    Args:
        file_path: File to tag
        tags: Tags to set
        workflow: Preview/confirm workflow (default: create new)
        
    Returns:
        PreviewResult
    """
    workflow = workflow or PreviewConfirmWorkflow()
    
    # Generate preview
    preview = workflow.preview_operation(
        file_path,
        "tag",
        lambda fp: preview_tag_operation(fp),
        lambda fp: True,  # Placeholder
        metadata={"tags": tags},
    )
    
    # In a real implementation, this would prompt the user
    # For now, auto-confirm
    def tag_operation(fp: Path) -> bool:
        from chop_shop.reversible_operations import TagOperation
        return TagOperation.tag_file(fp, tags)
    
    workflow.confirm_operation(preview, tag_operation)
    return preview


def normalize_with_preview(
    file_path: Path,
    target_lufs: float = -8.0,
    workflow: Optional[PreviewConfirmWorkflow] = None,
) -> PreviewResult:
    """
    Normalize a file with preview and confirm workflow.
    
    Args:
        file_path: File to normalize
        target_lufs: Target LUFS level
        workflow: Preview/confirm workflow (default: create new)
        
    Returns:
        PreviewResult
    """
    workflow = workflow or PreviewConfirmWorkflow()
    
    # Generate preview
    preview = workflow.preview_operation(
        file_path,
        "normalize",
        lambda fp: preview_normalize_operation(fp, target_lufs),
        lambda fp: True,  # Placeholder
        metadata={"target_lufs": target_lufs},
    )
    
    # In a real implementation, this would prompt the user
    # For now, auto-confirm
    def normalize_operation(fp: Path) -> bool:
        from chop_shop.reversible_operations import NormalizeOperation
        return NormalizeOperation.normalize_file(fp, target_lufs)
    
    workflow.confirm_operation(preview, normalize_operation)
    return preview


def convert_with_preview(
    file_path: Path,
    target_format: str,
    quality: str = "high",
    workflow: Optional[PreviewConfirmWorkflow] = None,
) -> PreviewResult:
    """
    Convert a file with preview and confirm workflow.
    
    Args:
        file_path: File to convert
        target_format: Target format
        quality: Conversion quality
        workflow: Preview/confirm workflow (default: create new)
        
    Returns:
        PreviewResult
    """
    workflow = workflow or PreviewConfirmWorkflow()
    
    # Generate preview
    preview = workflow.preview_operation(
        file_path,
        "convert",
        lambda fp: preview_convert_operation(fp, target_format, quality),
        lambda fp: True,  # Placeholder
        metadata={"target_format": target_format, "quality": quality},
    )
    
    # In a real implementation, this would prompt the user
    # For now, auto-confirm
    def convert_operation(fp: Path) -> bool:
        from chop_shop.reversible_operations import ConvertOperation
        return ConvertOperation.convert_file(fp, target_format, quality=quality)
    
    workflow.confirm_operation(preview, convert_operation)
    return preview