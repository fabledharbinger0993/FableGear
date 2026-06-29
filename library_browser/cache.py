"""
library_browser.cache — Caching system for library browser views.

Provides efficient caching of view data to improve performance
for large libraries and reduce filesystem/database access.
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class ViewCache:
    """
    Caches library view data for improved performance.
    
    Manages cache entries with TTL, size limits, and automatic cleanup.
    """
    
    def __init__(self, cache_dir: Optional[Path] = None, max_size_mb: int = 100):
        """
        Initialize the view cache.
        
        Args:
            cache_dir: Directory for cache files (default: ~/.fablegear/cache)
            max_size_mb: Maximum cache size in megabytes
        """
        self.cache_dir = cache_dir or Path.home() / ".fablegear" / "cache"
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self._lock = threading.Lock()
        self._memory_cache: Dict[str, Any] = {}
        self._cache_ttl = timedelta(hours=1)  # Cache entries expire after 1 hour
        
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get cached data by key.
        
        Args:
            key: Cache key
            
        Returns:
            Cached data or None if not found/expired
        """
        with self._lock:
            # Check memory cache first
            if key in self._memory_cache:
                entry = self._memory_cache[key]
                if self._is_valid(entry):
                    return entry["data"]
                else:
                    del self._memory_cache[key]
            
            # Check disk cache
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as f:
                        entry = json.load(f)
                    
                    if self._is_valid(entry):
                        # Promote to memory cache
                        self._memory_cache[key] = entry
                        return entry["data"]
                    else:
                        # Expired, remove from disk
                        cache_file.unlink()
                        
                except Exception as exc:
                    log.error("Failed to load cache entry %s: %s", key, exc)
            
            return None
    
    def set(self, key: str, data: Any, ttl: Optional[timedelta] = None) -> bool:
        """
        Cache data with the given key.
        
        Args:
            key: Cache key
            data: Data to cache
            ttl: Time-to-live (default: 1 hour)
            
        Returns:
            True if cache succeeded
        """
        with self._lock:
            try:
                entry = {
                    "data": data,
                    "created_at": datetime.now().isoformat(),
                    "ttl": (ttl or self._cache_ttl).total_seconds(),
                }
                
                # Store in memory cache
                self._memory_cache[key] = entry
                
                # Store in disk cache
                cache_file = self.cache_dir / f"{key}.json"
                with open(cache_file, "w") as f:
                    json.dump(entry, f)
                
                # Check cache size and cleanup if needed
                self._check_size_limit()
                
                return True
                
            except Exception as exc:
                log.error("Failed to cache data for key %s: %s", key, exc)
                return False
    
    def invalidate(self, key: str) -> None:
        """
        Invalidate a specific cache entry.
        
        Args:
            key: Cache key to invalidate
        """
        with self._lock:
            # Remove from memory cache
            if key in self._memory_cache:
                del self._memory_cache[key]
            
            # Remove from disk cache
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                try:
                    cache_file.unlink()
                except Exception as exc:
                    log.error("Failed to delete cache file %s: %s", cache_file, exc)
    
    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._memory_cache.clear()
            
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    cache_file.unlink()
                except Exception as exc:
                    log.error("Failed to delete cache file %s: %s", cache_file, exc)
    
    def _is_valid(self, entry: Dict[str, Any]) -> bool:
        """
        Check if a cache entry is still valid.
        
        Args:
            entry: Cache entry dictionary
            
        Returns:
            True if entry is valid
        """
        try:
            created_at = datetime.fromisoformat(entry["created_at"])
            ttl_seconds = entry.get("ttl", self._cache_ttl.total_seconds())
            
            return datetime.now() - created_at < timedelta(seconds=ttl_seconds)
            
        except Exception:
            return False
    
    def _check_size_limit(self) -> None:
        """Check cache size and cleanup if over limit."""
        try:
            total_size = sum(
                f.stat().st_size 
                for f in self.cache_dir.glob("*.json")
            )
            
            if total_size > self.max_size_bytes:
                # Remove oldest entries until under limit
                entries = []
                for cache_file in self.cache_dir.glob("*.json"):
                    try:
                        stat = cache_file.stat()
                        entries.append({
                            "file": cache_file,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        })
                    except Exception:
                        continue
                
                # Sort by modification time (oldest first)
                entries.sort(key=lambda x: x["mtime"])
                
                # Remove oldest entries
                for entry in entries:
                    if total_size <= self.max_size_bytes:
                        break
                    
                    try:
                        entry["file"].unlink()
                        total_size -= entry["size"]
                    except Exception:
                        continue
                        
        except Exception as exc:
            log.error("Error checking cache size limit: %s", exc)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        with self._lock:
            memory_count = len(self._memory_cache)
            disk_count = len(list(self.cache_dir.glob("*.json")))
            disk_size = sum(f.stat().st_size for f in self.cache_dir.glob("*.json"))
            
            return {
                "memory_entries": memory_count,
                "disk_entries": disk_count,
                "disk_size_bytes": disk_size,
                "disk_size_mb": disk_size / (1024 * 1024),
                "max_size_mb": self.max_size_bytes / (1024 * 1024),
            }