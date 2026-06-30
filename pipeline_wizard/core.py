"""
pipeline_wizard.core — Core pipeline wizard classes.

Defines the fundamental data structures and orchestration logic for
the Pipeline Wizard system.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

log = logging.getLogger(__name__)


class PipelineMode(Enum):
    """Pipeline execution modes."""
    AUTO = "auto"           # Run all tools without pauses
    CONFIRM = "confirm"     # Pause after each tool for review
    INTERACTIVE = "interactive"  # User can skip/retry/modify between tools


@dataclass
class ToolConfig:
    """Configuration for a single tool in a pipeline."""
    name: str                      # Tool identifier (e.g., "tag_tracks")
    config: Dict[str, Any] = field(default_factory=dict)  # Tool-specific parameters
    enabled: bool = True           # Whether this tool is enabled
    checkpoint_enabled: bool = True  # Whether to enable checkpointing
    depends_on: List[str] = field(default_factory=list)  # Tools that must complete first
    retry_on_failure: bool = False  # Whether to retry on failure
    max_retries: int = 3           # Maximum retry attempts


@dataclass
class PipelineConfig:
    """Complete pipeline configuration."""
    name: str                      # Pipeline name for identification
    tools: List[ToolConfig] = field(default_factory=list)  # Ordered tool list
    mode: PipelineMode = PipelineMode.AUTO  # Execution mode
    roots: List[Path] = field(default_factory=list)  # Root paths to process
    global_config: Dict[str, Any] = field(default_factory=dict)  # Global settings
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    description: str = ""           # Human-readable description
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert pipeline config to dictionary for serialization."""
        return {
            "name": self.name,
            "tools": [
                {
                    "name": tool.name,
                    "config": tool.config,
                    "enabled": tool.enabled,
                    "checkpoint_enabled": tool.checkpoint_enabled,
                    "depends_on": tool.depends_on,
                    "retry_on_failure": tool.retry_on_failure,
                    "max_retries": tool.max_retries,
                }
                for tool in self.tools
            ],
            "mode": self.mode.value,
            "roots": [str(r) for r in self.roots],
            "global_config": self.global_config,
            "created_at": self.created_at,
            "description": self.description,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineConfig":
        """Create pipeline config from dictionary."""
        tools = [
            ToolConfig(
                name=tool_data["name"],
                config=tool_data.get("config", {}),
                enabled=tool_data.get("enabled", True),
                checkpoint_enabled=tool_data.get("checkpoint_enabled", True),
                depends_on=tool_data.get("depends_on", []),
                retry_on_failure=tool_data.get("retry_on_failure", False),
                max_retries=tool_data.get("max_retries", 3),
            )
            for tool_data in data.get("tools", [])
        ]
        
        return cls(
            name=data["name"],
            tools=tools,
            mode=PipelineMode(data.get("mode", "auto")),
            roots=[Path(r) for r in data.get("roots", [])],
            global_config=data.get("global_config", {}),
            created_at=data.get("created_at", datetime.now().isoformat()),
            description=data.get("description", ""),
        )


@dataclass
class ToolResult:
    """Result of executing a single tool in a pipeline."""
    tool_name: str
    success: bool
    execution_time_seconds: float
    processed_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    error_message: str = ""
    checkpoint_used: bool = False
    checkpoint_created: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert tool result to dictionary."""
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "execution_time_seconds": self.execution_time_seconds,
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "skipped_count": self.skipped_count,
            "error_message": self.error_message,
            "checkpoint_used": self.checkpoint_used,
            "checkpoint_created": self.checkpoint_created,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class PipelineResult:
    """Result of executing a complete pipeline."""
    pipeline_name: str
    success: bool
    total_execution_time_seconds: float
    tool_results: List[ToolResult] = field(default_factory=list)
    total_processed: int = 0
    total_errors: int = 0
    interrupted: bool = False
    interruption_reason: str = ""
    started_at: str = ""
    completed_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert pipeline result to dictionary."""
        return {
            "pipeline_name": self.pipeline_name,
            "success": self.success,
            "total_execution_time_seconds": self.total_execution_time_seconds,
            "tool_results": [tr.to_dict() for tr in self.tool_results],
            "total_processed": self.total_processed,
            "total_errors": self.total_errors,
            "interrupted": self.interrupted,
            "interruption_reason": self.interruption_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }
    
    def get_summary(self) -> str:
        """Get human-readable summary of pipeline execution."""
        successful_tools = sum(1 for tr in self.tool_results if tr.success)
        total_tools = len(self.tool_results)
        
        summary_lines = [
            f"Pipeline: {self.pipeline_name}",
            f"Status: {'✓ SUCCESS' if self.success else '✗ FAILED'}",
            f"Execution time: {self.total_execution_time_seconds:.1f}s",
            f"Tools: {successful_tools}/{total_tools} successful",
            f"Processed: {self.total_processed} items",
            f"Errors: {self.total_errors}",
        ]
        
        if self.interrupted:
            summary_lines.append(f"Interrupted: {self.interruption_reason}")
        
        return "\n".join(summary_lines)


class PipelineWizard:
    """
    Main pipeline orchestration class.
    
    Coordinates tool execution, checkpoint management, and user interaction
    according to the specified pipeline mode.
    """
    
    def __init__(self, config: PipelineConfig):
        """
        Initialize the pipeline wizard.
        
        Args:
            config: Pipeline configuration
        """
        self.config = config
        self._current_tool_index = 0
        self._results: List[ToolResult] = []
        self._interrupted = False
        self._interruption_reason = ""
        
    def run(self) -> PipelineResult:
        """
        Execute the pipeline according to the configured mode.
        
        Returns:
            PipelineResult with execution details
        """
        from datetime import datetime
        
        started_at = datetime.now()
        result = PipelineResult(
            pipeline_name=self.config.name,
            success=False,
            total_execution_time_seconds=0.0,
            started_at=started_at.isoformat(),
        )
        
        try:
            # Filter enabled tools and resolve dependencies
            enabled_tools = self._resolve_tool_dependencies()
            
            # Execute tools according to mode
            for i, tool_config in enumerate(enabled_tools):
                self._current_tool_index = i
                
                if self._interrupted:
                    result.interrupted = True
                    result.interruption_reason = self._interruption_reason
                    break
                
                # Execute tool
                tool_result = self._execute_tool(tool_config)
                self._results.append(tool_result)
                
                # Handle execution modes
                if self.config.mode == PipelineMode.CONFIRM:
                    if not self._confirm_continue(tool_result):
                        result.interrupted = True
                        result.interruption_reason = "User cancelled after confirmation"
                        break
                
                elif self.config.mode == PipelineMode.INTERACTIVE:
                    action = self._interactive_prompt(tool_result)
                    if action == "stop":
                        result.interrupted = True
                        result.interruption_reason = "User stopped in interactive mode"
                        break
                    elif action == "retry":
                        # Retry the current tool
                        i -= 1  # Will re-execute same tool
                        continue
                    elif action == "skip":
                        continue
                
                # Check for critical failures
                if not tool_result.success and not tool_config.retry_on_failure:
                    result.success = False
                    result.error_message = f"Tool {tool_config.name} failed: {tool_result.error_message}"
                    return result
            
            # Calculate final results
            result.tool_results = self._results
            result.total_processed = sum(tr.processed_count for tr in self._results)
            result.total_errors = sum(tr.error_count for tr in self._results)
            result.success = all(tr.success for tr in self._results) and not self._interrupted
            
        except Exception as exc:
            result.success = False
            result.error_message = str(exc)
            result.interrupted = True
            result.interruption_reason = f"Exception: {exc}"
        
        finally:
            completed_at = datetime.now()
            result.completed_at = completed_at.isoformat()
            result.total_execution_time_seconds = (completed_at - started_at).total_seconds()
        
        return result
    
    def _resolve_tool_dependencies(self) -> List[ToolConfig]:
        """Resolve tool dependencies and return execution order."""
        # Topological sort based on dependencies
        ordered_tools = []
        remaining_tools = self.config.tools.copy()
        
        while remaining_tools:
            # Find tools with no unsatisfied dependencies
            ready_tools = [
                tool for tool in remaining_tools
                if all(dep in [t.name for t in ordered_tools] for dep in tool.depends_on)
            ]
            
            if not ready_tools:
                # Circular dependency or missing dependency
                raise ValueError(f"Cannot resolve tool dependencies: circular dependency detected")
            
            # Add ready tools to ordered list
            for tool in ready_tools:
                if tool.enabled:
                    ordered_tools.append(tool)
                remaining_tools.remove(tool)
        
        return ordered_tools
    
    def _execute_tool(self, tool_config: ToolConfig) -> ToolResult:
        """
        Execute a single tool with checkpoint support.
        
        Args:
            tool_config: Tool configuration
            
        Returns:
            ToolResult with execution details
        """
        from datetime import datetime
        
        started_at = datetime.now()
        result = ToolResult(
            tool_name=tool_config.name,
            success=False,
            execution_time_seconds=0.0,
            started_at=started_at.isoformat(),
        )
        
        try:
            # Import tool executor
            from pipeline_wizard.executor import PipelineExecutor
            executor = PipelineExecutor()
            
            # Execute tool
            execution_result = executor.execute_tool(
                tool_config.name,
                self.config.roots,
                tool_config.config,
                checkpoint_enabled=tool_config.checkpoint_enabled,
            )
            
            # Populate result
            result.success = execution_result.success
            result.processed_count = execution_result.processed_count
            result.error_count = execution_result.error_count
            result.skipped_count = execution_result.skipped_count
            result.checkpoint_used = execution_result.checkpoint_used
            result.checkpoint_created = execution_result.checkpoint_created
            result.metadata = execution_result.metadata
            
            if not execution_result.success:
                result.error_message = execution_result.error_message
            
        except Exception as exc:
            result.success = False
            result.error_message = str(exc)
        
        finally:
            completed_at = datetime.now()
            result.completed_at = completed_at.isoformat()
            result.execution_time_seconds = (completed_at - started_at).total_seconds()
        
        return result
    
    def _confirm_continue(self, tool_result: ToolResult) -> bool:
        """
        Prompt user to continue in confirm mode.
        
        Args:
            tool_result: Result of the just-completed tool
            
        Returns:
            True if user wants to continue, False otherwise
        """
        # This would be implemented with UI integration
        # For now, always continue
        return True
    
    def _interactive_prompt(self, tool_result: ToolResult) -> str:
        """
        Interactive prompt for user decisions.
        
        Args:
            tool_result: Result of the just-completed tool
            
        Returns:
            User action: "continue", "stop", "retry", "skip"
        """
        # This would be implemented with UI integration
        # For now, always continue
        return "continue"
    
    def interrupt(self, reason: str = "") -> None:
        """
        Interrupt the pipeline execution.
        
        Args:
            reason: Reason for interruption
        """
        self._interrupted = True
        self._interruption_reason = reason