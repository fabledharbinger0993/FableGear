"""
checkpoint_manager.base — Base CheckpointManager class for tool inheritance.

Provides the foundation for implementing checkpoint/resume functionality
across all FableGear tools with standardized interfaces and safety guarantees.
"""

import json
import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from checkpoint import Checkpoint, check_checkpoint
except ImportError:
    # For testing or when module structure changes
    Checkpoint = None
    check_checkpoint = None
    log = logging.getLogger(__name__)
else:
    log = logging.getLogger(__name__)


class CheckpointManager(ABC):
    """
    Base class for tool-specific checkpoint management.
    
    Provides standardized checkpoint lifecycle management while allowing
    tools to define their own checkpoint schemas and validation logic.
    
    Attributes:
        tool_name: Unique identifier for the tool (e.g., "tag_tracks", "duplicates")
        roots: List of root paths being processed
        config: Tool configuration dict
        checkpoint_interval: Number of operations between automatic checkpoints
        manual_checkpoint_enabled: Whether user can create manual checkpoints
    """
    
    def __init__(
        self,
        tool_name: str,
        roots: list[Path],
        config: Dict[str, Any],
        checkpoint_interval: int = 100,
        manual_checkpoint_enabled: bool = True,
    ):
        """
        Initialize the checkpoint manager.
        
        Args:
            tool_name: Unique identifier for this tool
            roots: List of root paths being processed
            config: Tool configuration parameters
            checkpoint_interval: Operations between automatic checkpoints
            manual_checkpoint_enabled: Allow user-initiated checkpoints
        """
        self.tool_name = tool_name
        self.roots = [Path(r) for r in roots]
        self.config = config
        self.checkpoint_interval = checkpoint_interval
        self.manual_checkpoint_enabled = manual_checkpoint_enabled
        
        # Initialize underlying checkpoint system
        if Checkpoint is None:
            raise RuntimeError("checkpoint module not available")
        self._checkpoint = Checkpoint(tool_name, roots, config)
        self._lock = threading.Lock()
        self._operation_count = 0
        self._last_checkpoint_time = None
        
        log.debug(
            "CheckpointManager initialized for %s with %d roots, interval=%d",
            tool_name,
            len(roots),
            checkpoint_interval
        )
    
    def has_checkpoint(self) -> bool:
        """Check if a checkpoint exists for this tool configuration."""
        return self._checkpoint.exists()
    
    def get_checkpoint_info(self) -> Dict[str, Any]:
        """
        Get lightweight checkpoint metadata without loading full data.
        
        Returns:
            Dict with checkpoint info or empty dict if no checkpoint exists
        """
        if check_checkpoint is None:
            return self._checkpoint.info()
        return check_checkpoint(self.tool_name, self.roots, self.config)
    
    def load_checkpoint(self) -> Dict[str, Any]:
        """
        Load checkpoint data for resuming.
        
        Returns:
            Dict with checkpoint data, empty dict if no checkpoint exists
            
        Raises:
            CheckpointValidationError: If checkpoint data is invalid
        """
        data = self._checkpoint.load()
        if not data:
            return {}
        
        # Validate checkpoint schema
        validation_errors = self.validate_checkpoint(data)
        if validation_errors:
            raise CheckpointValidationError(
                f"Checkpoint validation failed: {validation_errors}"
            )
        
        log.info(
            "Loaded checkpoint for %s: %d operations completed",
            self.tool_name,
            data.get("processed_count", 0)
        )
        
        # Restore internal state
        self._operation_count = data.get("operation_count", 0)
        self._last_checkpoint_time = data.get("last_checkpoint_time")
        
        return data
    
    def save_checkpoint(self, state: Dict[str, Any]) -> bool:
        """
        Save checkpoint with current tool state.
        
        Args:
            state: Tool-specific state data to persist
            
        Returns:
            True if save succeeded, False otherwise
        """
        with self._lock:
            try:
                # Merge tool state with standard metadata
                checkpoint_data = {
                    "tool_name": self.tool_name,
                    "operation_count": self._operation_count,
                    "last_checkpoint_time": datetime.now().isoformat(),
                    "roots": [str(r) for r in self.roots],
                    "config": self.config,
                    **state
                }
                
                # Validate before saving
                validation_errors = self.validate_checkpoint(checkpoint_data)
                if validation_errors:
                    log.warning(
                        "Checkpoint validation failed, saving anyway: %s",
                        validation_errors
                    )
                
                self._checkpoint.save(checkpoint_data)
                self._last_checkpoint_time = datetime.now()
                
                log.debug(
                    "Checkpoint saved for %s at operation %d",
                    self.tool_name,
                    self._operation_count
                )
                return True
                
            except Exception as exc:
                log.error("Failed to save checkpoint: %s", exc)
                return False
    
    def auto_checkpoint(self, state: Dict[str, Any]) -> bool:
        """
        Save checkpoint automatically based on operation count.
        
        Args:
            state: Current tool state to persist
            
        Returns:
            True if checkpoint was saved, False otherwise
        """
        self._operation_count += 1
        
        if self._operation_count % self.checkpoint_interval == 0:
            return self.save_checkpoint(state)
        
        return False
    
    def manual_checkpoint(self, state: Dict[str, Any]) -> bool:
        """
        Create a manual checkpoint (user-initiated).
        
        Args:
            state: Current tool state to persist
            
        Returns:
            True if checkpoint succeeded, False otherwise
            
        Raises:
            RuntimeError: If manual checkpoints are disabled
        """
        if not self.manual_checkpoint_enabled:
            raise RuntimeError("Manual checkpoints are disabled for this tool")
        
        log.info("Creating manual checkpoint for %s", self.tool_name)
        return self.save_checkpoint(state)
    
    def cleanup(self) -> None:
        """Remove checkpoint on successful completion."""
        with self._lock:
            self._checkpoint.reset()
            self._operation_count = 0
            self._last_checkpoint_time = None
            log.info("Checkpoint cleaned up for %s", self.tool_name)
    
    def should_checkpoint(self) -> bool:
        """
        Determine if a checkpoint should be created based on heuristics.
        
        Returns:
            True if checkpoint conditions are met
        """
        # Check operation count
        if self._operation_count % self.checkpoint_interval == 0:
            return True
        
        # Check time since last checkpoint (5 minutes)
        if self._last_checkpoint_time:
            elapsed = (datetime.now() - 
                      datetime.fromisoformat(self._last_checkpoint_time))
            if elapsed.total_seconds() > 300:  # 5 minutes
                return True
        
        return False
    
    @abstractmethod
    def validate_checkpoint(self, data: Dict[str, Any]) -> list[str]:
        """
        Validate checkpoint data structure and integrity.
        
        Args:
            data: Checkpoint data to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        pass
    
    @abstractmethod
    def get_state_summary(self) -> Dict[str, Any]:
        """
        Get a summary of current tool state for UI display.
        
        Returns:
            Dict with state summary information
        """
        pass
    
    def get_checkpoint_path(self) -> Path:
        """Get the filesystem path for this checkpoint."""
        return self._checkpoint.path


class CheckpointValidationError(Exception):
    """Raised when checkpoint data fails validation."""
    pass