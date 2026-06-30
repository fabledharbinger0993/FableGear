"""
update_system.rollback — Rollback management for failed updates.

Handles creation and restoration of backups when updates fail,
ensuring the application can be safely reverted to a working state.
"""

import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class RollbackManager:
    """
    Manages backup creation and rollback operations.
    
    Ensures that failed updates can be safely rolled back to a
    known working state.
    """
    
    def __init__(self, repo_root: Optional[Path] = None):
        """
        Initialize the rollback manager.
        
        Args:
            repo_root: Repository root directory
        """
        self.repo_root = repo_root
        self._backup_dir = repo_root / ".fablegear_backups" if repo_root else None
    
    def create_backup(self) -> Optional[Path]:
        """
        Create a timestamped backup of the current installation.
        
        Returns:
            Path to the backup directory or None if backup failed
        """
        if not self.repo_root or not self._backup_dir:
            log.error("Cannot create backup: repo root not set")
            return None
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"backup_{timestamp}"
            backup_path = self._backup_dir / backup_name
            
            # Create backup directory
            backup_path.mkdir(parents=True, exist_ok=True)
            
            # Copy repository files
            for item in self.repo_root.iterdir():
                if item.name == ".fablegear_backups":
                    continue  # Skip backup directory itself
                
                dest = backup_path / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                elif item.is_file():
                    shutil.copy2(item, dest)
            
            # Save current git HEAD for reference
            self._save_git_state(backup_path)
            
            log.info("Backup created successfully: %s", backup_path)
            return backup_path
            
        except Exception as exc:
            log.error("Failed to create backup: %s", exc)
            return None
    
    def rollback(self, backup_path: Optional[Path] = None) -> bool:
        """
        Rollback to a previous backup.
        
        Args:
            backup_path: Path to backup directory (None = use latest)
            
        Returns:
            True if rollback succeeded
        """
        if not self.repo_root:
            log.error("Cannot rollback: repo root not set")
            return False
        
        # Use latest backup if none specified
        if backup_path is None:
            backup_path = self._get_latest_backup()
        
        if not backup_path or not backup_path.exists():
            log.error("Cannot rollback: backup not found")
            return False
        
        try:
            # Stop any running processes
            self._stop_application()
            
            # Restore files from backup
            for item in backup_path.iterdir():
                dest = self.repo_root / item.name
                
                # Remove existing item
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                
                # Restore from backup
                if item.is_dir():
                    shutil.copytree(item, dest)
                elif item.is_file():
                    shutil.copy2(item, dest)
            
            # Restore git state if available
            self._restore_git_state(backup_path)
            
            log.info("Rollback completed successfully from: %s", backup_path)
            return True
            
        except Exception as exc:
            log.error("Failed to rollback: %s", exc)
            return False
    
    def cleanup_old_backups(self, max_age_days: int = 30) -> int:
        """
        Remove backups older than specified age.
        
        Args:
            max_age_days: Maximum age of backups to keep
            
        Returns:
            Number of backups removed
        """
        if not self._backup_dir or not self._backup_dir.exists():
            return 0
        
        removed_count = 0
        cutoff_time = datetime.now().timestamp() - (max_age_days * 24 * 60 * 60)
        
        try:
            for backup in self._backup_dir.iterdir():
                if backup.is_dir():
                    # Check modification time
                    mtime = backup.stat().st_mtime
                    if mtime < cutoff_time:
                        shutil.rmtree(backup)
                        removed_count += 1
                        log.info("Removed old backup: %s", backup.name)
            
            return removed_count
            
        except Exception as exc:
            log.error("Failed to cleanup old backups: %s", exc)
            return 0
    
    def _save_git_state(self, backup_path: Path) -> None:
        """Save current git state to backup."""
        try:
            git_state_file = backup_path / "git_state.txt"
            
            # Get current HEAD
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            
            with open(git_state_file, "w") as f:
                f.write(f"HEAD: {result.stdout.strip()}\n")
                
                # Get current branch
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                f.write(f"Branch: {result.stdout.strip()}\n")
                
        except Exception as exc:
            log.warning("Failed to save git state: %s", exc)
    
    def _restore_git_state(self, backup_path: Path) -> None:
        """Restore git state from backup."""
        try:
            git_state_file = backup_path / "git_state.txt"
            if not git_state_file.exists():
                return
            
            with open(git_state_file, "r") as f:
                lines = f.readlines()
            
            for line in lines:
                if line.startswith("HEAD:"):
                    head_ref = line.split(":")[1].strip()
                    subprocess.run(
                        ["git", "reset", "--hard", head_ref],
                        cwd=self.repo_root,
                        capture_output=True,
                        check=False,
                    )
                elif line.startswith("Branch:"):
                    branch_name = line.split(":")[1].strip()
                    subprocess.run(
                        ["git", "checkout", branch_name],
                        cwd=self.repo_root,
                        capture_output=True,
                        check=False,
                    )
                    
        except Exception as exc:
            log.warning("Failed to restore git state: %s", exc)
    
    def _get_latest_backup(self) -> Optional[Path]:
        """Get the most recent backup directory."""
        if not self._backup_dir or not self._backup_dir.exists():
            return None
        
        try:
            backups = [d for d in self._backup_dir.iterdir() if d.is_dir()]
            if not backups:
                return None
            
            # Sort by modification time
            backups.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            return backups[0]
            
        except Exception:
            return None
    
    def _stop_application(self) -> None:
        """Stop any running FableGear processes."""
        # This would involve finding and stopping FableGear processes
        # For now, this is a placeholder
        pass