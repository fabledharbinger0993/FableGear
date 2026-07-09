"""
pipeline_wizard.checkpoint_integration — Pipeline-level checkpoint management.

Integrates the tool-level checkpoint system with pipeline orchestration
to enable pipeline-level resume and recovery capabilities.
"""

import json
import logging
import gzip
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class PipelineCheckpointManager:
    """
    Manages checkpoint operations at the pipeline level.
    
    Coordinates checkpoint operations across multiple tools,
    enables pipeline resume from any point, and provides
    checkpoint cleanup and validation.
    """
    
    def __init__(self, pipeline_name: str, pipeline_config: Dict[str, Any]):
        """
        Initialize pipeline checkpoint manager.
        
        Args:
            pipeline_name: Name of the pipeline
            pipeline_config: Pipeline configuration dictionary
        """
        self.pipeline_name = pipeline_name
        self.pipeline_config = pipeline_config
        self._checkpoint_dir = Path.home() / ".fablegear" / "pipeline_checkpoints"
        self._checkpoint_path = self._checkpoint_dir / f"{pipeline_name}.json.gz"
        self._legacy_checkpoint_path = self._checkpoint_dir / f"{pipeline_name}.json"
        
    def save_pipeline_checkpoint(
        self,
        completed_tools: List[str],
        current_tool_index: int,
        tool_results: List[Dict[str, Any]],
    ) -> bool:
        """
        Save pipeline execution state.
        
        Args:
            completed_tools: List of completed tool names
            current_tool_index: Index of current tool in pipeline
            tool_results: Results from completed tools
            
        Returns:
            True if save succeeded
        """
        try:
            self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
            
            checkpoint_data = {
                "pipeline_name": self.pipeline_name,
                "pipeline_config": self.pipeline_config,
                "completed_tools": completed_tools,
                "current_tool_index": current_tool_index,
                "tool_results": tool_results,
                "saved_at": datetime.now().isoformat(),
                "version": "1.0",
            }
            
            # Atomic write
            tmp_path = self._checkpoint_path.with_name(self._checkpoint_path.name + ".tmp")
            with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2)
            tmp_path.replace(self._checkpoint_path)
            
            log.info("Pipeline checkpoint saved: %s", self.pipeline_name)
            return True
            
        except Exception as exc:
            log.error("Failed to save pipeline checkpoint: %s", exc)
            return False
    
    def load_pipeline_checkpoint(self) -> Optional[Dict[str, Any]]:
        """
        Load pipeline execution state.
        
        Returns:
            Checkpoint data or None if no checkpoint exists
        """
        if not self._checkpoint_path.exists() and not self._legacy_checkpoint_path.exists():
            return None
        
        try:
            path = self._checkpoint_path if self._checkpoint_path.exists() else self._legacy_checkpoint_path
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            
            # Validate checkpoint
            if not self._validate_checkpoint(data):
                log.warning("Invalid pipeline checkpoint, ignoring")
                return None
            
            log.info("Pipeline checkpoint loaded: %s", self.pipeline_name)
            return data
            
        except Exception as exc:
            log.error("Failed to load pipeline checkpoint: %s", exc)
            return None
    
    def has_checkpoint(self) -> bool:
        """Check if a pipeline checkpoint exists (compressed or legacy)."""
        return self._checkpoint_path.exists() or self._legacy_checkpoint_path.exists()
    
    def get_checkpoint_info(self) -> Dict[str, Any]:
        """
        Get pipeline checkpoint metadata.
        
        Returns:
            Checkpoint info dictionary
        """
        if not self.has_checkpoint():
            return {"exists": False}
        
        try:
            data = self.load_pipeline_checkpoint()
            if not data:
                return {"exists": True, "readable": False}
            
            return {
                "exists": True,
                "pipeline_name": data.get("pipeline_name"),
                "saved_at": data.get("saved_at"),
                "completed_tools": data.get("completed_tools", []),
                "current_tool_index": data.get("current_tool_index"),
                "total_tools": len(data.get("pipeline_config", {}).get("tools", [])),
            }
            
        except Exception as exc:
            log.error("Failed to get checkpoint info: %s", exc)
            return {"exists": True, "readable": False}
    
    def cleanup(self) -> None:
        """Remove pipeline checkpoint."""
        try:
            self._checkpoint_path.unlink(missing_ok=True)
            self._legacy_checkpoint_path.unlink(missing_ok=True)
            log.info("Pipeline checkpoint cleaned up: %s", self.pipeline_name)
        except Exception as exc:
            log.error("Failed to cleanup pipeline checkpoint: %s", exc)
    
    def _validate_checkpoint(self, data: Dict[str, Any]) -> bool:
        """
        Validate pipeline checkpoint structure.
        
        Args:
            data: Checkpoint data to validate
            
        Returns:
            True if valid
        """
        required_fields = {
            "pipeline_name",
            "pipeline_config",
            "completed_tools",
            "current_tool_index",
            "saved_at",
        }
        
        if not required_fields.issubset(set(data.keys())):
            return False
        
        # Validate pipeline name matches
        if data.get("pipeline_name") != self.pipeline_name:
            return False
        
        return True
    
    def get_resume_point(self) -> Optional[str]:
        """
        Determine which tool to resume from.
        
        Returns:
            Name of tool to resume from, or None if no checkpoint
        """
        checkpoint = self.load_pipeline_checkpoint()
        if not checkpoint:
            return None
        
        current_index = checkpoint.get("current_tool_index", 0)
        pipeline_config = checkpoint.get("pipeline_config", {})
        tools = pipeline_config.get("tools", [])
        
        if current_index < len(tools):
            return tools[current_index].get("name")
        
        return None
    
    def estimate_remaining_time(self) -> Optional[float]:
        """
        Estimate remaining execution time from checkpoint.
        
        Returns:
            Estimated remaining time in seconds, or None if no checkpoint
        """
        from pipeline_wizard.tool_registry import ToolRegistry
        
        checkpoint = self.load_pipeline_checkpoint()
        if not checkpoint:
            return None
        
        current_index = checkpoint.get("current_tool_index", 0)
        pipeline_config = checkpoint.get("pipeline_config", {})
        tools = pipeline_config.get("tools", [])
        roots = pipeline_config.get("roots", [])
        
        registry = ToolRegistry()
        total_time = 0.0
        
        # Estimate time for remaining tools
        for i in range(current_index, len(tools)):
            tool_config = tools[i]
            tool_name = tool_config.get("name")
            tool_def = registry.get_tool(tool_name)
            
            if tool_def:
                # Assume 1000 items per tool for estimation
                total_time += tool_def.estimated_time_per_item * 1000
        
        return total_time