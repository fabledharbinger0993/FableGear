"""
fablegear_database.database — Database connection and operations layer.

Provides the core database API for FableGear, implementing
database-first architecture with:
- Fast duplicate detection via database queries
- Instant library browsing and search
- Database-level checkpoints and undos
- Safe transaction management
- Pioneer-compatible data structures
"""

import logging
import sqlite3
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from .schema import DatabaseSchema, DatabaseConfig

log = logging.getLogger(__name__)


@dataclass
class ContentRecord:
    """Database record for a track in fg_content table."""
    id: Optional[int] = None
    file_path: str = ""
    file_name: str = ""
    file_size: int = 0
    duration: Optional[float] = None
    format: Optional[str] = None
    bit_rate: Optional[int] = None
    sample_rate: Optional[int] = None
    modified_date: Optional[str] = None
    file_hash: Optional[str] = None
    acoustic_fingerprint: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    title: Optional[str] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    genre: Optional[str] = None
    label: Optional[str] = None
    year: Optional[int] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    comment: Optional[str] = None
    rating: int = 0
    drive: Optional[str] = None
    relative_path: Optional[str] = None
    rekordbox_id: Optional[int] = None
    rekordbox_playlist_id: Optional[int] = None
    in_rekordbox: bool = False
    last_scanned: Optional[str] = None
    fingerprint_quality: int = 0
    is_corrupted: bool = False
    processing_status: str = "unprocessed"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database operations."""
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContentRecord":
        """Create ContentRecord from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


