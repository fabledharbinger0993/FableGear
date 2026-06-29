"""
update_system.core — Core update management system.

Handles the update workflow from checking to execution with proper
safety mechanisms and user interaction.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class UpdateChoice(Enum):
    """User update choice options."""
    UPDATE_NOW = "update_now"
    SKIP_VERSION = "skip_version"
    REMIND_LATER = "remind_later"


@dataclass
class UpdateInfo:
    """Information about an available update."""
    has_update: bool
    current_version: str
    latest_version: str
    release_notes: str
    release_url: str
    published_at: str
    is_forward_update: bool
    requires_backup: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "has_update": self.has_update,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "release_notes": self.release_notes,
            "release_url": self.release_url,
            "published_at": self.published_at,
            "is_forward_update": self.is_forward_update,
            "requires_backup": self.requires_backup,
        }


@dataclass
class UpdateConfig:
    """Configuration for the update system."""
    auto_check_enabled: bool = True
    check_interval_hours: int = 24
    quiet_failure_mode: bool = True
    backup_before_update: bool = True
    rollback_on_failure: bool = True
    skip_versions: List[str] = None
    dev_mode: bool = False
    
    def __post_init__(self):
        if self.skip_versions is None:
            self.skip_versions = []


class UpdateManager:
    """
    Main update management system.
    
    Coordinates update checking, user interaction, and safe
    update execution with rollback support.
    """
    
    def __init__(self, config: Optional[UpdateConfig] = None):
        """
        Initialize the update manager.
        
        Args:
            config: Update configuration (default: auto-detected)
        """
        self.config = config or self._detect_config()
        self._repo_root = self._find_repo_root()
        self._github_client = None
        self._rollback_manager = None
        
        # Initialize components
        self._initialize_components()
    
    def _detect_config(self) -> UpdateConfig:
        """Auto-detect update configuration from environment."""
        from pathlib import Path
        
        # Check for .dev sentinel
        repo_root = self._find_repo_root()
        dev_mode = repo_root and (repo_root / ".dev").exists()
        
        return UpdateConfig(
            dev_mode=dev_mode,
            quiet_failure_mode=True,
            backup_before_update=True,
            rollback_on_failure=True,
        )
    
    def _find_repo_root(self) -> Optional[Path]:
        """Find the FableGear repository root."""
        from pathlib import Path
        
        # Start from current directory and search upward
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            if (parent / ".git").exists():
                return parent
        return None
    
    def _initialize_components(self) -> None:
        """Initialize update system components."""
        from update_system.github_client import GitHubClient
        from update_system.rollback import RollbackManager
        
        self._github_client = GitHubClient()
        self._rollback_manager = RollbackManager(self._repo_root)
    
    def check_for_updates(self) -> UpdateInfo:
        """
        Check for available updates from GitHub.
        
        Returns:
            UpdateInfo with update details
        """
        if not self.config.auto_check_enabled or self.config.dev_mode:
            return UpdateInfo(
                has_update=False,
                current_version="unknown",
                latest_version="unknown",
                release_notes="",
                release_url="",
                published_at="",
                is_forward_update=False,
            )
        
        try:
            # Get current version
            current_version = self._get_current_version()
            
            # Get latest release from GitHub
            latest_release = self._github_client.get_latest_release()
            
            if not latest_release:
                return self._create_no_update_info(current_version)
            
            latest_version = latest_release.get("tag_name", "unknown")
            
            # Check if this is a forward update
            is_forward = self._is_forward_update(current_version, latest_version)
            
            # Check if version is in skip list
            if latest_version in self.config.skip_versions:
                return self._create_no_update_info(current_version)
            
            return UpdateInfo(
                has_update=is_forward,
                current_version=current_version,
                latest_version=latest_version,
                release_notes=latest_release.get("body", ""),
                release_url=latest_release.get("html_url", ""),
                published_at=latest_release.get("published_at", ""),
                is_forward_update=is_forward,
            )
            
        except Exception as exc:
            if self.config.quiet_failure_mode:
                log.warning("Update check failed silently: %s", exc)
                return self._create_no_update_info("unknown")
            else:
                raise
    
    def offer_update(self, update_info: UpdateInfo) -> UpdateChoice:
        """
        Offer update to user through UI.
        
        Args:
            update_info: Update information to present
            
        Returns:
            User's choice
        """
        from update_system.ui_integration import UpdateUI
        
        ui = UpdateUI()
        return ui.offer_update(update_info)
    
    def perform_update(self, update_info: UpdateInfo) -> bool:
        """
        Perform the update process.
        
        Args:
            update_info: Update information
            
        Returns:
            True if update succeeded
        """
        if not self._repo_root:
            log.error("Cannot perform update: repo root not found")
            return False
        
        try:
            # Create backup if configured
            if self.config.backup_before_update:
                backup_path = self._rollback_manager.create_backup()
                log.info("Created backup at: %s", backup_path)
            
            # Fetch and merge latest release
            success = self._fetch_and_merge(update_info.latest_version)
            
            if not success:
                # Rollback if configured
                if self.config.rollback_on_failure:
                    log.warning("Update failed, rolling back")
                    self._rollback_manager.rollback(backup_path)
                return False
            
            # Update dependencies
            success = self._update_dependencies()
            
            if not success:
                if self.config.rollback_on_failure:
                    log.warning("Dependency update failed, rolling back")
                    self._rollback_manager.rollback(backup_path)
                return False
            
            # Restart application
            self._restart_application()
            
            return True
            
        except Exception as exc:
            log.error("Update process failed: %s", exc)
            if self.config.rollback_on_failure:
                self._rollback_manager.rollback()
            return False
    
    def _get_current_version(self) -> str:
        """Get current version from git tags."""
        if not self._repo_root:
            return "unknown"
        
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        
        return "unknown"
    
    def _is_forward_update(self, current: str, latest: str) -> bool:
        """Check if latest version is forward-moving from current."""
        # Simple version comparison - could be enhanced
        try:
            # Remove 'v' prefix if present
            current_clean = current.lstrip('v')
            latest_clean = latest.lstrip('v')
            
            # For now, assume any different version is an update
            # A more sophisticated version comparison could be added
            return current_clean != latest_clean
            
        except Exception:
            return False
    
    def _fetch_and_merge(self, tag: str) -> bool:
        """Fetch and merge the specified tag."""
        try:
            # Fetch tags
            subprocess.run(
                ["git", "fetch", "origin", "--tags"],
                cwd=self._repo_root,
                capture_output=True,
                check=True,
            )
            
            # Check if tag is a forward update
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", "HEAD", tag],
                cwd=self._repo_root,
                capture_output=True,
                check=False,
            )
            
            if result.returncode != 0:
                log.warning("Tag %s is not a forward update, skipping", tag)
                return False
            
            # Merge the tag
            subprocess.run(
                ["git", "merge", "--ff-only", tag],
                cwd=self._repo_root,
                capture_output=True,
                check=True,
            )
            
            log.info("Successfully merged tag: %s", tag)
            return True
            
        except subprocess.CalledProcessError as exc:
            log.error("Failed to merge tag %s: %s", tag, exc)
            return False
    
    def _update_dependencies(self) -> bool:
        """Update Python dependencies."""
        try:
            # Update UI requirements
            subprocess.run(
                ["pip", "install", "--upgrade", "-r", "requirements_ui.txt"],
                cwd=self._repo_root,
                capture_output=True,
                check=True,
            )
            
            # Update main requirements
            subprocess.run(
                ["pip", "install", "--upgrade", "-r", "requirements.txt"],
                cwd=self._repo_root,
                capture_output=True,
                check=True,
            )
            
            log.info("Dependencies updated successfully")
            return True
            
        except subprocess.CalledProcessError as exc:
            log.error("Failed to update dependencies: %s", exc)
            return False
    
    def _restart_application(self) -> None:
        """Restart the application."""
        log.info("Restarting application...")
        # The actual restart would be handled by the main application
        # This is a placeholder for the restart logic
    
    def _create_no_update_info(self, current_version: str) -> UpdateInfo:
        """Create UpdateInfo for no-update scenario."""
        return UpdateInfo(
            has_update=False,
            current_version=current_version,
            latest_version=current_version,
            release_notes="",
            release_url="",
            published_at="",
            is_forward_update=False,
        )