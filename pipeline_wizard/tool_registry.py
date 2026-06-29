"""
pipeline_wizard.tool_registry — Registry of all FableGear tools.

Maintains a comprehensive registry of all available FableGear tools
with their metadata, configuration schemas, and execution requirements.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolDefinition:
    """Definition of a FableGear tool for pipeline execution."""
    name: str                          # Unique tool identifier
    display_name: str                  # Human-readable name
    category: str                      # "record_room" or "chop_shop"
    description: str                   # Tool description
    version: str                       # Tool version
    cli_command: str                   # CLI command to execute
    config_schema: Dict[str, Any]      # Configuration schema/validation
    required_roots: bool = True       # Whether tool requires root paths
    supports_multi_source: bool = False  # Whether tool supports multiple sources
    supports_checkpoint: bool = True   # Whether tool supports checkpointing
    estimated_time_per_item: float = 0.1  # Estimated time per item (seconds)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert tool definition to dictionary."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category,
            "description": self.description,
            "version": self.version,
            "cli_command": self.cli_command,
            "config_schema": self.config_schema,
            "required_roots": self.required_roots,
            "supports_multi_source": self.supports_multi_source,
            "supports_checkpoint": self.supports_checkpoint,
            "estimated_time_per_item": self.estimated_time_per_item,
        }


class ToolRegistry:
    """
    Registry of all available FableGear tools.
    
    Provides tool discovery, validation, and metadata management
    for the Pipeline Wizard system.
    """
    
    def __init__(self):
        """Initialize the tool registry."""
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register all default FableGear tools."""
        
        # Record Room Tools
        self.register_tool(ToolDefinition(
            name="audit",
            display_name="Library Audit",
            category="record_room",
            description="Scan Rekordbox database against actual drives to find issues",
            version="1.0",
            cli_command="python3 cli.py audit",
            config_schema={
                "scan_type": {"type": "string", "enum": ["full", "quick"], "default": "full"},
                "check_playlists": {"type": "boolean", "default": True},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.05,
        ))
        
        self.register_tool(ToolDefinition(
            name="import",
            display_name="Import Tracks",
            category="record_room",
            description="Add new audio files to Rekordbox database",
            version="1.0",
            cli_command="python3 cli.py import",
            config_schema={
                "create_playlists": {"type": "boolean", "default": True},
                "analyze_on_import": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": True},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.2,
        ))
        
        self.register_tool(ToolDefinition(
            name="fix_paths",
            display_name="Fix Broken Paths",
            category="record_room",
            description="Update file paths in database when drives are remounted",
            version="1.0",
            cli_command="python3 cli.py relocate",
            config_schema={
                "old_path": {"type": "string", "required": True},
                "new_path": {"type": "string", "required": True},
                "dry_run": {"type": "boolean", "default": True},
            },
            required_roots=False,
            supports_multi_source=False,
            supports_checkpoint=True,
            estimated_time_per_item=0.01,
        ))
        
        self.register_tool(ToolDefinition(
            name="link_playlists",
            display_name="Link Playlists",
            category="record_room",
            description="Map folder structure to Rekordbox playlist names",
            version="1.0",
            cli_command="python3 cli.py link",
            config_schema={
                "root_path": {"type": "string", "required": True},
                "playlist_naming": {"type": "string", "enum": ["folder", "artist", "album"], "default": "folder"},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.1,
        ))
        
        # Chop Shop Tools
        self.register_tool(ToolDefinition(
            name="tag_tracks",
            display_name="Tag Tracks",
            category="chop_shop",
            description="Analyze audio and write BPM/key metadata to files",
            version="1.0",
            cli_command="python3 cli.py process",
            config_schema={
                "analyze_bpm": {"type": "boolean", "default": True},
                "analyze_key": {"type": "boolean", "default": True},
                "overwrite_existing": {"type": "boolean", "default": False},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=2.0,
        ))
        
        self.register_tool(ToolDefinition(
            name="duplicates",
            display_name="Find Duplicates",
            category="chop_shop",
            description="Detect duplicate tracks using acoustic fingerprinting",
            version="1.0",
            cli_command="python3 cli.py duplicates",
            config_schema={
                "match_mode": {"type": "string", "enum": ["exact", "fuzzy", "tags", "all"], "default": "exact"},
                "fuzzy_threshold": {"type": "number", "min": 0.0, "max": 1.0, "default": 0.85},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.5,
        ))
        
        self.register_tool(ToolDefinition(
            name="rename_files",
            display_name="Rename Files",
            category="chop_shop",
            description="Batch rename files using patterns or learned rules",
            version="1.0",
            cli_command="python3 cli.py rename",
            config_schema={
                "pattern": {"type": "string", "required": True},
                "dry_run": {"type": "boolean", "default": True},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.05,
        ))
        
        self.register_tool(ToolDefinition(
            name="organize_library",
            display_name="Organize Library",
            category="chop_shop",
            description="Rebuild folder structure based on metadata",
            version="1.0",
            cli_command="python3 cli.py organize",
            config_schema={
                "structure": {"type": "string", "enum": ["artist_album", "artist", "album"], "default": "artist_album"},
                "dry_run": {"type": "boolean", "default": True},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.3,
        ))
        
        self.register_tool(ToolDefinition(
            name="normalize",
            display_name="Normalize Loudness",
            category="chop_shop",
            description="Normalize audio to target LUFS level",
            version="1.0",
            cli_command="python3 cli.py normalize",
            config_schema={
                "target_lufs": {"type": "number", "default": -8.0},
                "lufs_tolerance": {"type": "number", "default": 1.0},
                "dry_run": {"type": "boolean", "default": True},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=5.0,
        ))
        
        self.register_tool(ToolDefinition(
            name="convert",
            display_name="Convert Format",
            category="chop_shop",
            description="Re-encode audio files to target format",
            version="1.0",
            cli_command="python3 cli.py convert",
            config_schema={
                "target_format": {"type": "string", "required": True},
                "quality": {"type": "string", "default": "high"},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=3.0,
        ))
        
        self.register_tool(ToolDefinition(
            name="novelty_scanner",
            display_name="Novelty Scanner",
            category="chop_shop",
            description="Find tracks not in main library across drives",
            version="1.0",
            cli_command="python3 cli.py novelty",
            config_schema={
                "reference_library": {"type": "string", "required": True},
            },
            required_roots=True,
            supports_multi_source=True,
            supports_checkpoint=True,
            estimated_time_per_item=0.5,
        ))
    
    def register_tool(self, tool: ToolDefinition) -> None:
        """
        Register a tool in the registry.
        
        Args:
            tool: Tool definition to register
        """
        self._tools[tool.name] = tool
    
    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """
        Get a tool by name.
        
        Args:
            name: Tool identifier
            
        Returns:
            ToolDefinition or None if not found
        """
        return self._tools.get(name)
    
    def get_all_tools(self) -> List[ToolDefinition]:
        """Get all registered tools."""
        return list(self._tools.values())
    
    def get_tools_by_category(self, category: str) -> List[ToolDefinition]:
        """
        Get tools by category.
        
        Args:
            category: Tool category ("record_room" or "chop_shop")
            
        Returns:
            List of tools in the category
        """
        return [tool for tool in self._tools.values() if tool.category == category]
    
    def validate_tool_config(self, tool_name: str, config: Dict[str, Any]) -> List[str]:
        """
        Validate tool configuration against schema.
        
        Args:
            tool_name: Name of the tool
            config: Configuration to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return [f"Unknown tool: {tool_name}"]
        
        errors = []
        schema = tool.config_schema
        
        for field_name, field_schema in schema.items():
            if field_schema.get("required", False) and field_name not in config:
                errors.append(f"Required field missing: {field_name}")
            
            if field_name in config:
                value = config[field_name]
                field_type = field_schema.get("type")
                
                # Type validation
                if field_type == "string" and not isinstance(value, str):
                    errors.append(f"Field {field_name} must be a string")
                elif field_type == "number" and not isinstance(value, (int, float)):
                    errors.append(f"Field {field_name} must be a number")
                elif field_type == "boolean" and not isinstance(value, bool):
                    errors.append(f"Field {field_name} must be a boolean")
                
                # Enum validation
                if "enum" in field_schema and value not in field_schema["enum"]:
                    errors.append(f"Field {field_name} must be one of {field_schema['enum']}")
                
                # Range validation
                if "min" in field_schema and isinstance(value, (int, float)) and value < field_schema["min"]:
                    errors.append(f"Field {field_name} must be >= {field_schema['min']}")
                if "max" in field_schema and isinstance(value, (int, float)) and value > field_schema["max"]:
                    errors.append(f"Field {field_name} must be <= {field_schema['max']}")
        
        return errors
    
    def estimate_execution_time(self, tool_name: str, item_count: int) -> float:
        """
        Estimate execution time for a tool.
        
        Args:
            tool_name: Name of the tool
            item_count: Number of items to process
            
        Returns:
            Estimated time in seconds
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return 0.0
        
        return item_count * tool.estimated_time_per_item