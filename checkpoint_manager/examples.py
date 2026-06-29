"""
checkpoint_manager.examples — Example checkpoint implementations for tools.

Demonstrates how to implement checkpoint/resume functionality for specific
FableGear tools using the CheckpointManager base class.
"""

from pathlib import Path
from typing import Any, Dict, List

from checkpoint_manager import CheckpointManager, CheckpointSchema
from checkpoint_manager.schema import ToolSpecificSchema


class TagTracksCheckpoint(CheckpointManager):
    """Checkpoint manager for Tag Tracks tool."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="tag_tracks",
            roots=roots,
            config=config,
            checkpoint_interval=50,  # Save every 50 files
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate tag tracks checkpoint data."""
        errors = CheckpointSchema.validate(data)
        
        # Validate tool-specific state
        tool_state = data.get("tool_state", {})
        
        if "processed_files" in tool_state:
            if not isinstance(tool_state["processed_files"], list):
                errors.append("processed_files must be a list")
        
        if "failed_files" in tool_state:
            if not isinstance(tool_state["failed_files"], list):
                errors.append("failed_files must be a list")
        
        return errors
    
    def get_state_summary(self) -> Dict[str, Any]:
        """Get summary of current tagging state."""
        return {
            "tool_name": self.tool_name,
            "operation_count": self._operation_count,
            "has_checkpoint": self.has_checkpoint(),
            "roots": [str(r) for r in self.roots],
            "config_summary": {
                "analyze_bpm": self.config.get("analyze_bpm", True),
                "analyze_key": self.config.get("analyze_key", True),
            }
        }


class DuplicatesCheckpoint(CheckpointManager):
    """Checkpoint manager for Duplicates tool."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="duplicates",
            roots=roots,
            config=config,
            checkpoint_interval=25,  # Save every 25 fingerprint batches
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate duplicates checkpoint data."""
        errors = CheckpointSchema.validate(data)
        
        tool_state = data.get("tool_state", {})
        
        if "fingerprinted_files" in tool_state:
            if not isinstance(tool_state["fingerprinted_files"], int):
                errors.append("fingerprinted_files must be an integer")
        
        if "duplicate_groups" in tool_state:
            if not isinstance(tool_state["duplicate_groups"], list):
                errors.append("duplicate_groups must be a list")
        
        return errors
    
    def get_state_summary(self) -> Dict[str, Any]:
        """Get summary of current duplicate detection state."""
        return {
            "tool_name": self.tool_name,
            "operation_count": self._operation_count,
            "has_checkpoint": self.has_checkpoint(),
            "roots": [str(r) for r in self.roots],
            "config_summary": {
                "match_mode": self.config.get("match_mode", "exact"),
                "fuzzy_threshold": self.config.get("fuzzy_threshold", 0.85),
            }
        }


class NormalizeCheckpoint(CheckpointManager):
    """Checkpoint manager for Normalize Loudness tool."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="normalize",
            roots=roots,
            config=config,
            checkpoint_interval=10,  # Save every 10 files (re-encoding is expensive)
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate normalize checkpoint data."""
        errors = CheckpointSchema.validate(data)
        
        tool_state = data.get("tool_state", {})
        
        if "normalized_files" in tool_state:
            if not isinstance(tool_state["normalized_files"], list):
                errors.append("normalized_files must be a list")
        
        if "skipped_files" in tool_state:
            if not isinstance(tool_state["skipped_files"], list):
                errors.append("skipped_files must be a list")
        
        return errors
    
    def get_state_summary(self) -> Dict[str, Any]:
        """Get summary of current normalization state."""
        return {
            "tool_name": self.tool_name,
            "operation_count": self._operation_count,
            "has_checkpoint": self.has_checkpoint(),
            "roots": [str(r) for r in self.roots],
            "config_summary": {
                "target_lufs": self.config.get("target_lufs", -8.0),
                "lufs_tolerance": self.config.get("lufs_tolerance", 1.0),
            }
        }


