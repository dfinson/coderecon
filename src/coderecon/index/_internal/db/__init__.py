"""Database layer for the index."""

from coderecon.index._internal.db.database import BulkWriter, Database
from coderecon.index._internal.db.epoch import EpochManager, EpochStats
from coderecon.index._internal.db.indexes import create_additional_indexes
from coderecon.index._internal.db.integrity import (
    IndexRecovery,
    IntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)
from coderecon.index._internal.db.reconcile import ChangedFile, Reconciler, ReconcileResult

__all__ = [
    "Database",
    "BulkWriter",
    "EpochManager",
    "EpochStats",
    "create_additional_indexes",
    "IndexRecovery",
    "IntegrityChecker",
    "IntegrityIssue",
    "IntegrityReport",
    "Reconciler",
    "ReconcileResult",
    "ChangedFile",
]
