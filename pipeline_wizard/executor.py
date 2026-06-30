"""
pipeline_wizard.executor — Tool execution engine for pipelines.

Handles the actual execution of FableGear tools within the pipeline
context, including subprocess management, progress tracking, and
error handling.
"""

import logging
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing a single tool."""
    success: bool
    processed_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    error_message: str = ""
    checkpoint_used: bool = False
    checkpoint_created: bool = False
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class PipelineExecutor:
    """
    Executes FableGear tools within the pipeline context.
    
    Manages subprocess execution, progress tracking, and integration
    with the checkpoint system.
    """
    
    def __init__(self):
        """Initialize the pipeline executor."""
        self._current_process: Optional[subprocess.Popen] = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
    
    def execute_tool(
        self,
        tool_name: str,
        roots: List[Path],
        config: Dict[str, Any],
        checkpoint_enabled: bool = True,
    ) -> ExecutionResult:
        """
        Execute a single tool with the given configuration.
        
        Args:
            tool_name: Name of the tool to execute
            roots: Root paths to process
            config: Tool-specific configuration
            checkpoint_enabled: Whether to enable checkpointing
            
        Returns:
            ExecutionResult with execution details
        """
        from pipeline_wizard.tool_registry import ToolRegistry
        
        registry = ToolRegistry()
        tool_def = registry.get_tool(tool_name)
        
        if not tool_def:
            return ExecutionResult(
                success=False,
                error_message=f"Unknown tool: {tool_name}"
            )
        
        # Validate configuration
        validation_errors = registry.validate_tool_config(tool_name, config)
        if validation_errors:
            return ExecutionResult(
                success=False,
                error_message=f"Configuration validation failed: {validation_errors}"
            )
        
        # Build command
        command = self._build_command(tool_def, roots, config)
        
        # Check for existing checkpoint
        checkpoint_used = False
        if checkpoint_enabled and tool_def.supports_checkpoint:
            checkpoint_used = self._check_for_checkpoint(tool_name, roots, config)
        
        # Execute command
        try:
            result = self._execute_command(command, tool_name)
            result.checkpoint_used = checkpoint_used
            
            # Check if checkpoint was created
            if checkpoint_enabled and tool_def.supports_checkpoint:
                result.checkpoint_created = self._check_checkpoint_created(tool_name, roots, config)
            
            return result
            
        except Exception as exc:
            return ExecutionResult(
                success=False,
                error_message=f"Execution failed: {exc}"
            )
    
    def _build_command(
        self,
        tool_def: "ToolDefinition",
        roots: List[Path],
        config: Dict[str, Any],
    ) -> List[str]:
        """
        Build the command line for tool execution.
        
        Args:
            tool_def: Tool definition
            roots: Root paths
            config: Tool configuration
            
        Returns:
            List of command components
        """
        command = tool_def.cli_command.split()
        
        # Add root paths
        for root in roots:
            command.extend(["--path", str(root)])
        
        # Add configuration parameters
        for key, value in config.items():
            if isinstance(value, bool):
                if value:
                    command.append(f"--{key}")
            else:
                command.extend([f"--{key}", str(value)])
        
        return command
    
    def _execute_command(self, command: List[str], tool_name: str) -> ExecutionResult:
        """
        Execute the command and monitor progress.
        
        Args:
            command: Command to execute
            tool_name: Name of the tool being executed
            
        Returns:
            ExecutionResult with execution details
        """
        started_at = datetime.now()
        processed_count = 0
        error_count = 0
        
        try:
            with self._lock:
                self._current_process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            
            # Monitor process
            while True:
                if self._cancel_event.is_set():
                    self._current_process.terminate()
                    return ExecutionResult(
                        success=False,
                        error_message="Execution cancelled by user"
                    )
                
                return_code = self._current_process.poll()
                if return_code is not None:
                    break
                
                # Could parse stdout for progress information here
                # For now, just wait
                
                import time
                time.sleep(0.1)
            
            # Get output
            stdout, stderr = self._current_process.communicate()
            
            # Parse output for counts
            processed_count = self._parse_processed_count(stdout)
            error_count = self._parse_error_count(stderr)
            
            success = return_code == 0
            error_message = stderr if not success else ""
            
            return ExecutionResult(
                success=success,
                processed_count=processed_count,
                error_count=error_count,
                error_message=error_message,
                metadata={
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "execution_time_seconds": (datetime.now() - started_at).total_seconds(),
                }
            )
            
        except Exception as exc:
            return ExecutionResult(
                success=False,
                error_message=f"Command execution failed: {exc}"
            )
        
        finally:
            with self._lock:
                self._current_process = None
    
    def _parse_processed_count(self, output: str) -> int:
        """
        Parse processed count from tool output.
        
        Args:
            output: Tool stdout output
            
        Returns:
            Number of processed items
        """
        # This would be customized based on each tool's output format
        # For now, return 0
        return 0
    
    def _parse_error_count(self, output: str) -> int:
        """
        Parse error count from tool output.
        
        Args:
            output: Tool stderr output
            
        Returns:
            Number of errors encountered
        """
        # This would be customized based on each tool's output format
        # For now, return 0
        return 0
    
    def _check_for_checkpoint(
        self,
        tool_name: str,
        roots: List[Path],
        config: Dict[str, Any],
    ) -> bool:
        """
        Check if a checkpoint exists for the tool.
        
        Args:
            tool_name: Name of the tool
            roots: Root paths
            config: Tool configuration
            
        Returns:
            True if checkpoint exists
        """
        try:
            from checkpoint import check_checkpoint
            return check_checkpoint(tool_name, roots, config).get("exists", False)
        except Exception:
            return False
    
    def _check_checkpoint_created(
        self,
        tool_name: str,
        roots: List[Path],
        config: Dict[str, Any],
    ) -> bool:
        """
        Check if a checkpoint was created during execution.
        
        Args:
            tool_name: Name of the tool
            roots: Root paths
            config: Tool configuration
            
        Returns:
            True if checkpoint was created
        """
        # This would check if a new checkpoint exists after execution
        # For now, return False
        return False
    
    def cancel(self) -> None:
        """Cancel the currently running tool execution."""
        with self._lock:
            self._cancel_event.set()
            if self._current_process:
                self._current_process.terminate()
    
    def reset(self) -> None:
        """Reset the executor state."""
        with self._lock:
            self._cancel_event.clear()
            self._current_process = None