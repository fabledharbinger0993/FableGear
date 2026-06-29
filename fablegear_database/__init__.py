"""
fablegear_database — Database-first library management layer.

Provides a database-centric architecture for FableGear that separates
library control (Record Room) from file editing (Chop Shop), enabling:
- Fast duplicate detection via database queries
- Instant library browsing and search
- Database-level checkpoints and undos
- Pioneer hardware compatibility
- Clean separation of concerns
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
