"""
pipeline_wizard.examples — Example pipeline configurations and usage.

Demonstrates how to create and execute pipelines using the
Pipeline Wizard system.
"""

from pathlib import Path
from pipeline_wizard import (
    PipelineWizard,
    PipelineConfig,
    PipelineMode,
    ToolConfig,
)


def example_basic_pipeline():
    """Create and run a basic pipeline."""
    
    # Create pipeline configuration
    config = PipelineConfig(
        name="Basic Library Cleanup",
        description="Audit library, tag tracks, and find duplicates",
        mode=PipelineMode.AUTO,
        roots=[Path("/Music/Library")],
        tools=[
            ToolConfig(
                name="audit",
                config={"scan_type": "full"},
            ),
            ToolConfig(
                name="tag_tracks",
                config={"analyze_bpm": True, "analyze_key": True},
            ),
            ToolConfig(
                name="duplicates",
                config={"match_mode": "fuzzy", "fuzzy_threshold": 0.85},
            ),
        ],
    )
    
    # Execute pipeline
    wizard = PipelineWizard(config)
    result = wizard.run()
    
    print(result.get_summary())
    return result


def example_multi_drive_pipeline():
    """Create a pipeline that processes multiple drives."""
    
    config = PipelineConfig(
        name="Multi-Drive Library Organization",
        description="Organize and normalize across multiple drives",
        mode=PipelineMode.CONFIRM,
        roots=[
            Path("/Music/Drive1"),
            Path("/Music/Drive2"),
            Path("/Music/Drive3"),
        ],
        tools=[
            ToolConfig(
                name="organize_library",
                config={"structure": "artist_album", "dry_run": True},
            ),
            ToolConfig(
                name="normalize",
                config={"target_lufs": -8.0, "dry_run": True},
            ),
        ],
    )
    
    wizard = PipelineWizard(config)
    result = wizard.run()
    
    print(result.get_summary())
    return result


def example_pipeline_with_dependencies():
    """Create a pipeline with tool dependencies."""
    
    config = PipelineConfig(
        name="Safe Library Update",
        description="Audit before making changes, with dependencies",
        mode=PipelineMode.INTERACTIVE,
        roots=[Path("/Music/Library")],
        tools=[
            ToolConfig(
                name="audit",
                config={"scan_type": "full"},
            ),
            ToolConfig(
                name="tag_tracks",
                config={"analyze_bpm": True, "analyze_key": True},
                depends_on=["audit"],  # Only run after audit succeeds
                retry_on_failure=True,
                max_retries=2,
            ),
            ToolConfig(
                name="duplicates",
                config={"match_mode": "exact"},
                depends_on=["tag_tracks"],  # Only run after tagging
            ),
        ],
    )
    
    wizard = PipelineWizard(config)
    result = wizard.run()
    
    print(result.get_summary())
    return result


def example_checkpoint_resume():
    """Demonstrate checkpoint resume functionality."""
    
    config = PipelineConfig(
        name="Large Library Processing",
        description="Process large library with checkpoint support",
        mode=PipelineMode.AUTO,
        roots=[Path("/Music/LargeLibrary")],
        tools=[
            ToolConfig(
                name="tag_tracks",
                config={"analyze_bpm": True, "analyze_key": True},
                checkpoint_enabled=True,
            ),
            ToolConfig(
                name="duplicates",
                config={"match_mode": "fuzzy"},
                checkpoint_enabled=True,
            ),
        ],
    )
    
    wizard = PipelineWizard(config)
    
    # Check for existing pipeline checkpoint
    from pipeline_wizard.checkpoint_integration import PipelineCheckpointManager
    
    ckpt_manager = PipelineCheckpointManager(config.name, config.to_dict())
    if ckpt_manager.has_checkpoint():
        print("Found existing pipeline checkpoint")
        info = ckpt_manager.get_checkpoint_info()
        print(f"Resume from tool: {ckpt_manager.get_resume_point()}")
        print(f"Estimated remaining time: {ckpt_manager.estimate_remaining_time():.1f}s")
    
    result = wizard.run()
    
    print(result.get_summary())
    return result


def example_save_load_pipeline():
    """Demonstrate saving and loading pipeline configurations."""
    
    # Create a complex pipeline
    config = PipelineConfig(
        name="Weekly Maintenance",
        description="Comprehensive weekly library maintenance",
        mode=PipelineMode.AUTO,
        roots=[Path("/Music/Library")],
        tools=[
            ToolConfig(name="audit", config={"scan_type": "full"}),
            ToolConfig(name="tag_tracks", config={"analyze_bpm": True, "analyze_key": True}),
            ToolConfig(name="duplicates", config={"match_mode": "fuzzy"}),
            ToolConfig(name="normalize", config={"target_lufs": -8.0}),
        ],
    )
    
    # Save to file
    import json
    config_path = Path.home() / ".fablegear" / "pipelines" / "weekly_maintenance.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w") as f:
        json.dump(config.to_dict(), f, indent=2)
    
    print(f"Pipeline saved to: {config_path}")
    
    # Load from file
    with open(config_path) as f:
        loaded_data = json.load(f)
    
    loaded_config = PipelineConfig.from_dict(loaded_data)
    print(f"Loaded pipeline: {loaded_config.name}")
    
    return loaded_config


def example_tool_registry():
    """Demonstrate tool registry usage."""
    from pipeline_wizard.tool_registry import ToolRegistry
    
    registry = ToolRegistry()
    
    # Get all tools
    all_tools = registry.get_all_tools()
    print(f"Total tools registered: {len(all_tools)}")
    
    # Get tools by category
    record_room_tools = registry.get_tools_by_category("record_room")
    chop_shop_tools = registry.get_tools_by_category("chop_shop")
    
    print(f"Record Room tools: {len(record_room_tools)}")
    print(f"Chop Shop tools: {len(chop_shop_tools)}")
    
    # Get specific tool
    tag_tool = registry.get_tool("tag_tracks")
    if tag_tool:
        print(f"Tag Tracks tool: {tag_tool.display_name}")
        print(f"  Description: {tag_tool.description}")
        print(f"  Supports multi-source: {tag_tool.supports_multi_source}")
        print(f"  Supports checkpoint: {tag_tool.supports_checkpoint}")
    
    # Validate tool configuration
    config = {"analyze_bpm": True, "analyze_key": True, "invalid_option": "value"}
    errors = registry.validate_tool_config("tag_tracks", config)
    if errors:
        print(f"Configuration errors: {errors}")
    
    # Estimate execution time
    estimated_time = registry.estimate_execution_time("tag_tracks", 1000)
    print(f"Estimated time for 1000 tracks: {estimated_time:.1f}s")


if __name__ == "__main__":
    print("=== Basic Pipeline Example ===")
    example_basic_pipeline()
    
    print("\n=== Multi-Drive Pipeline Example ===")
    example_multi_drive_pipeline()
    
    print("\n=== Pipeline with Dependencies Example ===")
    example_pipeline_with_dependencies()
    
    print("\n=== Checkpoint Resume Example ===")
    example_checkpoint_resume()
    
    print("\n=== Save/Load Pipeline Example ===")
    example_save_load_pipeline()
    
    print("\n=== Tool Registry Example ===")
    example_tool_registry()