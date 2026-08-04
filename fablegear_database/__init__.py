"""
fablegear_database — Database-first library management layer.

Provides a database-centric architecture for FableGear that separates
library control (Record Room) from file editing (Chop Shop), enabling:
- Fast duplicate detection via database queries
- Instant library browsing and search
- Pioneer hardware compatibility
- Clean separation of concerns

Note: TransactionHistory and DatabaseUndoManager (from .undo) are
exported here for future use but are not yet called by any production
code path.  Live DB safety is provided by db_connection.open_db and
the fg_processing_log journal.
"""

from .database import ContentRecord, FableGearDatabase
from .exporter import PioneerExporter, PioneerHandshake
from .importer import FileImporter
from .rekordbox_sync import RekordboxSyncAdapter
from .schema import DatabaseConfig, DatabaseSchema
from .sync import DatabaseSync
from .undo import DatabaseUndoManager, TransactionHistory

__all__ = [
    "ContentRecord",
    "DatabaseConfig",
    "DatabaseSchema",
    "DatabaseSync",
    "DatabaseUndoManager",
    "FableGearDatabase",
    "FileImporter",
    "PioneerExporter",
    "PioneerHandshake",
    "RekordboxSyncAdapter",
    "TransactionHistory",
]
