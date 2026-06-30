"""
checkpoint_manager.schema — Standardized checkpoint schema definitions.

Defines the universal checkpoint data structure that all tools should follow
for consistency and reliability across the FableGear ecosystem.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional


class CheckpointSchema:
    """
    Standardized schema for FableGear checkpoint data.
    
    All checkpoints should follow this structure to ensure consistency
    across tools and enable reliable resume functionality.
    """
    
    # Required top-level fields
    REQUIRED_FIELDS = {
        "tool_name",
        "saved_at",
        "operation_count",
        "roots",
        "config",
    }
    
    # Optional but recommended fields
    RECOMMENDED_FIELDS = {
        "processed_count",
        "total_count",
        "last_processed_item",
        "tool_state",
        "metadata",
    }
    
    @staticmethod
    def create_base_schema(
        tool_name: str,
        roots: List[str],
        config: Dict[str, Any],
        operation_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Create a base checkpoint dict with required fields.
        
        Args:
            tool_name: Name of the tool creating the checkpoint
            roots: List of root paths being processed
            config: Tool configuration
            operation_count: Number of operations completed
            
        Returns:
            Dict with base checkpoint structure
        """
        return {
            "tool_name": tool_name,
            "saved_at": datetime.now().isoformat(),
            "operation_count": operation_count,
            "roots": roots,
            "config": config,
            "processed_count": 0,
            "total_count": None,
            "last_processed_item": None,
            "tool_state": {},
            "metadata": {
                "fablegear_version": "1.0",
                "schema_version": "1.0",
            }
        }
    
    @staticmethod
    def validate(data: Dict[str, Any]) -> List[str]:
        """
        Validate checkpoint data against the standard schema.
        
        Args:
            data: Checkpoint data to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Check required fields
        missing_required = CheckpointSchema.REQUIRED_FIELDS - set(data.keys())
        if missing_required:
            errors.append(f"Missing required fields: {missing_required}")
        
        # Validate field types
        if "tool_name" in data and not isinstance(data["tool_name"], str):
            errors.append("tool_name must be a string")
        
        if "saved_at" in data:
            try:
                datetime.fromisoformat(data["saved_at"])
            except (ValueError, TypeError):
                errors.append("saved_at must be a valid ISO format datetime")
        
        if "operation_count" in data and not isinstance(data["operation_count"], int):
            errors.append("operation_count must be an integer")
        
        if "roots" in data and not isinstance(data["roots"], list):
            errors.append("roots must be a list")
        
        if "config" in data and not isinstance(data["config"], dict):
            errors.append("config must be a dict")
        
        # Validate optional fields if present
        if "processed_count" in data and not isinstance(data["processed_count"], int):
            errors.append("processed_count must be an integer")
        
        if "total_count" in data and data["total_count"] is not None:
            if not isinstance(data["total_count"], int):
                errors.append("total_count must be an integer or null")
        
        return errors
    
    @staticmethod
    def sanitize(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize checkpoint data by removing sensitive or invalid fields.
        
        Args:
            data: Checkpoint data to sanitize
            
        Returns:
            Sanitized checkpoint data
        """
        sanitized = data.copy()
        
        # Remove any keys that might contain sensitive data
        sensitive_keys = ["password", "token", "api_key", "secret"]
        for key in list(sanitized.keys()):
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = "***REDACTED***"
        
        # Ensure metadata exists
        if "metadata" not in sanitized:
            sanitized["metadata"] = {}
        
        return sanitized


class ToolSpecificSchema:
    """
    Base class for tool-specific checkpoint schemas.
    
    Tools should inherit from this class to define their specific
    checkpoint requirements and validation logic.
    """
    
    # Tool-specific required fields
    TOOL_REQUIRED_FIELDS = set()
    
    # Tool-specific optional fields
    TOOL_OPTIONAL_FIELDS = set()
    
    @classmethod
    def validate_tool_data(cls, data: Dict[str, Any]) -> List[str]:
        """
        Validate tool-specific checkpoint data.
        
        Args:
            data: Checkpoint data to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Check tool-specific required fields
        missing_tool_fields = cls.TOOL_REQUIRED_FIELDS - set(data.get("tool_state", {}).keys())
        if missing_tool_fields:
            errors.append(f"Missing tool-specific fields: {missing_tool_fields}")
        
        return errors
    
    @classmethod
    def get_example_state(cls) -> Dict[str, Any]:
        """
        Get an example tool state for documentation/testing.
        
        Returns:
            Dict with example tool state data
        """
        return {}


class CheckpointValidationError(Exception):
    """Raised when checkpoint data fails schema validation."""
    
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Checkpoint validation failed: {', '.join(errors)}")
    
    def get_errors(self) -> List[str]:
        """Get the list of validation errors."""
        return self.errors