class FableGearDatabase:
    """
    Main database interface for FableGear.
    
    Provides high-level database operations with safety features:
    - Transaction management
    - Automatic backups
    - Duplicate detection
    - Fast queries via indexed fields
    """
    
    def __init__(self, config: Optional[DatabaseConfig] = None):
        """
        Initialize the database connection.
        
        Args:
            config: Database configuration (default: auto-detected)
        """
        self.config = config or self._default_config()
        self._conn: Optional[sqlite3.Connection] = None
        self._in_transaction = False
        
        # Initialize database if needed
        self._initialize_database()
    
    def _default_config(self) -> DatabaseConfig:
        """Create default database configuration."""
        db_dir = Path.home() / ".fablegear"
        db_dir.mkdir(parents=True, exist_ok=True)
        
        return DatabaseConfig(
            db_path=db_dir / "fablegear.db",
            auto_vacuum_enabled=True,
            journal_mode="WAL",
            cache_size=-2000,
            synchronous="NORMAL",
            foreign_keys=True,
        )
    
    def _initialize_database(self) -> None:
        """Initialize database schema if needed."""
        if not self.config.db_path.exists():
            log.info("Creating new FableGear database: %s", self.config.db_path)
            DatabaseSchema.create_schema(self.config.db_path)
            self._set_metadata("schema_version", DatabaseSchema.get_schema_version())
            self._set_metadata("created_at", datetime.now().isoformat())
        else:
            # Validate existing schema
            errors = DatabaseSchema.validate_schema(self.config.db_path)
            if errors:
                log.warning("Database schema validation errors: %s", errors)
    
    @contextmanager
    def connection(self):
        """
        Context manager for database connection.
        
        Yields a connection with proper configuration applied.
        """
        conn = sqlite3.connect(self.config.db_path)
        
        # Apply configuration pragmas
        for pragma, value in self.config.get_pragmas().items():
            conn.execute(f"PRAGMA {pragma} = {value}")
        
        try:
            yield conn
        finally:
            conn.close()
    
    @contextmanager
    def transaction(self):
        """
        Context manager for database transactions.
        
        Automatically commits on success, rolls back on failure.
        """
        with self.connection() as conn:
            try:
                self._in_transaction = True
                yield conn
                conn.commit()
                self._in_transaction = False
            except Exception as exc:
                conn.rollback()
                self._in_transaction = False
                log.error("Transaction failed, rolled back: %s", exc)
                raise
    
    def insert_content(self, record: ContentRecord) -> int:
        """
        Insert a content record into the database.
        
        Args:
            record: ContentRecord to insert
            
        Returns:
            ID of the inserted record
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            
            data = record.to_dict()
            columns = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            
            cursor.execute(
                f"INSERT INTO fg_content ({columns}) VALUES ({placeholders})",
                list(data.values())
            )
            
            return cursor.lastrowid
    
    def update_content(self, record_id: int, updates: Dict[str, Any]) -> bool:
        """
        Update a content record.
        
        Args:
            record_id: ID of the record to update
            updates: Dictionary of field:value pairs to update
            
        Returns:
            True if update succeeded
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [record_id]
            
            cursor.execute(
                f"UPDATE fg_content SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values
            )
            
            return cursor.rowcount > 0
    
    def get_content_by_path(self, file_path: str) -> Optional[ContentRecord]:
        """
        Get a content record by file path.
        
        Args:
            file_path: File path to search for
            
        Returns:
            ContentRecord or None if not found
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM fg_content WHERE file_path = ?",
                (file_path,)
            )
            row = cursor.fetchone()
            
            if row:
                columns = [desc[0] for desc in cursor.description]
                return ContentRecord.from_dict(dict(zip(columns, row)))
            return None
    
    def get_content_by_id(self, record_id: int) -> Optional[ContentRecord]:
        """
        Get a content record by ID.
        
        Args:
            record_id: ID of the record
            
        Returns:
            ContentRecord or None if not found
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM fg_content WHERE id = ?",
                (record_id,)
            )
            row = cursor.fetchone()
            
            if row:
                columns = [desc[0] for desc in cursor.description]
                return ContentRecord.from_dict(dict(zip(columns, row)))
            return None
    
    def find_duplicates_by_hash(self) -> List[Tuple[str, List[int]]]:
        """
        Find duplicate files by file hash (fast).
        
        Returns:
            List of (hash, [record_ids]) tuples for duplicates
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT file_hash, GROUP_CONCAT(id) as ids
                FROM fg_content
                WHERE file_hash IS NOT NULL
                GROUP BY file_hash
                HAVING COUNT(*) > 1
            """)
            
            results = []
            for row in cursor.fetchall():
                file_hash, ids_str = row
                record_ids = [int(id_str) for id_str in ids_str.split(",")]
                results.append((file_hash, record_ids))
            
            return results
    
    def find_duplicates_by_fingerprint(self) -> List[Tuple[str, List[int]]]:
        """
        Find duplicate files by acoustic fingerprint (medium speed).
        
        Returns:
            List of (fingerprint, [record_ids]) tuples for duplicates
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT acoustic_fingerprint, GROUP_CONCAT(id) as ids
                FROM fg_content
                WHERE acoustic_fingerprint IS NOT NULL
                GROUP BY acoustic_fingerprint
                HAVING COUNT(*) > 1
            """)
            
            results = []
            for row in cursor.fetchall():
                fingerprint, ids_str = row
                record_ids = [int(id_str) for id_str in ids_str.split(",")]
                results.append((fingerprint, record_ids))
            
            return results
    
    def search_content(
        self,
        query: str,
        fields: Optional[List[str]] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[ContentRecord]:
        """
        Search content records by query string.
        
        Args:
            query: Search query
            fields: Fields to search in (None = all text fields)
            limit: Maximum results
            offset: Result offset
            
        Returns:
            List of matching ContentRecords
        """
        if not fields:
            fields = ["title", "artist", "album", "file_name"]
        
        where_clauses = [f"{field} LIKE ?" for field in fields]
        where_sql = " OR ".join(where_clauses)
        like_pattern = f"%{query}%"
        
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM fg_content WHERE {where_sql} LIMIT ? OFFSET ?",
                [like_pattern] * len(fields) + [limit, offset]
            )
            
            results = []
            columns = [desc[0] for desc in cursor.description]
            for row in cursor.fetchall():
                results.append(ContentRecord.from_dict(dict(zip(columns, row))))
            
            return results
    
    def get_all_content(
        self,
        limit: int = 1000,
        offset: int = 0,
        order_by: str = "id",
        ascending: bool = True,
    ) -> List[ContentRecord]:
        """
        Get all content records with pagination.
        
        Args:
            limit: Maximum results
            offset: Result offset
            order_by: Field to order by
            ascending: Sort direction
            
        Returns:
            List of ContentRecords
        """
        direction = "ASC" if ascending else "DESC"
        
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM fg_content ORDER BY {order_by} {direction} LIMIT ? OFFSET ?",
                (limit, offset)
            )
            
            results = []
            columns = [desc[0] for desc in cursor.description]
            for row in cursor.fetchall():
                results.append(ContentRecord.from_dict(dict(zip(columns, row))))
            
            return results
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get database statistics.
        
        Returns:
            Dictionary with database statistics
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            
            # Get counts
            cursor.execute("SELECT COUNT(*) FROM fg_content")
            total_tracks = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM fg_content WHERE in_rekordbox = 1")
            in_rekordbox = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM fg_content WHERE is_corrupted = 1")
            corrupted = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM fg_content WHERE acoustic_fingerprint IS NOT NULL")
            fingerprinted = cursor.fetchone()[0]
            
            # Get format distribution
            cursor.execute("SELECT format, COUNT(*) FROM fg_content GROUP BY format")
            format_counts = {row[0]: row[1] for row in cursor.fetchall()}
            
            return {
                "total_tracks": total_tracks,
                "in_rekordbox": in_rekordbox,
                "not_in_rekordbox": total_tracks - in_rekordbox,
                "corrupted": corrupted,
                "fingerprinted": fingerprinted,
                "format_counts": format_counts,
            }
    
    def create_backup(self) -> Path:
        """
        Create a timestamped backup of the database.
        
        Returns:
            Path to the backup file
        """
        backup_dir = self.config.db_path.parent / "database_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"fablegear_backup_{timestamp}.db"
        
        shutil.copy2(self.config.db_path, backup_path)
        log.info("Database backup created: %s", backup_path)
        
        return backup_path
    
    def restore_backup(self, backup_path: Path) -> bool:
        """
        Restore database from backup.
        
        Args:
            backup_path: Path to backup file
            
        Returns:
            True if restore succeeded
        """
        if not backup_path.exists():
            log.error("Backup file not found: %s", backup_path)
            return False
        
        try:
            # Create backup of current state before restore
            current_backup = self.create_backup()
            
            # Restore from backup
            shutil.copy2(backup_path, self.config.db_path)
            log.info("Database restored from: %s", backup_path)
            log.info("Previous state backed up to: %s", current_backup)
            
            return True
            
        except Exception as exc:
            log.error("Failed to restore database: %s", exc)
            return False
    
    def _set_metadata(self, key: str, value: str) -> None:
        """Set metadata value."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO fg_metadata (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, value))
    
    def _get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM fg_metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None