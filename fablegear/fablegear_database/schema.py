"""
fablegear_database.schema — Rekordbox-compatible database schema.

Defines database schema that is compatible with Pioneer Rekordbox
structure while being FableGear-specific. This enables:
- Direct interoperability with Rekordbox databases
- Fast duplicate detection via database queries
- Pioneer hardware compatibility
- Clean separation from physical file operations
"""

import logging
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class TableType(Enum):
    """Database table types."""
    CONTENT = "fg_content"
    ARTIST = "fg_artist"
    ALBUM = "fg_album"
    GENRE = "fg_genre"
    KEY = "fg_key"
    LABEL = "fg_label"
    PLAYLIST = "fg_playlist"
    PLAYLIST_SONG = "fg_playlist_song"
    PLAYLIST_FOLDER = "fg_playlist_folder"
    CUE = "fg_cue"
    BEATGRID = "fg_beatgrid"


class DatabaseSchema:
    """
    Rekordbox-compatible database schema for FableGear.

    Tables mirror Rekordbox structure where appropriate for compatibility,
    while adding FableGear-specific fields for enhanced functionality.
    """

    # Main content table (similar to Rekordbox's djmdContent)
    CONTENT_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL UNIQUE,
        file_name TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        duration REAL,
        format TEXT,
        bit_rate INTEGER,
        sample_rate INTEGER,
        modified_date TEXT,
        file_hash TEXT,
        acoustic_fingerprint TEXT,

        -- Metadata fields
        artist TEXT,
        album TEXT,
        title TEXT,
        bpm REAL,  -- FG DB stores raw float; Rekordbox stores ×100 int — cross-DB code must transform
        key TEXT,
        genre TEXT,
        label TEXT,
        year INTEGER,
        track_number INTEGER,
        disc_number INTEGER,
        comment TEXT,
        rating INTEGER DEFAULT 0,

        -- Drive/location tracking
        drive TEXT,
        relative_path TEXT,

        -- Rekordbox compatibility fields
        rekordbox_id INTEGER,
        rekordbox_playlist_id INTEGER,

        -- FableGear-specific fields
        in_rekordbox BOOLEAN DEFAULT 0,
        last_scanned TEXT,
        fingerprint_quality INTEGER DEFAULT 0,
        is_corrupted BOOLEAN DEFAULT 0,
        processing_status TEXT DEFAULT 'unprocessed',
        color TEXT,

        -- Standard timestamps
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_file_path ON fg_content(file_path);
    CREATE INDEX IF NOT EXISTS idx_file_hash ON fg_content(file_hash);
    CREATE INDEX IF NOT EXISTS idx_acoustic_fp ON fg_content(acoustic_fingerprint);
    CREATE INDEX IF NOT EXISTS idx_artist ON fg_content(artist);
    CREATE INDEX IF NOT EXISTS idx_album ON fg_content(album);
    CREATE INDEX IF NOT EXISTS idx_bpm ON fg_content(bpm);
    CREATE INDEX IF NOT EXISTS idx_key ON fg_content(key);
    CREATE INDEX IF NOT EXISTS idx_genre ON fg_content(genre);
    CREATE INDEX IF NOT EXISTS idx_drive ON fg_content(drive);
    CREATE INDEX IF NOT EXISTS idx_rekordbox_id ON fg_content(rekordbox_id);
    CREATE INDEX IF NOT EXISTS idx_processing_status ON fg_content(processing_status);
    """

    # Artist table (similar to djmdArtist)
    ARTIST_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_artist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        search_str TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_artist_name ON fg_artist(name);
    """

    # Album table (similar to djmdAlbum)
    ALBUM_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_album (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        album_artist_id INTEGER,
        image_path TEXT,
        compilation BOOLEAN DEFAULT 0,
        search_str TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_album_name ON fg_album(name);
    """

    # Genre table (similar to djmdGenre)
    GENRE_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_genre (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_genre_name ON fg_genre(name);
    """

    # Key table (similar to djmdKey)
    KEY_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_key (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scale_name TEXT NOT NULL,
        camelot_wheel INTEGER,
        open_key INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_key_name ON fg_key(scale_name);
    """

    # Label table (similar to djmdLabel)
    LABEL_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_label (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_label_name ON fg_label(name);
    """

    # Playlist table (similar to djmdPlaylist)
    PLAYLIST_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_playlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        playlist_type TEXT DEFAULT 'playlist',
        parent_id INTEGER,
        track_count INTEGER DEFAULT 0,
        rekordbox_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_playlist_name ON fg_playlist(name);
    CREATE INDEX IF NOT EXISTS idx_playlist_parent ON fg_playlist(parent_id);
    """

    # Playlist song mapping table (similar to djmdSongPlaylist)
    PLAYLIST_SONG_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_playlist_song (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist_id INTEGER NOT NULL,
        content_id INTEGER NOT NULL,
        track_number INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (playlist_id) REFERENCES fg_playlist(id) ON DELETE CASCADE,
        FOREIGN KEY (content_id) REFERENCES fg_content(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_playlist_song_playlist ON fg_playlist_song(playlist_id);
    CREATE INDEX IF NOT EXISTS idx_playlist_song_content ON fg_playlist_song(content_id);
    """

    # Database metadata table
    METADATA_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_metadata (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """

    # Processing log table (for tracking operations)
    PROCESSING_LOG_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_processing_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_type TEXT NOT NULL,
        file_path TEXT,
        status TEXT,
        error_message TEXT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        metadata TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_processing_log_operation ON fg_processing_log(operation_type);
    CREATE INDEX IF NOT EXISTS idx_processing_log_status ON fg_processing_log(status);
    """

    # Cue and loop table
    CUE_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_cue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER NOT NULL,
        kind INTEGER NOT NULL, -- 0 = Memory, 1 = Hotcue, 2 = Loop, 3 = Active Loop
        slot INTEGER,
        in_msec INTEGER NOT NULL,
        out_msec INTEGER,
        color TEXT,
        comment TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (content_id) REFERENCES fg_content(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_cue_content_id ON fg_cue(content_id);
    CREATE INDEX IF NOT EXISTS idx_cue_content_in ON fg_cue(content_id, in_msec);
    """

    # Beatgrid table (variable tempo support)
    BEATGRID_TABLE = """
    CREATE TABLE IF NOT EXISTS fg_beatgrid (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_id INTEGER NOT NULL,
        beat_number INTEGER NOT NULL,
        time_msec INTEGER NOT NULL,
        bpm REAL NOT NULL,
        meter_numerator INTEGER DEFAULT 4,
        meter_denominator INTEGER DEFAULT 4,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (content_id) REFERENCES fg_content(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_beatgrid_content_id ON fg_beatgrid(content_id);
    CREATE INDEX IF NOT EXISTS idx_beatgrid_content_time ON fg_beatgrid(content_id, time_msec);
    """

    @staticmethod
    def create_schema(db_path: Path) -> bool:
        """
        Create all tables in the database.

        Args:
            db_path: Path to the database file

        Returns:
            True if schema creation succeeded
        """
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Enable foreign keys
            cursor.execute("PRAGMA foreign_keys = ON")

            # Create tables. Each *_TABLE string bundles a CREATE TABLE plus
            # its CREATE INDEX statements, so executescript() is required —
            # cursor.execute() runs only a single statement and raises on the
            # rest, which previously left the database with zero tables.
            cursor.executescript(DatabaseSchema.CONTENT_TABLE)
            cursor.executescript(DatabaseSchema.ARTIST_TABLE)
            cursor.executescript(DatabaseSchema.ALBUM_TABLE)
            cursor.executescript(DatabaseSchema.GENRE_TABLE)
            cursor.executescript(DatabaseSchema.KEY_TABLE)
            cursor.executescript(DatabaseSchema.LABEL_TABLE)
            cursor.executescript(DatabaseSchema.PLAYLIST_TABLE)
            cursor.executescript(DatabaseSchema.PLAYLIST_SONG_TABLE)
            cursor.executescript(DatabaseSchema.METADATA_TABLE)
            cursor.executescript(DatabaseSchema.PROCESSING_LOG_TABLE)
            cursor.executescript(DatabaseSchema.CUE_TABLE)
            cursor.executescript(DatabaseSchema.BEATGRID_TABLE)

            conn.commit()
            conn.close()

            return True

        except Exception as exc:
            print(f"Failed to create database schema: {exc}")
            return False

    @staticmethod
    def get_schema_version() -> str:
        """Get the current schema version."""
        return "1.1.0"

    @staticmethod
    def validate_schema(db_path: Path) -> list[str]:
        """
        Validate that the database schema is correct.

        Args:
            db_path: Path to the database file

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Check for required tables
            required_tables = [
                "fg_content", "fg_artist", "fg_album", "fg_genre",
                "fg_key", "fg_label", "fg_playlist", "fg_playlist_song",
                "fg_cue", "fg_beatgrid"
            ]

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {row[0] for row in cursor.fetchall()}

            for table in required_tables:
                if table not in existing_tables:
                    errors.append(f"Missing required table: {table}")

            conn.close()

        except Exception as exc:
            errors.append(f"Schema validation failed: {exc}")

        return errors

    @staticmethod
    def upgrade_schema(db_path: Path) -> bool:
        """
        Upgrade the schema of an existing database if needed.
        Adds new columns and tables for schema version 1.1.0.
        """
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Check if color column exists in fg_content
            cursor.execute("PRAGMA table_info(fg_content)")
            columns = {row[1] for row in cursor.fetchall()}
            if "color" not in columns:
                log.info("Migrating database: adding color column to fg_content")
                cursor.execute("ALTER TABLE fg_content ADD COLUMN color TEXT")

            # Create new tables
            cursor.executescript(DatabaseSchema.CUE_TABLE)
            cursor.executescript(DatabaseSchema.BEATGRID_TABLE)

            # Update schema version metadata if metadata table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fg_metadata'")
            if cursor.fetchone():
                try:
                    cursor.execute(
                        "INSERT OR REPLACE INTO fg_metadata (key, value, updated_at) VALUES ('schema_version', ?, CURRENT_TIMESTAMP)",
                        (DatabaseSchema.get_schema_version(),)
                    )
                except sqlite3.OperationalError:
                    cursor.execute(
                        "INSERT OR REPLACE INTO fg_metadata (key, value) VALUES ('schema_version', ?)",
                        (DatabaseSchema.get_schema_version(),)
                    )

            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            log.error("Failed to upgrade database schema: %s", exc)
            return False


@dataclass
class DatabaseConfig:
    """Configuration for the FableGear database."""
    db_path: Path
    auto_vacuum_enabled: bool = True
    journal_mode: str = "WAL"  # Write-Ahead Logging for better performance
    cache_size: int = -2000  # SQLite cache size (negative = KiB)
    synchronous: str = "NORMAL"  # Safety vs performance balance
    foreign_keys: bool = True

    def get_pragmas(self) -> dict[str, str | int]:
        """Get SQLite pragmas for configuration."""
        return {
            "journal_mode": self.journal_mode,
            "cache_size": self.cache_size,
            "synchronous": self.synchronous,
            "foreign_keys": "ON" if self.foreign_keys else "OFF",
        }
