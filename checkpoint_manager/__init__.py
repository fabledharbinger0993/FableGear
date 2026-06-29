"""
checkpoint_manager — Universal checkpoint/resume system for FableGear tools.

This module provides a robust base class for implementing checkpoint/resume
functionality across all FableGear tools. It builds on the existing checkpoint.py
system while adding standardized schemas, validation, and lifecycle management.

Key Features:
- Base CheckpointManager class for tool inheritance
- Standardized checkpoint schema with metadata
- Automatic checkpoint validation and cleanup
- Manual checkpoint creation support
- Integration with existing checkpoint.py infrastructure
- Thread-safe operations for concurrent tool execution

Example Usage:
    from checkpoint_manager import CheckpointManager

    class MyToolCheckpoint(CheckpointManager):
        def __init__(self, roots, config):
            super().__init__(
                tool_name="my_tool",
                roots=roots,
                config=config,
                checkpoint_interval=100  # Save every 100 items
            )

    # In tool implementation:
    ckpt = MyToolCheckpoint(roots, config)
    if ckpt.has_checkpoint():
        # Resume from checkpoint
        state = ckpt.load()
        # Restore state...

    # During processing:
    for i, item in enumerate(items):
        # Process item...
        if i % ckpt.checkpoint_interval == 0:
            ckpt.save_checkpoint({
                "processed_count": i,
                "last_processed": item,
                # ... other state
            })

    # On completion:
    ckpt.cleanup()
"""

from .base import CheckpointManager, CheckpointValidationError
from .schema import CheckpointSchema
from .cleanup import CheckpointCleanup

__all__ = [
    "CheckpointManager",
    "CheckpointSchema", 
    "CheckpointValidationError",
    "CheckpointCleanup",
]
