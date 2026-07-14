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

from .database import FableGearDatabase, ContentRecord
from .schema import DatabaseSchema, DatabaseConfig
from .importer import FileImporter
from .exporter import PioneerExporter, PioneerHandshake
from .sync import DatabaseSync
from .undo import TransactionHistory, DatabaseUndoManager

__all__ = [
    "FableGearDatabase",
    "ContentRecord",
    "DatabaseSchema",
    "DatabaseConfig",
    "FileImporter",
    "PioneerExporter",
    "PioneerHandshake",
    "DatabaseSync",
    "TransactionHistory",
    "DatabaseUndoManager",
]
