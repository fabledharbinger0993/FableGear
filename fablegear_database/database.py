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
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import DatabaseConfig, DatabaseSchema

log = logging.getLogger(__name__)

# Canonical on-disk location of the FableGear library database. Kept as a
# module constant so callers can test for existence *without* constructing a
# FableGearDatabase (which would create the file as a side effect).
DEFAULT_DB_DIR = Path.home() / ".fablegear"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "fablegear.db"


class LibraryNotInitializedError(RuntimeError):
    """Raised when the FableGear database is opened read-only (create=False)
    but has not been created yet. Read paths catch this and present an empty
    library instead of silently materialising the file — FableGear never
    creates the library as a side effect of merely viewing it."""


@dataclass
class CueRecord:
    """Database record for a cue point or loop in fg_cue table."""
    id: int | None = None
    content_id: int | None = None
    kind: int = 0  # 0 = Memory, 1 = Hotcue, 2 = Loop, 3 = Active Loop
    slot: int | None = None  # 0-7 for hotcues
    in_msec: int = 0
    out_msec: int | None = None
    color: str | None = None
    comment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CueRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class BeatGridRecord:
    """Database record for a beatgrid marker in fg_beatgrid table."""
    id: int | None = None
    content_id: int | None = None
    beat_number: int = 0
    time_msec: int = 0
    bpm: float = 120.0
    meter_numerator: int = 4
    meter_denominator: int = 4
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BeatGridRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class ContentRecord:
    """Database record for a track in fg_content table."""
    id: int | None = None
    file_path: str = ""
    file_name: str = ""
    file_size: int = 0
    duration: float | None = None
    format: str | None = None
    bit_rate: int | None = None
    sample_rate: int | None = None
    modified_date: str | None = None
    file_hash: str | None = None
    acoustic_fingerprint: str | None = None
    artist: str | None = None
    album: str | None = None
    title: str | None = None
    bpm: float | None = None
    key: str | None = None
    genre: str | None = None
    label: str | None = None
    year: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    comment: str | None = None
    rating: int = 0
    drive: str | None = None
    relative_path: str | None = None
    rekordbox_id: int | None = None
    rekordbox_playlist_id: int | None = None
    in_rekordbox: bool = False
    last_scanned: str | None = None
    fingerprint_quality: int = 0
    is_corrupted: bool = False
    processing_status: str = "unprocessed"
    color: str | None = None
    cues: list[CueRecord] = field(default_factory=list)
    beatgrid: list[BeatGridRecord] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database operations."""
        exclude = {"cues", "beatgrid"}
        return {k: v for k, v in self.__dict__.items() if v is not None and k not in exclude}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentRecord":
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

    def __init__(self, config: DatabaseConfig | None = None, *, create: bool = True):
        """
        Initialize the database connection.

        Args:
            config: Database configuration (default: auto-detected)
            create: When True (default) the database file and schema are
                created if they do not exist. Pass ``create=False`` on read
                paths that must not materialise the library — a missing file
                then raises ``LibraryNotInitializedError``.
        """
        self.config = config or self._default_config()
        self._conn: sqlite3.Connection | None = None
        self._in_transaction = False

        # Initialize database if needed
        self._initialize_database(create=create)

    @staticmethod
    def default_db_path() -> Path:
        """Return the canonical library path without any side effects (no
        directory creation, no connection). Use this to check existence
        before deciding whether a read path should open the database."""
        return DEFAULT_DB_PATH

    def _default_config(self) -> DatabaseConfig:
        """Create default database configuration.

        Note: this no longer creates ``~/.fablegear`` — the directory is only
        made when the database is actually created (see _initialize_database),
        so constructing with ``create=False`` stays side-effect-free.
        """
        return DatabaseConfig(
            db_path=DEFAULT_DB_PATH,
            auto_vacuum_enabled=True,
            journal_mode="WAL",
            cache_size=-2000,
            synchronous="NORMAL",
            foreign_keys=True,
        )

    def _initialize_database(self, *, create: bool = True) -> None:
        """Initialize database schema if needed."""
        if not self.config.db_path.exists():
            if not create:
                raise LibraryNotInitializedError(
                    f"FableGear library database does not exist yet: "
                    f"{self.config.db_path}"
                )
            self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
            log.info("Creating new FableGear database: %s", self.config.db_path)
            DatabaseSchema.create_schema(self.config.db_path)
            self._set_metadata("schema_version", DatabaseSchema.get_schema_version())
            self._set_metadata("created_at", datetime.now().isoformat())
        else:
            # Validate existing schema
            errors = DatabaseSchema.validate_schema(self.config.db_path)
            if errors:
                log.info("Attempting automatic database schema upgrade...")
                if DatabaseSchema.upgrade_schema(self.config.db_path):
                    log.info("Database schema upgrade successful.")
                    errors = DatabaseSchema.validate_schema(self.config.db_path)
                    if errors:
                        log.warning("Database schema still has errors after upgrade: %s", errors)
                else:
                    log.warning("Database schema upgrade failed. Validation errors: %s", errors)

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

            # lastrowid is Optional in the stubs; an INSERT always sets it.
            return int(cursor.lastrowid or 0)

    def update_content(self, record_id: int, updates: dict[str, Any]) -> bool:
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
            values = [*list(updates.values()), record_id]

            cursor.execute(
                f"UPDATE fg_content SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values
            )

            return cursor.rowcount > 0

    # Columns written by bulk_upsert_content, in a fixed order. Excludes id
    # (autoincrement) and the created_at/updated_at timestamps (managed by the
    # table defaults and the upsert clause respectively).
    _UPSERT_COLUMNS: tuple[str, ...] = (
        "file_path", "file_name", "file_size", "duration", "format",
        "bit_rate", "sample_rate", "modified_date", "file_hash",
        "acoustic_fingerprint", "artist", "album", "title", "bpm", "key",
        "genre", "label", "year", "track_number", "disc_number", "comment",
        "rating", "drive", "relative_path", "rekordbox_id",
        "rekordbox_playlist_id", "in_rekordbox", "last_scanned",
        "fingerprint_quality", "is_corrupted", "processing_status", "color",
    )

    def bulk_upsert_content(self, records: list[ContentRecord]) -> int:
        """
        Insert or update many content records in a single transaction.

        Existing rows (matched on the unique file_path) are updated in place;
        new rows are inserted. This is the bulk path used by the importer —
        one transaction and one executemany instead of a commit per file.

        Args:
            records: ContentRecords to upsert

        Returns:
            Number of rows written
        """
        if not records:
            return 0

        cols = self._UPSERT_COLUMNS
        col_list = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        update_list = ", ".join(
            f"{c} = excluded.{c}" for c in cols if c != "file_path"
        )
        sql = (
            f"INSERT INTO fg_content ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT(file_path) DO UPDATE SET {update_list}, "
            f"updated_at = datetime('now', 'localtime')"
        )

        rows = [tuple(getattr(rec, col) for col in cols) for rec in records]

        with self.transaction() as conn:
            conn.executemany(sql, rows)

        return len(rows)

    def get_path_index(self) -> dict[str, tuple[int, str | None, str | None]]:
        """
        Return a map of file_path -> (file_size, modified_date, file_hash).

        Used by the importer for fast change detection: it can decide whether
        a file needs re-hashing/re-importing without a query per file.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_path, file_size, modified_date, file_hash FROM fg_content"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}

    def get_fingerprint_index(self) -> dict[str, tuple[str | None, int, float | None]]:
        """
        Return a map of file_path -> (acoustic_fingerprint, file_size, duration).

        Used by the duplicate scanner to reuse fingerprints the Archive already
        knows instead of re-running fpcalc on every file. file_size is the
        cheap staleness check: if the on-disk size differs, recompute.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_path, acoustic_fingerprint, file_size, duration FROM fg_content"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}

    def bulk_set_fingerprints(
        self, entries: list[tuple[str, str, float | None, int]]
    ) -> int:
        """
        Persist computed fingerprints without clobbering other columns.

        entries: (file_path, acoustic_fingerprint, duration, file_size).
        Rows the Archive doesn't know yet are inserted; existing rows keep
        their tags/metadata and only gain the fingerprint (+ duration when
        we learned one). One transaction for the whole batch.
        """
        if not entries:
            return 0
        sql = (
            "INSERT INTO fg_content (file_path, file_name, file_size, duration, acoustic_fingerprint) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET "
            "  acoustic_fingerprint = excluded.acoustic_fingerprint, "
            "  duration = COALESCE(excluded.duration, duration), "
            "  file_size = excluded.file_size, "
            "  updated_at = datetime('now', 'localtime')"
        )
        rows = [
            (path, Path(path).name, file_size, duration, fingerprint)
            for path, fingerprint, duration, file_size in entries
        ]
        with self.transaction() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def bulk_set_analysis(
        self, entries: list[tuple[str, float | None, str | None, int]]
    ) -> int:
        """
        Persist tagger analysis (BPM / musical key) without clobbering other
        columns. None values never overwrite an existing value (COALESCE).

        entries: (file_path, bpm, key, file_size).
        """
        if not entries:
            return 0
        sql = (
            "INSERT INTO fg_content (file_path, file_name, file_size, bpm, key) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET "
            "  bpm = COALESCE(excluded.bpm, bpm), "
            "  key = COALESCE(excluded.key, key), "
            "  file_size = excluded.file_size, "
            "  updated_at = datetime('now', 'localtime')"
        )
        rows = [
            (path, Path(path).name, file_size, bpm, key)
            for path, bpm, key, file_size in entries
        ]
        with self.transaction() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def delete_content(self, record_id: int) -> bool:
        """
        Delete a content record by ID.

        Args:
            record_id: ID of the record to delete

        Returns:
            True if a row was deleted
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fg_content WHERE id = ?", (record_id,))
            return cursor.rowcount > 0

    def relink_content(self, record_id: int, new_path: str) -> bool:
        """
        Re-point an existing record at a new file path (e.g. a moved file).

        Keeps the row — and everything attached to it (playlists, cues,
        fingerprint) — instead of deleting and re-adding, which is what lets a
        moved file keep its place in the library.

        Args:
            record_id: ID of the record to relink
            new_path: New file path

        Returns:
            True if the record was relinked
        """
        return self.update_content(record_id, {
            "file_path": new_path,
            "file_name": Path(new_path).name,
            "processing_status": "relinked",
        })

    def bulk_relink_content(
        self,
        updates: list[tuple[int, str]],
        *,
        chunk_size: int = 500,
    ) -> int:
        """
        Relink many records in bounded chunks to avoid per-row transactions.

        Args:
            updates: List of (record_id, new_path)
            chunk_size: Rows per transaction chunk

        Returns:
            Number of rows relinked
        """
        if not updates:
            return 0
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

        sql = (
            "UPDATE fg_content SET "
            "  file_path = ?, "
            "  file_name = ?, "
            "  processing_status = 'relinked', "
            "  updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?"
        )
        total = 0
        for i in range(0, len(updates), chunk_size):
            chunk = updates[i:i + chunk_size]
            rows = [(new_path, Path(new_path).name, int(record_id)) for record_id, new_path in chunk]
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.executemany(sql, rows)
                total += cursor.rowcount if cursor.rowcount is not None else 0
        return total

    def relink_converted(self, record_id: int, new_path: str) -> bool:
        """Repoint a record at its just-converted file (Rekordbox-style relocate)
        AND refresh the fields the conversion invalidated, so nothing goes stale.

        Retained (format-independent): the row id, tags, cues, playlist
        membership, rating — everything attached to the record.
        Refreshed (byte/audio-derived): file_size + file_hash are recomputed for
        the new file, and acoustic_fingerprint is cleared so it is recomputed
        lazily on the next dedup/tag pass instead of being trusted while stale.
        """
        import hashlib
        import os
        # Format is derived from the new extension — the conversion's whole point
        # is a format change, so leaving the old `format` would make every
        # downstream consumer (e.g. the OneLibrary fileType code) mislabel the
        # file. aif → aiff is normalised to match the rest of the codebase.
        new_fmt = Path(new_path).suffix.lstrip(".").lower()
        if new_fmt == "aif":
            new_fmt = "aiff"
        updates: dict[str, Any] = {
            "file_path": new_path,
            "file_name": Path(new_path).name,
            "format": new_fmt,
            "processing_status": "relinked",
            "acoustic_fingerprint": None,   # audio re-encoded → recompute later
            "fingerprint_quality": 0,
        }
        try:
            updates["file_size"] = os.path.getsize(new_path)
        except OSError:
            pass
        try:
            h = hashlib.sha256()
            with open(new_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            updates["file_hash"] = h.hexdigest()
        except OSError as exc:
            log.warning("Could not re-hash converted file %s: %s", new_path, exc)
        return self.update_content(record_id, updates)

    def get_paths_for_ids(self, ids) -> dict[int, str]:
        """Batch-resolve content ids to file paths in one query (chunked under
        SQLite's variable limit). Lets the fast hash-based duplicate scan avoid
        one query per id."""
        id_list = []
        for i in ids:
            try:
                id_list.append(int(i))
            except (TypeError, ValueError):
                continue
        if not id_list:
            return {}
        out: dict[int, str] = {}
        with self.connection() as conn:
            cur = conn.cursor()
            for start in range(0, len(id_list), 900):
                chunk = id_list[start:start + 900]
                placeholders = ",".join("?" * len(chunk))
                cur.execute(
                    f"SELECT id, file_path FROM fg_content WHERE id IN ({placeholders})",
                    chunk,
                )
                for rid, fp in cur.fetchall():
                    out[rid] = fp
        return out

    def get_content_by_path(self, file_path: str) -> ContentRecord | None:
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

    def get_content_by_id(self, record_id: int) -> ContentRecord | None:
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

    # ── Playlists ──────────────────────────────────────────────────────────
    # FableGear-native playlists live alongside the library in fg_playlist /
    # fg_playlist_song and reference fg_content by its own id — so a user whose
    # music lives in the FableGear Archive (not Rekordbox) can build playlists
    # from their actual tracks. These are the source of truth the Record Room
    # uses by default; Rekordbox playlists remain reachable as a demoted source.

    def list_playlists(self) -> list[dict]:
        """Return the playlist/folder tree as nested dicts (Record Room shape)."""
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT p.id, p.name, p.playlist_type, p.parent_id, "
                "(SELECT COUNT(*) FROM fg_playlist_song s WHERE s.playlist_id = p.id) "
                "FROM fg_playlist p "
                "ORDER BY (p.playlist_type = 'folder') DESC, p.name COLLATE NOCASE"
            )
            rows = cur.fetchall()
        nodes = {}
        for pid, name, ptype, parent, tcount in rows:
            nodes[pid] = {
                "id": str(pid),
                "name": name or "",
                "type": "folder" if ptype == "folder" else "playlist",
                "parent_id": parent,
                "track_count": tcount,
                "children": [],
            }
        roots = []
        for _pid, node in nodes.items():
            parent = node["parent_id"]
            if parent is not None and parent in nodes:
                nodes[parent]["children"].append(node)
            else:
                roots.append(node)

        def _clean(node):
            node.pop("parent_id", None)
            if node["type"] == "folder":
                node["children"] = [_clean(c) for c in node["children"]]
            else:
                node.pop("children", None)
            return node

        return [_clean(n) for n in roots]

    def get_playlist(self, playlist_id) -> dict | None:
        """Return one playlist/folder as a dict, or None if it doesn't exist."""
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, playlist_type, parent_id FROM fg_playlist WHERE id = ?",
                (int(playlist_id),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "name": row[1] or "",
            "type": "folder" if row[2] == "folder" else "playlist",
            "parent_id": row[3],
        }

    def create_playlist(self, name: str, parent_id=None,
                        playlist_type: str = "playlist") -> int:
        """Create a playlist or folder; returns the new id."""
        ptype = "folder" if playlist_type == "folder" else "playlist"
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO fg_playlist (name, playlist_type, parent_id) VALUES (?, ?, ?)",
                (name, ptype, int(parent_id) if parent_id else None),
            )
            # lastrowid is Optional in the stubs; an INSERT always sets it.
            return int(cur.lastrowid or 0)

    def rename_playlist(self, playlist_id, name: str) -> bool:
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE fg_playlist SET name = ?, updated_at = datetime('now','localtime') "
                "WHERE id = ?",
                (name, int(playlist_id)),
            )
            return cur.rowcount > 0

    def delete_playlist(self, playlist_id) -> bool:
        """Delete a playlist/folder and its song rows (children reparent to root)."""
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM fg_playlist_song WHERE playlist_id = ?", (int(playlist_id),))
            cur.execute("UPDATE fg_playlist SET parent_id = NULL WHERE parent_id = ?", (int(playlist_id),))
            cur.execute("DELETE FROM fg_playlist WHERE id = ?", (int(playlist_id),))
            return cur.rowcount > 0

    def get_playlist_songs(self, playlist_id) -> list[ContentRecord]:
        """Return the playlist's tracks as ContentRecords in playlist order."""
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT c.* FROM fg_playlist_song s JOIN fg_content c ON c.id = s.content_id "
                "WHERE s.playlist_id = ? ORDER BY s.track_number, s.id",
                (int(playlist_id),),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
        return [ContentRecord.from_dict(dict(zip(columns, row))) for row in rows]

    def add_song(self, playlist_id, content_id) -> bool:
        """Append a track if not already present. Returns True when added.

        Raises LookupError if the content id isn't in the library.
        """
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM fg_playlist_song WHERE playlist_id = ? AND content_id = ?",
                (int(playlist_id), int(content_id)),
            )
            if cur.fetchone():
                return False
            cur.execute("SELECT 1 FROM fg_content WHERE id = ?", (int(content_id),))
            if not cur.fetchone():
                raise LookupError(f"content {content_id} not in library")
            cur.execute(
                "SELECT COALESCE(MAX(track_number), 0) + 1 FROM fg_playlist_song "
                "WHERE playlist_id = ?",
                (int(playlist_id),),
            )
            next_no = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO fg_playlist_song (playlist_id, content_id, track_number) "
                "VALUES (?, ?, ?)",
                (int(playlist_id), int(content_id), next_no),
            )
            self._refresh_playlist_count(cur, playlist_id)
            return True

    def remove_song(self, playlist_id, content_id) -> int:
        with self.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM fg_playlist_song WHERE playlist_id = ? AND content_id = ?",
                (int(playlist_id), int(content_id)),
            )
            removed = cur.rowcount
            self._refresh_playlist_count(cur, playlist_id)
            return removed

    def reorder_playlist(self, playlist_id, ordered_content_ids: list) -> int:
        """Set track_number from the given content-id order. Returns rows touched."""
        with self.transaction() as conn:
            cur = conn.cursor()
            touched = 0
            for pos, cid in enumerate(ordered_content_ids, start=1):
                cur.execute(
                    "UPDATE fg_playlist_song SET track_number = ? "
                    "WHERE playlist_id = ? AND content_id = ?",
                    (pos, int(playlist_id), int(cid)),
                )
                touched += cur.rowcount
            return touched

    @staticmethod
    def _refresh_playlist_count(cur, playlist_id) -> None:
        cur.execute(
            "UPDATE fg_playlist SET "
            "track_count = (SELECT COUNT(*) FROM fg_playlist_song WHERE playlist_id = ?), "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (int(playlist_id), int(playlist_id)),
        )

    def find_duplicates_by_hash(self) -> list[tuple[str, list[int]]]:
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

    def find_duplicates_by_fingerprint(self) -> list[tuple[str, list[int]]]:
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

    def get_unfingerprinted(self, limit: int = 1_000_000) -> list[ContentRecord]:
        """
        Return content records that have no acoustic fingerprint yet.

        This is the work-list for the fingerprinter and the resume mechanism:
        a fingerprint already stored is never recomputed, so an interrupted
        run simply continues with whatever is still missing.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM fg_content "
                "WHERE acoustic_fingerprint IS NULL OR acoustic_fingerprint = '' "
                "ORDER BY id LIMIT ?",
                (limit,),
            )
            columns = [desc[0] for desc in cursor.description]
            return [
                ContentRecord.from_dict(dict(zip(columns, row)))
                for row in cursor.fetchall()
            ]

    def log_operation(
        self,
        operation_type: str,
        file_path: str | None = None,
        status: str = "ok",
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """
        Append a row to the fg_processing_log — the Archive's audit trail.

        Every tool action (analysis or mutation) should be logged here so the
        history of what touched a file is durable and queryable. Returns the
        log row id.
        """
        import json

        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO fg_processing_log "
                "(operation_type, file_path, status, error_message, "
                " completed_at, metadata) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
                (
                    operation_type,
                    file_path,
                    status,
                    error_message,
                    json.dumps(metadata) if metadata is not None else None,
                ),
            )
            # lastrowid is Optional in the stubs; an INSERT always sets it.
            return int(cursor.lastrowid or 0)

    def bulk_log_operations(
        self,
        operations: list[tuple[str, str | None, str, str | None, dict[str, Any] | None]] | list[dict[str, Any]],
        chunk_size: int = 500,
    ) -> int:
        """
        Insert multiple audit-log rows in bounded chunks.

        Each operation may be either a tuple
        ``(operation_type, file_path, status, error_message, metadata)``
        or a dict with keys accepted by :meth:`log_operation`.

        Args:
            operations: List of operation tuples or dicts.
            chunk_size: Maximum rows per executemany call.

        Returns:
            Total number of rows inserted.
        """
        if not operations:
            return 0
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

        import json

        total = 0
        for start in range(0, len(operations), chunk_size):
            chunk = operations[start : start + chunk_size]
            rows = []
            for op in chunk:
                if isinstance(op, dict):
                    rows.append(
                        (
                            op["operation_type"],
                            op.get("file_path"),
                            op.get("status", "ok"),
                            op.get("error_message"),
                            json.dumps(op["metadata"]) if op.get("metadata") is not None else None,
                        )
                    )
                else:
                    op_type, file_path, status, error_message, metadata = op
                    rows.append(
                        (
                            op_type,
                            file_path,
                            status,
                            error_message,
                            json.dumps(metadata) if metadata is not None else None,
                        )
                    )
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.executemany(
                    "INSERT INTO fg_processing_log "
                    "(operation_type, file_path, status, error_message, "
                    " completed_at, metadata) "
                    "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
                    rows,
                )
                total += len(chunk)
        return total

    def count_operations(self, operation_type: str | None = None) -> int:
        """Count rows in the processing log, optionally by operation type."""
        with self.connection() as conn:
            cursor = conn.cursor()
            if operation_type is None:
                cursor.execute("SELECT COUNT(*) FROM fg_processing_log")
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM fg_processing_log WHERE operation_type = ?",
                    (operation_type,),
                )
            return cursor.fetchone()[0]

    def search_content(
        self,
        query: str,
        fields: list[str] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[ContentRecord]:
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
    ) -> list[ContentRecord]:
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

    def get_statistics(self) -> dict[str, Any]:
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

        # Microsecond resolution so two backups in the same second (e.g. the
        # pre-restore snapshot taken inside restore_backup) never collide and
        # clobber each other.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"fablegear_backup_{timestamp}.db"

        # Use SQLite's online backup API rather than copying the file directly:
        # in WAL mode the latest writes may still live in the -wal sidecar, and
        # a raw file copy would miss them. backup() produces a consistent copy.
        src = sqlite3.connect(self.config.db_path)
        try:
            dest = sqlite3.connect(backup_path)
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()

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

            # Drop any stale WAL/SHM sidecars from the previous database so
            # they are not replayed on top of the freshly restored file.
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.config.db_path) + suffix)
                if sidecar.exists():
                    sidecar.unlink()

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

    def _get_metadata(self, key: str) -> str | None:
        """Get metadata value."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM fg_metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    # ── Performance Metadata APIs (Cues, Loops, Beatgrids) ────────────────

    def get_cues_for_content(self, content_id: int) -> list[CueRecord]:
        """Get all cues and loops for a content record, sorted by position."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, content_id, kind, slot, in_msec, out_msec, color, comment, created_at, updated_at
                FROM fg_cue
                WHERE content_id = ?
                ORDER BY in_msec ASC
            """, (content_id,))
            columns = [col[0] for col in cursor.description]
            return [CueRecord.from_dict(dict(zip(columns, row))) for row in cursor.fetchall()]

    def get_beatgrid_for_content(self, content_id: int) -> list[BeatGridRecord]:
        """Get beatgrid markers for a content record, sorted by beat number."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, content_id, beat_number, time_msec, bpm, meter_numerator, meter_denominator, created_at, updated_at
                FROM fg_beatgrid
                WHERE content_id = ?
                ORDER BY beat_number ASC
            """, (content_id,))
            columns = [col[0] for col in cursor.description]
            return [BeatGridRecord.from_dict(dict(zip(columns, row))) for row in cursor.fetchall()]

    def bulk_upsert_cues(self, content_id: int, cues: list[CueRecord]) -> None:
        """Replace all cues and loops for a content record in a single transaction."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            # Clear existing cues
            cursor.execute("DELETE FROM fg_cue WHERE content_id = ?", (content_id,))

            # Insert new cues
            for cue in cues:
                cursor.execute("""
                    INSERT INTO fg_cue (content_id, kind, slot, in_msec, out_msec, color, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    content_id,
                    cue.kind,
                    cue.slot,
                    cue.in_msec,
                    cue.out_msec,
                    cue.color,
                    cue.comment
                ))

    def bulk_upsert_beatgrids(self, content_id: int, beatgrid: list[BeatGridRecord]) -> None:
        """Replace all beatgrid markers for a content record in a single transaction."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            # Clear existing beatgrid
            cursor.execute("DELETE FROM fg_beatgrid WHERE content_id = ?", (content_id,))

            # Insert new beatgrid
            for grid in beatgrid:
                cursor.execute("""
                    INSERT INTO fg_beatgrid (content_id, beat_number, time_msec, bpm, meter_numerator, meter_denominator)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    content_id,
                    grid.beat_number,
                    grid.time_msec,
                    grid.bpm,
                    grid.meter_numerator,
                    grid.meter_denominator
                ))

    def get_content_with_relations(self, content_ids: list[int] | None = None) -> list[ContentRecord]:
        """
        Fetch multiple ContentRecords along with all their related cues and beatgrids.
        IDs are processed in chunks of 500 to stay within SQLite's host-parameter
        limit; each chunk issues 1 content query plus 1 cue query and 1 beatgrid
        query, so the total number of queries scales with ceil(N / 500) rather than
        being a fixed count. This still prevents N+1 roundtrips.
        If content_ids is None, fetches all records in a single pass.
        """
        # Chunking to avoid SQLite host parameter limits — applies to every
        # IN (...) here, including the fg_content fetch itself: an explicit
        # content_ids list can exceed the limit just as easily as the
        # relation lookups can.
        CHUNK_SIZE = 500

        # 1. Fetch content records
        with self.connection() as conn:
            cursor = conn.cursor()

            if content_ids is not None:
                if not content_ids:
                    return []
                tracks = []
                columns = None
                for i in range(0, len(content_ids), CHUNK_SIZE):
                    chunk = content_ids[i:i + CHUNK_SIZE]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor.execute(f"""
                        SELECT * FROM fg_content
                        WHERE id IN ({placeholders})
                    """, chunk)
                    columns = [col[0] for col in cursor.description]
                    tracks.extend(
                        ContentRecord.from_dict(dict(zip(columns, row)))
                        for row in cursor.fetchall()
                    )
            else:
                cursor.execute("SELECT * FROM fg_content")
                columns = [col[0] for col in cursor.description]
                tracks = [ContentRecord.from_dict(dict(zip(columns, row))) for row in cursor.fetchall()]

            if not tracks:
                return []

            track_map = {t.id: t for t in tracks}
            actual_ids = list(track_map.keys())
            for i in range(0, len(actual_ids), CHUNK_SIZE):
                chunk = actual_ids[i:i + CHUNK_SIZE]
                placeholders_chunk = ",".join("?" for _ in chunk)

                # 2. Fetch cues for this chunk
                cursor.execute(f"""
                    SELECT id, content_id, kind, slot, in_msec, out_msec, color, comment, created_at, updated_at
                    FROM fg_cue
                    WHERE content_id IN ({placeholders_chunk})
                    ORDER BY in_msec ASC
                """, chunk)
                cue_cols = [col[0] for col in cursor.description]
                for row in cursor.fetchall():
                    cue = CueRecord.from_dict(dict(zip(cue_cols, row)))
                    if cue.content_id in track_map:
                        track_map[cue.content_id].cues.append(cue)

                # 3. Fetch beatgrid markers for this chunk
                cursor.execute(f"""
                    SELECT id, content_id, beat_number, time_msec, bpm, meter_numerator, meter_denominator, created_at, updated_at
                    FROM fg_beatgrid
                    WHERE content_id IN ({placeholders_chunk})
                    ORDER BY beat_number ASC
                """, chunk)
                grid_cols = [col[0] for col in cursor.description]
                for row in cursor.fetchall():
                    grid = BeatGridRecord.from_dict(dict(zip(grid_cols, row)))
                    if grid.content_id in track_map:
                        track_map[grid.content_id].beatgrid.append(grid)

            return tracks
