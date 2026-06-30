"""
update_system.examples — Example usage of the update system.

Demonstrates how to use the auto-update system for various scenarios.
"""

from pathlib import Path
from update_system import UpdateManager, UpdateConfig, UpdateChoice


def example_basic_update_check():
    """Example: Basic update check on startup."""
    
    manager = UpdateManager()
    
    # Check for updates
    update_info = manager.check_for_updates()
    
    if update_info.has_update:
        print(f"Update available: {update_info.current_version} -> {update_info.latest_version}")
        print(f"Release notes: {update_info.release_notes[:200]}...")
        
        # Offer update to user
        choice = manager.offer_update(update_info)
        
        if choice == UpdateChoice.UPDATE_NOW:
            success = manager.perform_update(update_info)
            if success:
                print("Update completed successfully")
            else:
                print("Update failed, rollback performed")
    else:
        print("No updates available")


def example_custom_config():
    """Example: Using custom update configuration."""
    
    config = UpdateConfig(
        auto_check_enabled=True,
        check_interval_hours=12,
        quiet_failure_mode=True,
        backup_before_update=True,
        rollback_on_failure=True,
        skip_versions=["v1.0.0", "v1.0.1"],  # Skip specific versions
        dev_mode=False,
    )
    
    manager = UpdateManager(config)
    update_info = manager.check_for_updates()
    
    print(f"Update check with custom config: has_update={update_info.has_update}")


def example_manual_update():
    """Example: Manually triggering an update check."""
    
    manager = UpdateManager()
    
    # Force update check even if auto-check is disabled
    temp_config = UpdateConfig(auto_check_enabled=True)
    manager.config = temp_config
    
    update_info = manager.check_for_updates()
    
    if update_info.has_update:
        print(f"Manual update check found: {update_info.latest_version}")


def example_rollback_scenario():
    """Example: Update failure and rollback scenario."""
    
    manager = UpdateManager()
    
    # Simulate an update that fails
    class MockUpdateInfo:
        has_update = True
        current_version = "v1.0.0"
        latest_version = "v1.1.0"
        release_notes = "Bug fixes and improvements"
        release_url = "https://github.com/fabledharbinger0993/FableGear/releases/tag/v1.1.0"
        published_at = "2026-06-28T00:00:00Z"
        is_forward_update = True
    
    # The update manager would handle rollback automatically
    # if the update fails and rollback_on_failure is enabled
    print("Update manager configured to auto-rollback on failure")


def example_skip_version():
    """Example: Skipping a specific version."""
    
    config = UpdateConfig(
        skip_versions=["v1.0.5"],  # Skip this specific version
    )
    
    manager = UpdateManager(config)
    update_info = manager.check_for_updates()
    
    # If the latest version is v1.0.5, it will be skipped
    print(f"Update check with skip list: has_update={update_info.has_update}")


def example_dev_mode():
    """Example: Development mode with updates disabled."""
    
    config = UpdateConfig(
        dev_mode=True,  # Updates disabled in dev mode
    )
    
    manager = UpdateManager(config)
    update_info = manager.check_for_updates()
    
    # In dev mode, has_update should always be False
    print(f"Dev mode update check: has_update={update_info.has_update}")


if __name__ == "__main__":
    print("=== Basic Update Check Example ===")
    example_basic_update_check()
    
    print("\n=== Custom Config Example ===")
    example_custom_config()
    
    print("\n=== Manual Update Example ===")
    example_manual_update()
    
    print("\n=== Rollback Scenario Example ===")
    example_rollback_scenario()
    
    print("\n=== Skip Version Example ===")
    example_skip_version()
    
    print("\n=== Dev Mode Example ===")
    example_dev_mode()