class ImportCheckpoint(CheckpointManager):
    """Checkpoint manager for Import to Rekordbox tool."""
    
    def __init__(self, roots: List[Path], config: Dict[str, Any]):
        super().__init__(
            tool_name="import",
            roots=roots,
            config=config,
            checkpoint_interval=100,  # Save every 100 imported tracks
        )
    
    def validate_checkpoint(self, data: Dict[str, Any]) -> List[str]:
        """Validate import checkpoint data."""
        errors = CheckpointSchema.validate(data)
        
        tool_state = data.get("tool_state", {})
        
        if "imported_tracks" in tool_state:
            if not isinstance(tool_state["imported_tracks"], list):
                errors.append("imported_tracks must be a list")
        
        if "playlist_mappings" in tool_state:
            if not isinstance(tool_state["playlist_mappings"], dict):
                errors.append("playlist_mappings must be a dict")
        
        return errors
    
    def get_state_summary(self) -> Dict[str, Any]:
        """Get summary of current import state."""
        return {
            "tool_name": self.tool_name,
            "operation_count": self._operation_count,
            "has_checkpoint": self.has_checkpoint(),
            "roots": [str(r) for r in self.roots],
            "config_summary": {
                "create_playlists": self.config.get("create_playlists", True),
                "analyze_on_import": self.config.get("analyze_on_import", False),
            }
        }


# Example usage demonstrations
def example_tag_tracks_usage():
    """Demonstrate Tag Tracks checkpoint usage."""
    from pathlib import Path
    
    roots = [Path("/Music/Library")]
    config = {"analyze_bpm": True, "analyze_key": True}
    
    # Initialize checkpoint manager
    ckpt = TagTracksCheckpoint(roots, config)
    
    # Check for existing checkpoint
    if ckpt.has_checkpoint():
        print("Found existing checkpoint")
        info = ckpt.get_checkpoint_info()
        print(f"Checkpoint info: {info}")
        
        # Load and resume
        state = ckpt.load_checkpoint()
        processed_files = state.get("tool_state", {}).get("processed_files", [])
        print(f"Resuming from {len(processed_files)} processed files")
    else:
        print("No existing checkpoint, starting fresh")
        processed_files = []
    
    # Simulate processing
    for i, file in enumerate(range(200)):  # Simulate 200 files
        # Process file...
        processed_files.append(f"file_{i}.mp3")
        
        # Auto-save every 50 files
        ckpt.auto_checkpoint({
            "tool_state": {
                "processed_files": processed_files,
                "failed_files": [],
            }
        })
    
    # Manual checkpoint (user requested)
    ckpt.manual_checkpoint({
        "tool_state": {
            "processed_files": processed_files,
            "failed_files": [],
        }
    })
    
    # On completion, cleanup
    ckpt.cleanup()
    print("Processing complete, checkpoint cleaned up")


def example_duplicates_usage():
    """Demonstrate Duplicates checkpoint usage."""
    from pathlib import Path
    
    roots = [Path("/Music/Drive1"), Path("/Music/Drive2")]
    config = {"match_mode": "fuzzy", "fuzzy_threshold": 0.85}
    
    ckpt = DuplicatesCheckpoint(roots, config)
    
    if ckpt.has_checkpoint():
        state = ckpt.load_checkpoint()
        fingerprinted_count = state.get("tool_state", {}).get("fingerprinted_files", 0)
        print(f"Resuming from {fingerprinted_count} fingerprinted files")
    else:
        fingerprinted_count = 0
    
    # Simulate fingerprinting
    for i in range(100):
        fingerprinted_count += 1
        
        ckpt.auto_checkpoint({
            "tool_state": {
                "fingerprinted_files": fingerprinted_count,
                "duplicate_groups": [],
            }
        })
    
    ckpt.cleanup()


if __name__ == "__main__":
    print("Tag Tracks Example:")
    example_tag_tracks_usage()
    
    print("\nDuplicates Example:")
    example_duplicates_usage()