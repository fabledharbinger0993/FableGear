"""
fablegear_database.examples — Example usage of the database layer.

Demonstrates how to use the new database-first architecture for
library management, duplicate detection, and Pioneer integration.
"""

from pathlib import Path

from fablegear_database import (
    DatabaseSync,
    FableGearDatabase,
    FileImporter,
    PioneerExporter,
)


def example_initialize_database():
    """Example: Initialize FableGear database."""

    # Create database with default configuration
    db = FableGearDatabase()

    # Get database statistics
    stats = db.get_statistics()
    print(f"Database statistics: {stats}")

    return db


def example_import_files():
    """Example: Import audio files into database."""

    db = FableGearDatabase()
    importer = FileImporter(db)

    # Import files from multiple drives
    root_paths = [
        Path("/Music/Drive1"),
        Path("/Music/Drive2"),
    ]

    # Progress callback
    def progress_callback(current, total):
        print(f"Progress: {current}/{total} ({current/total*100:.1f}%)")

    # Import files
    stats = importer.import_files(
        root_paths=root_paths,
        progress_callback=progress_callback,
        force_refresh=False,
    )

    print(f"Import complete: {stats}")
    return stats


def example_fast_duplicate_detection():
    """Example: Fast duplicate detection using database."""

    db = FableGearDatabase()

    # Find duplicates by file hash (instant)
    hash_duplicates = db.find_duplicates_by_hash()
    print(f"Found {len(hash_duplicates)} duplicate groups by hash")

    # Find duplicates by acoustic fingerprint (medium speed)
    fingerprint_duplicates = db.find_duplicates_by_fingerprint()
    print(f"Found {len(fingerprint_duplicates)} duplicate groups by fingerprint")

    return hash_duplicates, fingerprint_duplicates


def example_library_search():
    """Example: Fast library search using database."""

    db = FableGearDatabase()

    # Search by artist
    results = db.search_content("Daft Punk", fields=["artist"])
    print(f"Found {len(results)} tracks by Daft Punk")

    # Search by title
    results = db.search_content("One More Time", fields=["title"])
    print(f"Found {len(results)} tracks matching 'One More Time'")

    # Search across all fields
    results = db.search_content("techno")
    print(f"Found {len(results)} tracks matching 'techno'")

    return results


def example_database_sync():
    """Example: Sync database with filesystem."""

    db = FableGearDatabase()
    sync = DatabaseSync(db)

    # Sync database to filesystem
    stats = sync.sync_database_to_filesystem(verify_integrity=True)
    print(f"Sync complete: {stats}")

    # Find orphaned files
    orphaned = sync.find_orphaned_files([Path("/Music/Drive1")])
    print(f"Found {len(orphaned)} orphaned files")

    # Find stale records
    stale = sync.find_stale_records()
    print(f"Found {len(stale)} stale database records")

    return stats


def example_pioneer_export():
    """Example: Export to Pioneer-compatible format."""

    db = FableGearDatabase()
    exporter = PioneerExporter(db)

    # Export to Pioneer XML
    xml_path = Path.home() / "Desktop" / "fablegear_pioneer.xml"
    success = exporter.export_to_pioneer_xml(xml_path)
    print(f"Pioneer XML export: {'success' if success else 'failed'}")

    # Export to Rekordbox-compatible database
    db_path = Path.home() / "Desktop" / "fablegear_rekordbox.db"
    success = exporter.export_to_rekordbox_db(db_path)
    print(f"Rekordbox DB export: {'success' if success else 'failed'}")

    return success


def example_database_operations():
    """Example: Basic database operations."""

    db = FableGearDatabase()

    # Insert a record
    from fablegear_database.database import ContentRecord

    record = ContentRecord(
        file_path="/Music/test.mp3",
        file_name="test.mp3",
        file_size=1024000,
        artist="Test Artist",
        title="Test Track",
        bpm=128.0,
        key="Am",
    )

    record_id = db.insert_content(record)
    print(f"Inserted record with ID: {record_id}")

    # Get record by path
    retrieved = db.get_content_by_path("/Music/test.mp3")
    print(f"Retrieved record: {retrieved}")

    # Update record
    success = db.update_content(record_id, {"bpm": 130.0})
    print(f"Update successful: {success}")

    # Get all records
    all_records = db.get_all_content(limit=10)
    print(f"Total records: {len(all_records)}")

    return record_id


def example_database_backups():
    """Example: Database backup and restore."""

    db = FableGearDatabase()

    # Create backup
    backup_path = db.create_backup()
    print(f"Backup created: {backup_path}")

    # Restore from backup
    success = db.restore_backup(backup_path)
    print(f"Restore successful: {success}")

    return backup_path


def example_compared_to_old_system():
    """Example: Comparison between old and new systems."""

    print("=== OLD SYSTEM (File-First) ===")
    print("Duplicate detection: Scan all files → Audio processing → Compare (slow)")
    print("Library browse: Scan filesystem each time (slow)")
    print("Change detection: Full re-scan required (slow)")

    print("\n=== NEW SYSTEM (Database-First) ===")
    print("Duplicate detection: Database query (instant)")
    print("Library browse: Database query (instant)")
    print("Change detection: File hash comparison (fast)")

    db = FableGearDatabase()

    # Demonstrate speed difference
    import time

    # Fast duplicate detection
    start = time.time()
    duplicates = db.find_duplicates_by_hash()
    fast_time = time.time() - start

    print(f"\nDuplicate detection (new): {len(duplicates)} groups in {fast_time:.3f}s")
    print("Duplicate detection (old): Would take minutes for large libraries")


if __name__ == "__main__":
    print("=== Initialize Database Example ===")
    example_initialize_database()

    print("\n=== Import Files Example ===")
    example_import_files()

    print("\n=== Fast Duplicate Detection Example ===")
    example_fast_duplicate_detection()

    print("\n=== Library Search Example ===")
    example_library_search()

    print("\n=== Database Sync Example ===")
    example_database_sync()

    print("\n=== Pioneer Export Example ===")
    example_pioneer_export()

    print("\n=== Database Operations Example ===")
    example_database_operations()

    print("\n=== Database Backups Example ===")
    example_database_backups()

    print("\n=== System Comparison Example ===")
    example_compared_to_old_system()
