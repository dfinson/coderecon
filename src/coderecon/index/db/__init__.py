"""Database layer for the index."""

from coderecon.index.db.consistency import (
    ConsistencyReport,
    SignalGap,
    backfill_gaps,
    check_consistency,
)
from coderecon.index.db.database import BulkWriter, Database
from coderecon.index.db.epoch import EpochManager, EpochStats
from coderecon.index.db.indexes import create_additional_indexes
from coderecon.index.db.integrity import (
    IndexRecovery,
    IntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)
from coderecon.index.db.reconcile import ChangedFile, Reconciler, ReconcileResult

__all__ = [
    "Database",
    "BulkWriter",
    "ConsistencyReport",
    "SignalGap",
    "backfill_gaps",
    "check_consistency",
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
