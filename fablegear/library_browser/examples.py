"""
library_browser.examples — Example usage of the library browser system.

Demonstrates how to use the three-view library browser for different
use cases and workflows.
"""

from pathlib import Path

from library_browser import LibraryBrowser, ViewMode


def example_rekordbox_view():
    """Example: Using Rekordbox View to check library health."""

    browser = LibraryBrowser()
    browser.set_view_mode(ViewMode.REKORDBOX)

    # Load data
    browser.refresh()

    # Get statistics
    stats = browser.get_statistics()
    print(f"Total tracks: {stats['total_tracks']}")
    print(f"Valid files: {stats['valid_files']}")
    print(f"Missing files: {stats['missing_files']}")
    print(f"Health: {stats['health_percentage']:.1f}%")

    # Get tracks
    tracks = browser.get_tracks(limit=100)
    for track in tracks[:10]:  # Show first 10
        print(f"{track.title} - {track.artist} [{track.file_status}]")

    # Search for specific tracks
    results = browser.search("techno", ["title", "genre"])
    print(f"Found {len(results)} matching tracks")


def example_local_view():
    """Example: Using Local View to browse all files across drives."""

    browser = LibraryBrowser()
    browser.set_view_mode(ViewMode.LOCAL)

    # Scan multiple drives
    roots = [
        Path("/Music/Drive1"),
        Path("/Music/Drive2"),
        Path("/Music/External"),
    ]

    # This would require extending the browser to accept roots
    # For now, showing the interface
    print("Local View would scan:", [str(r) for r in roots])

    # Sort by BPM
    browser.sort("bpm", ascending=False)

    # Filter by format
    browser.filter({"format": "flac"})

    # Get files
    files = browser.get_files(limit=50)
    print(f"Found {len(files)} FLAC files sorted by BPM")


def example_integrated_view():
    """Example: Using Integrated View for sync operations."""

    browser = LibraryBrowser()
    browser.set_view_mode(ViewMode.INTEGRATED)

    # Load data
    browser.refresh()

    # Get statistics
    stats = browser.get_statistics()
    print(f"Total entries: {stats['total_entries']}")
    print(f"Synced: {stats['synced']}")
    print(f"Broken: {stats['broken']}")
    print(f"Orphans: {stats['orphan']}")

    # Get integrated data
    integrated_data = browser.get_integrated_view(limit=100)

    # Show broken entries
    broken_entries = [e for e in integrated_data if e.sync_status == "broken"]
    print(f"Found {len(broken_entries)} broken entries")

    # Show orphan files
    orphan_entries = [e for e in integrated_data if e.sync_status == "orphan"]
    total_orphans = sum(len(e.orphan_files) for e in orphan_entries)
    print(f"Found {total_orphans} orphan files")


def example_view_switching():
    """Example: Switching between views for different tasks."""

    browser = LibraryBrowser()

    # Start with Rekordbox View for health check
    browser.set_view_mode(ViewMode.REKORDBOX)
    rb_stats = browser.get_statistics()
    print(f"Rekordbox health: {rb_stats['health_percentage']:.1f}%")

    # Switch to Local View to find new files
    browser.set_view_mode(ViewMode.LOCAL)
    # Would scan all drives and show files not in Rekordbox

    # Switch to Integrated View for sync operations
    browser.set_view_mode(ViewMode.INTEGRATED)
    int_stats = browser.get_statistics()
    print(f"Ready to import {int_stats['orphan']} orphan entries")


def example_search_and_filter():
    """Example: Advanced search and filtering across views."""

    browser = LibraryBrowser()
    browser.set_view_mode(ViewMode.REKORDBOX)

    # Search by artist
    results = browser.search("Daft Punk", ["artist"])
    print(f"Found {len(results)} Daft Punk tracks")

    # Filter by genre
    browser.filter({"genre": "House"})

    # Sort by BPM
    browser.sort("bpm", ascending=False)

    # Get filtered and sorted results
    browser.get_tracks(limit=20)
    print("Top 20 House tracks by BPM")


if __name__ == "__main__":
    print("=== Rekordbox View Example ===")
    example_rekordbox_view()

    print("\n=== Local View Example ===")
    example_local_view()

    print("\n=== Integrated View Example ===")
    example_integrated_view()

    print("\n=== View Switching Example ===")
    example_view_switching()

    print("\n=== Search and Filter Example ===")
    example_search_and_filter()
