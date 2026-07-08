"""
checkpoint_manager.cleanup — Checkpoint cleanup and maintenance utilities.

Provides automated cleanup of old checkpoints to prevent disk space
bloat and maintain system performance.
"""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


class CheckpointCleanup:
    """
    Manages checkpoint cleanup and maintenance operations.
    
    Features:
    - Automatic cleanup of checkpoints older than specified age
    - Per-tool cleanup policies
    - Disk space monitoring
    - Thread-safe cleanup operations
    """
    
    DEFAULT_MAX_AGE_DAYS = 30
    DEFAULT_MAX_SIZE_MB = 500
    
    def __init__(
        self,
        checkpoint_base: Optional[Path] = None,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        max_size_mb: int = DEFAULT_MAX_SIZE_MB,
    ):
        """
        Initialize checkpoint cleanup manager.
        
        Args:
            checkpoint_base: Base directory for checkpoints (default: ~/.fablegear/checkpoints)
            max_age_days: Maximum age of checkpoints in days before cleanup
            max_size_mb: Maximum total size of checkpoints in MB before cleanup
        """
        self.checkpoint_base = checkpoint_base or Path.home() / ".fablegear" / "checkpoints"
        self.max_age_days = max_age_days
        self.max_size_mb = max_size_mb
        self._lock = threading.Lock()
        
        log.debug(
            "CheckpointCleanup initialized: base=%s, max_age=%d days, max_size=%d MB",
            checkpoint_base,
            max_age_days,
            max_size_mb
        )
    
    def cleanup_old_checkpoints(
        self,
        tool_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, any]:
        """
        Remove checkpoints older than max_age_days.
        
        Args:
            tool_name: Specific tool to clean up (None = all tools)
            dry_run: If True, only report what would be deleted
            
        Returns:
            Dict with cleanup results
        """
        with self._lock:
            results = {
                "deleted_count": 0,
                "deleted_size_bytes": 0,
                "skipped_count": 0,
                "errors": [],
                "dry_run": dry_run,
            }
            
            if not self.checkpoint_base.exists():
                log.debug("Checkpoint base directory does not exist: %s", self.checkpoint_base)
                return results
            
            # Determine which tool directories to process
            if tool_name:
                tool_dirs = [self.checkpoint_base / tool_name]
            else:
                tool_dirs = [d for d in self.checkpoint_base.iterdir() if d.is_dir()]
            
            cutoff_time = datetime.now() - timedelta(days=self.max_age_days)
            
            for tool_dir in tool_dirs:
                if not tool_dir.exists():
                    continue
                
                for checkpoint_file in tool_dir.glob("*.json*"):
                    try:
                        # Check file age
                        file_mtime = datetime.fromtimestamp(checkpoint_file.stat().st_mtime)
                        
                        if file_mtime < cutoff_time:
                            file_size = checkpoint_file.stat().st_size
                            
                            if dry_run:
                                results["deleted_count"] += 1
                                results["deleted_size_bytes"] += file_size
                                log.debug(
                                    "Would delete old checkpoint: %s (age: %d days)",
                                    checkpoint_file.name,
                                    (datetime.now() - file_mtime).days
                                )
                            else:
                                checkpoint_file.unlink()
                                results["deleted_count"] += 1
                                results["deleted_size_bytes"] += file_size
                                log.info(
                                    "Deleted old checkpoint: %s (age: %d days)",
                                    checkpoint_file.name,
                                    (datetime.now() - file_mtime).days
                                )
                        else:
                            results["skipped_count"] += 1
                            
                    except Exception as exc:
                        error_msg = f"Error processing {checkpoint_file}: {exc}"
                        results["errors"].append(error_msg)
                        log.warning(error_msg)
            
            log.info(
                "Checkpoint cleanup complete: deleted=%d, skipped=%d, size=%.2f MB",
                results["deleted_count"],
                results["skipped_count"],
                results["deleted_size_bytes"] / (1024 * 1024)
            )
            
            return results
    
    def cleanup_by_size(
        self,
        target_size_mb: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, any]:
        """
        Remove oldest checkpoints to stay under size limit.
        
        Args:
            target_size_mb: Target size in MB (default: self.max_size_mb)
            dry_run: If True, only report what would be deleted
            
        Returns:
            Dict with cleanup results
        """
        with self._lock:
            target_size_mb = target_size_mb or self.max_size_mb
            target_size_bytes = target_size_mb * 1024 * 1024
            
            results = {
                "deleted_count": 0,
                "deleted_size_bytes": 0,
                "final_size_bytes": 0,
                "errors": [],
                "dry_run": dry_run,
            }
            
            if not self.checkpoint_base.exists():
                return results
            
            # Get all checkpoint files with their metadata
            checkpoint_files = []
            for tool_dir in self.checkpoint_base.iterdir():
                if not tool_dir.is_dir():
                    continue
                for checkpoint_file in tool_dir.glob("*.json*"):
                    try:
                        stat = checkpoint_file.stat()
                        checkpoint_files.append({
                            "path": checkpoint_file,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        })
                    except Exception as exc:
                        results["errors"].append(f"Error reading {checkpoint_file}: {exc}")
            
            # Calculate current total size
            current_size = sum(cf["size"] for cf in checkpoint_files)
            results["final_size_bytes"] = current_size
            
            if current_size <= target_size_bytes:
                log.debug("Checkpoint size under limit: %.2f MB", current_size / (1024 * 1024))
                return results
            
            # Sort by modification time (oldest first)
            checkpoint_files.sort(key=lambda x: x["mtime"])
            
            # Delete oldest until under limit
            for cf in checkpoint_files:
                if current_size <= target_size_bytes:
                    break
                
                try:
                    if dry_run:
                        results["deleted_count"] += 1
                        results["deleted_size_bytes"] += cf["size"]
                        current_size -= cf["size"]
                        log.debug("Would delete: %s", cf["path"].name)
                    else:
                        cf["path"].unlink()
                        results["deleted_count"] += 1
                        results["deleted_size_bytes"] += cf["size"]
                        current_size -= cf["size"]
                        log.info("Deleted for size cleanup: %s", cf["path"].name)
                        
                except Exception as exc:
                    results["errors"].append(f"Error deleting {cf['path']}: {exc}")
            
            results["final_size_bytes"] = current_size
            
            log.info(
                "Size-based cleanup complete: deleted=%d files, freed=%.2f MB, final=%.2f MB",
                results["deleted_count"],
                results["deleted_size_bytes"] / (1024 * 1024),
                results["final_size_bytes"] / (1024 * 1024)
            )
            
            return results
    
    def get_checkpoint_stats(self) -> Dict[str, any]:
        """
        Get statistics about current checkpoint usage.
        
        Returns:
            Dict with checkpoint statistics
        """
        stats = {
            "total_count": 0,
            "total_size_bytes": 0,
            "by_tool": {},
            "oldest_checkpoint": None,
            "newest_checkpoint": None,
        }
        
        if not self.checkpoint_base.exists():
            return stats
        
        oldest_time = None
        newest_time = None
        oldest_path = None
        newest_path = None
        
        for tool_dir in self.checkpoint_base.iterdir():
            if not tool_dir.is_dir():
                continue
            
            tool_name = tool_dir.name
            tool_count = 0
            tool_size = 0
            
            for checkpoint_file in tool_dir.glob("*.json*"):
                try:
                    stat = checkpoint_file.stat()
                    tool_count += 1
                    tool_size += stat.st_size
                    
                    file_time = stat.st_mtime
                    if oldest_time is None or file_time < oldest_time:
                        oldest_time = file_time
                        oldest_path = checkpoint_file
                    if newest_time is None or file_time > newest_time:
                        newest_time = file_time
                        newest_path = checkpoint_file
                        
                except Exception:
                    continue
            
            if tool_count > 0:
                stats["by_tool"][tool_name] = {
                    "count": tool_count,
                    "size_bytes": tool_size,
                }
                stats["total_count"] += tool_count
                stats["total_size_bytes"] += tool_size
        
        if oldest_path:
            stats["oldest_checkpoint"] = {
                "path": str(oldest_path),
                "age_days": (datetime.now() - datetime.fromtimestamp(oldest_time)).days,
            }
        
        if newest_path:
            stats["newest_checkpoint"] = {
                "path": str(newest_path),
                "age_days": (datetime.now() - datetime.fromtimestamp(newest_time)).days,
            }
        
        return stats
    
    def cleanup_all(self, dry_run: bool = False) -> Dict[str, any]:
        """
        Run both age-based and size-based cleanup.
        
        Args:
            dry_run: If True, only report what would be deleted
            
        Returns:
            Combined cleanup results
        """
        age_results = self.cleanup_old_checkpoints(dry_run=dry_run)
        size_results = self.cleanup_by_size(dry_run=dry_run)
        
        return {
            "age_based": age_results,
            "size_based": size_results,
            "total_deleted": age_results["deleted_count"] + size_results["deleted_count"],
            "total_freed_bytes": age_results["deleted_size_bytes"] + size_results["deleted_size_bytes"],
            "dry_run": dry_run,
        }