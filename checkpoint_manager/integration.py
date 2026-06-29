"""
checkpoint_manager.integration — Integration with existing tools.

Provides integration layer to connect the new checkpoint manager
with existing FableGear tools and the legacy checkpoint.py system.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class CheckpointIntegrator:
    """
    Integrates new checkpoint manager with existing tools.
    
    Provides compatibility layer between the new CheckpointManager
    system and existing tools that use the legacy checkpoint.py.
    """
    
    def __init__(self):
        """Initialize the checkpoint integrator."""
        self._tool_implementations = {}
        self._register_tool_implementations()
    
    def _register_tool_implementations(self) -> None:
        """Register checkpoint implementations for existing tools."""
        from checkpoint_manager.examples import (
            TagTracksCheckpoint,
            DuplicatesCheckpoint,
            NormalizeCheckpoint,
            ImportCheckpoint,
        )
        
        self._tool_implementations = {
            "tag_tracks": TagTracksCheckpoint,
            "duplicates": DuplicatesCheckpoint,
            "normalize": NormalizeCheckpoint,
            "import": ImportCheckpoint,
        }
    
    def get_checkpoint_manager(
        self,
        tool_name: str,
        roots: List[Path],
        config: Dict[str, Any],
    ) -> Optional["CheckpointManager"]:
        """
        Get checkpoint manager instance for a tool.
        
        Args:
            tool_name: Name of the tool
            roots: Root paths being processed
            config: Tool configuration
            
        Returns:
            CheckpointManager instance or None if not supported
        """
        implementation = self._tool_implementations.get(tool_name)
        if implementation:
            return implementation(roots, config)
        return None
    
    def is_tool_supported(self, tool_name: str) -> bool:
        """
        Check if a tool has checkpoint support.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            True if tool supports checkpoints
        """
        return tool_name in self._tool_implementations
    
    def get_supported_tools(self) -> List[str]:
        """Get list of tools with checkpoint support."""
        return list(self._tool_implementations.keys())
    
    def migrate_legacy_checkpoint(
        self,
        tool_name: str,
        roots: List[Path],
        config: Dict[str, Any],
    ) -> bool:
        """
        Migrate legacy checkpoint to new format.
        
        Args:
            tool_name: Name of the tool
            roots: Root paths
            config: Tool configuration
            
        Returns:
            True if migration succeeded
        """
        try:
            from checkpoint import Checkpoint
            
            # Load legacy checkpoint
            legacy_checkpoint = Checkpoint(tool_name, roots, config)
            legacy_data = legacy_checkpoint.load()
            
            if not legacy_data:
                return True  # Nothing to migrate
            
            # Create new checkpoint manager
            new_manager = self.get_checkpoint_manager(tool_name, roots, config)
            if not new_manager:
                return False
            
            # Convert data format
            converted_data = self._convert_checkpoint_data(legacy_data, tool_name)
            
            # Save in new format
            success = new_manager.save_checkpoint(converted_data)
            
            if success:
                # Remove legacy checkpoint
                legacy_checkpoint.reset()
                log.info("Migrated legacy checkpoint for %s", tool_name)
            
            return success
            
        except Exception as exc:
            log.error("Failed to migrate legacy checkpoint: %s", exc)
            return False
    
    def _convert_checkpoint_data(
        self,
        legacy_data: Dict[str, Any],
        tool_name: str,
    ) -> Dict[str, Any]:
        """
        Convert legacy checkpoint data to new format.
        
        Args:
            legacy_data: Legacy checkpoint data
            tool_name: Name of the tool
            
        Returns:
            Converted checkpoint data
        """
        # Map legacy field names to new schema
        field_mapping = {
            "fp_map": "processed_files",
            "completed": "processed_count",
            "total": "total_count",
        }
        
        converted = {
            "tool_state": {},
            "processed_count": 0,
            "total_count": None,
        }
        
        for legacy_key, new_key in field_mapping.items():
            if legacy_key in legacy_data:
                converted[new_key] = legacy_data[legacy_key]
                if new_key == "processed_count":
                    converted["tool_state"][new_key] = legacy_data[legacy_key]
        
        # Preserve any other legacy data in tool_state
        for key, value in legacy_data.items():
            if key not in field_mapping and key not in ["tool", "roots", "config", "saved_at"]:
                converted["tool_state"][key] = value
        
        return converted