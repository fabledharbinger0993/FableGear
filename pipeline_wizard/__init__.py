"""
pipeline_wizard — Universal Pipeline Wizard for FableGear tools.

Provides a comprehensive pipeline orchestration system that can run any
FableGear tool in sequence with checkpoint/resume support and multiple
execution modes.

Key Features:
- Universal tool coverage (Record Room + Chop Shop)
- Three execution modes: Auto, Confirm, Interactive
- Checkpoint integration for safe interrupt/resume
- Pipeline configuration save/load
- Progress tracking and reporting
- Error handling and recovery

Example Usage:
    from pipeline_wizard import PipelineWizard, PipelineConfig
    
    # Create pipeline configuration
    config = PipelineConfig(
        tools=[
            {"name": "audit", "config": {"scan_type": "full"}},
            {"name": "tag_tracks", "config": {"analyze_bpm": True}},
            {"name": "duplicates", "config": {"match_mode": "fuzzy"}},
        ],
        mode="auto",  # or "confirm" or "interactive"
    )
    
    # Run pipeline
    wizard = PipelineWizard(config)
    results = wizard.run()
"""

from .core import PipelineWizard, PipelineConfig, PipelineMode
from .tool_registry import ToolRegistry, ToolDefinition
from .executor import PipelineExecutor, ExecutionResult
from .checkpoint_integration import PipelineCheckpointManager

__all__ = [
    "PipelineWizard",
    "PipelineConfig", 
    "PipelineMode",
    "ToolRegistry",
    "ToolDefinition",
    "PipelineExecutor",
    "ExecutionResult",
    "PipelineCheckpointManager",
]
