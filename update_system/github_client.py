"""
update_system.github_client — GitHub API client for release information.

Handles communication with GitHub API to fetch release information
and update details.
"""

import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class GitHubClient:
    """
    Client for GitHub API operations.
    
    Fetches release information from GitHub with proper error handling
    and rate limit awareness.
    """
    
    def __init__(self, repo: str = "fabledharbinger0993/FableGear"):
        """
        Initialize the GitHub client.
        
        Args:
            repo: GitHub repository in format "owner/repo"
        """
        self.repo = repo
        self.api_base = "https://api.github.com"
        self.timeout = 10  # seconds
    
    def get_latest_release(self) -> Optional[Dict[str, Any]]:
        """
        Get the latest release information from GitHub.
        
        Returns:
            Release information dictionary or None if request fails
        """
        url = f"{self.api_base}/repos/{self.repo}/releases/latest"
        
        try:
            request = urllib.request.Request(url)
            request.add_header("User-Agent", "FableGear-Updater")
            
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                import json
                data = json.load(response)
                return data
                
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.warning("No releases found for repository: %s", self.repo)
            else:
                log.warning("GitHub API error (HTTP %d): %s", e.code, e.reason)
            return None
            
        except urllib.error.URLError as e:
            log.warning("GitHub API connection failed: %s", e.reason)
            return None
            
        except Exception as exc:
            log.error("Failed to fetch latest release: %s", exc)
            return None
    
    def get_release_by_tag(self, tag: str) -> Optional[Dict[str, Any]]:
        """
        Get release information for a specific tag.
        
        Args:
            tag: Git tag to fetch
            
        Returns:
            Release information dictionary or None if request fails
        """
        url = f"{self.api_base}/repos/{self.repo}/releases/tags/{tag}"
        
        try:
            request = urllib.request.Request(url)
            request.add_header("User-Agent", "FableGear-Updater")
            
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                import json
                data = json.load(response)
                return data
                
        except Exception as exc:
            log.error("Failed to fetch release for tag %s: %s", tag, exc)
            return None
    
    def get_all_releases(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get all releases from the repository.
        
        Returns:
            List of release dictionaries or None if request fails
        """
        url = f"{self.api_base}/repos/{self.repo}/releases"
        
        try:
            request = urllib.request.Request(url)
            request.add_header("User-Agent", "FableGear-Updater")
            
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                import json
                data = json.load(response)
                return data
                
        except Exception as exc:
            log.error("Failed to fetch all releases: %s", exc)
            return None