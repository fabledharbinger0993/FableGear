"""
fablegear_database.undo — Database-level transaction history.

Provides transaction history and rollback capabilities for database
operations.  The classes here are well-tested infrastructure but are
NOT currently called by any production tool, route, or CLI command —
they are tested in tests/test_snapshot_storage.py and exported from
fablegear_database/__init__.py for future use.

If you are reading this module expecting it to protect ongoing DB
operations: it does not.  Live DB safety is provided by
db_connection.open_db(write=True) (full-snapshot backup before every
write session) and the fg_processing_log journal (routes_undo.py).
"""

import logging
import json
import gzip
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

try:
    from fablegear_database import FableGearDatabase, ContentRecord
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

log = logging.getLogger(__name__)


@dataclass
class TransactionRecord:
    """Record of a database transaction."""
    transaction_id: str
    operation_type: str
    timestamp: str
    description: str
    user: str = "system"
    affected_records: List[int] = field(default_factory=list)
    before_state: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    after_state: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "transaction_id": self.transaction_id,
            "operation_type": self.operation_type,
            "timestamp": self.timestamp,
            "description": self.description,
            "user": self.user,
            "affected_records": self.affected_records,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransactionRecord":
        """Create TransactionRecord from dictionary.

        JSON round-trips coerce the int record-id keys of before_state /
        after_state to strings; normalize them back so undo_transaction's
        `record_id in before_state` lookups (int keys) still match after a
        reload from disk.
        """
        data = dict(data)
        for state_key in ("before_state", "after_state"):
            state = data.get(state_key)
            if isinstance(state, dict):
                data[state_key] = {int(k): v for k, v in state.items()}
        return cls(**data)


