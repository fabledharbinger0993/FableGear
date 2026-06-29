"""
update_system — Intelligent auto-update system for FableGear.

Provides automatic release checking, user approval workflow, and safe
update mechanisms with rollback support.

Key Features:
- Automatic GitHub release checking on app launch
- User approval workflow with release notes
- Safe update process with automatic rollback
- Quiet failure mode for network issues
- Integration with existing launch.sh update mechanism

Example Usage:
    from update_system import UpdateManager, UpdateConfig
    
    manager = UpdateManager()
    
    # Check for updates on startup
    update_info = manager.check_for_updates()
    
    if update_info.has_update:
        # Offer update to user
        user_choice = manager.offer_update(update_info)
        
        if user_choice == "update_now":
            success = manager.perform_update(update_info)
"""

from .core import UpdateManager, UpdateInfo, UpdateConfig, UpdateChoice
from .github_client import GitHubClient
from .rollback import RollbackManager
from .ui_integration import UpdateUI

__all__ = [
    "UpdateManager",
    "UpdateInfo",
    "UpdateConfig",
    "UpdateChoice",
    "GitHubClient",
    "RollbackManager",
    "UpdateUI",
]