class TransactionHistory:
    """
    Manages transaction history for database operations.
    
    Enables undo functionality by tracking all database changes
    and providing rollback capabilities.
    """
    
    def __init__(self, database: FableGearDatabase, max_history: int = 100):
        """
        Initialize transaction history manager.
        
        Args:
            database: FableGear database instance
            max_history: Maximum number of transactions to keep
        """
        self.database = database
        self.max_history = max_history
        self._history_file = Path.home() / ".fablegear" / "transaction_history.json.gz"
        self._legacy_history_file = Path.home() / ".fablegear" / "transaction_history.json"
        self._transactions: List[TransactionRecord] = []
        
        self._load_history()
    
    def _load_history(self) -> None:
        """Load transaction history from file.

        Tries the compressed file first, then the legacy .json — a corrupt
        .gz must not wipe the rollback history when an intact legacy file
        is still on disk.
        """
        self._transactions = []
        for path in (self._history_file, self._legacy_history_file):
            if not path.exists():
                continue
            try:
                if path.suffix == ".gz":
                    with gzip.open(path, "rt", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    with open(path, "r") as f:
                        data = json.load(f)

                self._transactions = [
                    TransactionRecord.from_dict(item) for item in data
                ]

                # Trim to max history
                if len(self._transactions) > self.max_history:
                    self._transactions = self._transactions[-self.max_history:]

                log.info("Loaded %d transactions from history", len(self._transactions))
                return

            except Exception as exc:
                log.error("Failed to load transaction history from %s: %s", path, exc)
                self._transactions = []
    
    def _save_history(self) -> None:
        """Save transaction history to file."""
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = [t.to_dict() for t in self._transactions]
            # Atomic write: this file is the rollback record store, and once
            # the legacy .json is gone a torn write would leave no fallback.
            tmp = self._history_file.with_name(self._history_file.name + ".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self._history_file)
            self._legacy_history_file.unlink(missing_ok=True)
            
        except Exception as exc:
            log.error("Failed to save transaction history: %s", exc)
    
    def record_transaction(
        self,
        operation_type: str,
        description: str,
        affected_records: List[int],
        before_state: Dict[int, Dict[str, Any]],
        after_state: Dict[int, Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Record a database transaction.
        
        Args:
            operation_type: Type of operation (e.g., "import", "update", "delete")
            description: Human-readable description
            affected_records: List of record IDs affected
            before_state: State before operation
            after_state: State after operation
            metadata: Additional metadata
            
        Returns:
            Transaction ID
        """
        transaction_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        transaction = TransactionRecord(
            transaction_id=transaction_id,
            operation_type=operation_type,
            timestamp=datetime.now().isoformat(),
            description=description,
            affected_records=affected_records,
            before_state=before_state,
            after_state=after_state,
            metadata=metadata or {},
        )
        
        self._transactions.append(transaction)
        
        # Trim to max history
        if len(self._transactions) > self.max_history:
            self._transactions = self._transactions[-self.max_history:]
        
        self._save_history()
        
        log.info("Recorded transaction %s: %s", transaction_id, description)
        return transaction_id
    
    def undo_transaction(self, transaction_id: str) -> bool:
        """
        Undo a specific transaction.
        
        Args:
            transaction_id: ID of transaction to undo
            
        Returns:
            True if undo succeeded
        """
        transaction = self._get_transaction(transaction_id)
        if not transaction:
            log.error("Transaction not found: %s", transaction_id)
            return False
        
        try:
            # Restore before state for each affected record
            restored = 0
            for record_id in transaction.affected_records:
                if record_id in transaction.before_state:
                    before_state = transaction.before_state[record_id]
                    self.database.update_content(record_id, before_state)
                    restored += 1
                else:
                    log.warning(
                        "Undo %s: no before-state for record %s — skipped",
                        transaction_id, record_id,
                    )

            if transaction.affected_records and restored == 0:
                # Nothing actually restored — reporting success here would
                # tell the user their rollback worked when it did not.
                log.error(
                    "Undo %s restored 0 of %d records; refusing to report success",
                    transaction_id, len(transaction.affected_records),
                )
                return False

            log.info(
                "Undid transaction %s (%d/%d records): %s",
                transaction_id, restored, len(transaction.affected_records),
                transaction.description,
            )
            return True

        except Exception as exc:
            log.error("Failed to undo transaction %s: %s", transaction_id, exc)
            return False
    
    def get_transaction(self, transaction_id: str) -> Optional[TransactionRecord]:
        """
        Get a specific transaction.
        
        Args:
            transaction_id: ID of transaction
            
        Returns:
            TransactionRecord or None if not found
        """
        return self._get_transaction(transaction_id)
    
    def _get_transaction(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Get transaction by ID."""
        for transaction in reversed(self._transactions):
            if transaction.transaction_id == transaction_id:
                return transaction
        return None
    
    def get_recent_transactions(self, limit: int = 10) -> List[TransactionRecord]:
        """
        Get recent transactions.
        
        Args:
            limit: Maximum number of transactions to return
            
        Returns:
            List of recent transactions
        """
        return self._transactions[-limit:]
    
    def clear_history(self) -> None:
        """Clear all transaction history."""
        self._transactions = []
        self._save_history()
        log.info("Cleared transaction history")


class DatabaseUndoManager:
    """
    High-level undo manager for database operations.
    
    Provides simple API for recording and undoing database changes.
    """
    
    def __init__(self, database: FableGearDatabase):
        """
        Initialize undo manager.
        
        Args:
            database: FableGear database instance
        """
        self.database = database
        self.history = TransactionHistory(database)
    
    def record_import(
        self,
        imported_count: int,
        root_paths: List[Path],
    ) -> str:
        """
        Record an import operation.
        
        Args:
            imported_count: Number of files imported
            root_paths: Paths that were imported
            
        Returns:
            Transaction ID
        """
        return self.history.record_transaction(
            operation_type="import",
            description=f"Imported {imported_count} files from {len(root_paths)} drives",
            affected_records=[],
            before_state={},
            after_state={},
            metadata={
                "imported_count": imported_count,
                "root_paths": [str(p) for p in root_paths],
            }
        )
    
    def record_update(
        self,
        record_id: int,
        before_state: Dict[str, Any],
        after_state: Dict[str, Any],
        description: str,
    ) -> str:
        """
        Record a record update operation.
        
        Args:
            record_id: ID of updated record
            before_state: State before update
            after_state: State after update
            description: Description of update
            
        Returns:
            Transaction ID
        """
        return self.history.record_transaction(
            operation_type="update",
            description=description,
            affected_records=[record_id],
            before_state={record_id: before_state},
            after_state={record_id: after_state},
        )
    
    def undo_last(self) -> bool:
        """
        Undo the last transaction.
        
        Returns:
            True if undo succeeded
        """
        if not self.history._transactions:
            log.warning("No transactions to undo")
            return False
        
        last_transaction = self.history._transactions[-1]
        return self.history.undo_transaction(last_transaction.transaction_id)
    
    def can_undo(self) -> bool:
        """Check if there are transactions to undo."""
        return len(self.history._transactions) > 0
    
    def get_undo_count(self) -> int:
        """Get number of available undo operations."""
        return len(self.history._transactions)