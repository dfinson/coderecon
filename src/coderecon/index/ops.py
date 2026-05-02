"""High-level orchestration of the indexing engine.

This module implements the IndexCoordinatorEngine - the entry point for all
index operations. It enforces critical serialization invariants:

- reconcile_lock: Only ONE reconcile() at a time (prevents RepoState corruption)
- tantivy_write_lock: Only ONE Tantivy write batch at a time (prevents crashes)

The Coordinator owns component lifecycles and coordinates the indexing pipeline:
Discovery -> Authority -> Membership -> Probe -> Router -> Index
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import select

log = structlog.get_logger(__name__)

# --- Extracted modules ---
from coderecon.index import (
    ops_discovery,
    ops_getters,
    ops_graph,
    ops_indexing,
    ops_init,
    ops_reindex,
    ops_reindex_full,
    ops_search,
)
from coderecon.index.db import (
    Database,
    EpochManager,
    EpochStats,
    Reconciler,
)
from coderecon.index.discovery import (
    ContextRouter,
)
from coderecon.index.search.lexical import LexicalIndex
from coderecon.index.structural.structural import StructuralIndexer
from coderecon.index.parsing.service import tree_sitter_service
from coderecon.index._state import FileStateService
from coderecon.index.models import (
    Context,
    DefFact,
    Worktree,
)

# Re-export data classes and glob helpers for backward compatibility
from coderecon.index.ops_glob import (
    _matches_glob,
)
from coderecon.index.ops_types import (
    IndexStats,
    InitResult,
    SearchMode,
    SearchResponse,
    SearchResult,
)

if TYPE_CHECKING:
    from coderecon.daemon.concurrency import FreshnessGate


class IndexCoordinatorEngine:
    """
    High-level orchestration with serialization guarantees.

    SERIALIZATION:
    - Per-worktree locks via _get_worktree_lock(): Only ONE reconcile per worktree
    - _tantivy_write_lock: Only ONE Tantivy write batch at a time (global)

    These locks prevent:
    - RepoState corruption from concurrent reconciliations
    - Tantivy crashes from multiple writers

    Usage::

        coordinator = CplIndexCoordinator(repo_root, db_path, tantivy_path)
        result = await coordinator.initialize()

        # Search (thread-safe, no locks needed)
        results = await coordinator.search("query", SearchMode.TEXT)

        # Reindex (acquires locks automatically)
        stats = await coordinator.reindex_incremental([Path("a.py")])
    """

    def __init__(
        self,
        repo_root: Path,
        db_path: Path,
        tantivy_path: Path,
        busy_timeout_ms: int = 30_000,
    ) -> None:
        """Initialize coordinator with paths."""
        self.repo_root = repo_root
        self.db_path = db_path
        self.tantivy_path = tantivy_path
        # Database
        self.db = Database(db_path, busy_timeout_ms=busy_timeout_ms)
        # Serialization locks
        self._worktree_locks: dict[str, threading.Lock] = {}
        self._worktree_locks_guard = threading.Lock()
        self._tantivy_write_lock = threading.Lock()
        # Freshness gating — injected by daemon layer.
        self._freshness_gate: FreshnessGate | None = None
        self._freshness_worktree: str | None = None
        # Ordered worktree overlay for search queries.
        self._search_worktrees: list[str] = ["main"]
        # Components (initialized lazily in initialize())
        self._lexical: LexicalIndex | None = None
        self._parser = None
        self._router: ContextRouter | None = None
        self._structural: StructuralIndexer | None = None
        self._facts = None
        self._state: FileStateService | None = None
        self._reconciler: Reconciler | None = None
        self._epoch_manager: EpochManager | None = None
        self._initialized = False
        # Cache of worktree name → DB row ID.
        self._worktree_id_cache: dict[str, int] = {}
        self._worktree_is_main_cache: dict[str, bool] = {}
        self._worktree_root_cache: dict[str, Path] = {}
        # Optional in-memory cache of all DefFacts (keyed by def_uid).
        self._def_cache: dict[str, DefFact] | None = None

    def _get_worktree_lock(self, worktree: str) -> threading.Lock:
        """Return a per-worktree lock, creating it on first access."""
        with self._worktree_locks_guard:
            if worktree not in self._worktree_locks:
                self._worktree_locks[worktree] = threading.Lock()
            return self._worktree_locks[worktree]

    def _is_main_worktree(self, worktree: str) -> bool:
        """Return True if *worktree* is the main checkout (from DB flag)."""
        if worktree in self._worktree_is_main_cache:
            return self._worktree_is_main_cache[worktree]
        with self.db.session() as session:
            wt = session.exec(select(Worktree).where(Worktree.name == worktree)).first()
            is_main = wt.is_main if wt else (worktree == "main")
            self._worktree_is_main_cache[worktree] = is_main
            return is_main

    def _get_or_create_worktree_id(self, name: str, root_path: str | None = None) -> int:
        """Return the `worktrees.id` for *name*, inserting the row if absent."""
        if name in self._worktree_id_cache:
            return self._worktree_id_cache[name]
        with self._worktree_locks_guard:
            if name in self._worktree_id_cache:
                return self._worktree_id_cache[name]
            with self.db.session() as session:
                stmt = select(Worktree).where(Worktree.name == name)
                existing = session.exec(stmt).first()
                if existing and existing.id is not None:
                    self._worktree_id_cache[name] = existing.id
                    self._worktree_is_main_cache[name] = existing.is_main
                    return existing.id
                effective_root = root_path if root_path is not None else str(self.repo_root)
                wt = Worktree(
                    name=name,
                    root_path=effective_root,
                    is_main=(name == "main"),
                )
                session.add(wt)
                session.commit()
                session.refresh(wt)
                if wt.id is None:
                    raise RuntimeError(f"Failed to allocate worktree id for {name!r}")
                self._worktree_id_cache[name] = wt.id
                self._worktree_is_main_cache[name] = wt.is_main
                return wt.id

    def set_freshness_gate(
        self, gate: FreshnessGate, worktree: str, worktree_root: str | None = None
    ) -> None:
        """Inject freshness gate from daemon layer."""
        self._freshness_gate = gate
        self._freshness_worktree = worktree
        self._search_worktrees = [worktree] if worktree == "main" else [worktree, "main"]
        self._get_or_create_worktree_id(worktree, root_path=worktree_root)
        if worktree_root is not None and worktree != "main":
            self._worktree_root_cache[worktree] = Path(worktree_root)

    async def initialize(
        self,
        on_index_progress: Callable[[int, int, dict[str, int], str], None],
    ) -> InitResult:
        """Full initialization: discover, probe, index."""
        return await ops_init.initialize(self, on_index_progress)

    async def collect_initial_coverage(
        self,
        *,
        parallelism: int | None = None,
        memory_reserve_mb: int = 1024,
        subprocess_memory_limit_mb: int | None = None,
        timeout_sec: int = 600,
    ) -> int:
        """Run the full test suite with coverage and ingest results."""
        return await ops_init.collect_initial_coverage(
            self,
            parallelism=parallelism,
            memory_reserve_mb=memory_reserve_mb,
            subprocess_memory_limit_mb=subprocess_memory_limit_mb,
            timeout_sec=timeout_sec,
        )

    async def load_existing(self) -> bool:
        """Load existing index without re-indexing."""
        if self._initialized:
            return True
        if not self.db_path.exists():
            return False
        self._parser = tree_sitter_service.parser
        self._lexical = LexicalIndex(self.tantivy_path)
        self._epoch_manager = EpochManager(self.db, self._lexical)
        self._router = ContextRouter()
        with self.db.session() as session:
            contexts = session.exec(select(Context)).all()
            if not contexts:
                return False
        self._structural = StructuralIndexer(self.db, self.repo_root)
        self._state = FileStateService(self.db)
        self._reconciler = Reconciler(self.db, self.repo_root)
        self._facts = None
        if self._lexical is not None:
            self._lexical.reload()
        self._get_or_create_worktree_id("main")
        self._initialized = True
        return True

    def backfill_missing_signals(self) -> dict[str, int]:
        """Detect and backfill missing derived signals."""
        from coderecon.index.db import (
            backfill_gaps,
            check_consistency,
        )
        report = check_consistency(self.db)
        if report.consistent:
            return {}
        return backfill_gaps(self.db, report)

    def changed_since_last_index(self) -> list[Path]:
        """Return paths changed since the last indexed HEAD."""
        if self._reconciler is None:
            return []
        changed = self._reconciler.get_changed_files()
        return [self.repo_root / cf.path for cf in changed]

    async def wait_for_freshness(self) -> None:
        """Block until index is fresh (no pending writes)."""
        if not self._initialized:
            msg = "Coordinator not initialized"
            raise RuntimeError(msg)
        if self._freshness_gate is not None and self._freshness_worktree is not None:
            await self._freshness_gate.wait_fresh(self._freshness_worktree)

    # ------------------------------------------------------------------
    # Reindex delegates
    # ------------------------------------------------------------------

    async def reindex_incremental(
        self, changed_paths: list[Path], worktree: str = "main"
    ) -> IndexStats:
        """Incremental reindex for changed files.  SERIALIZED."""
        try:
            return await ops_reindex._reindex_incremental_impl(self, changed_paths, worktree)
        finally:
            self._def_cache = None

    async def reindex_full(self) -> IndexStats:
        """Full repository reindex - idempotent and incremental."""
        try:
            return await ops_reindex_full._reindex_full_impl(self)
        finally:
            self._def_cache = None

    # ------------------------------------------------------------------
    # Graph maintenance delegates (ops_graph)
    # ------------------------------------------------------------------

    def _remove_structural_facts_for_paths(self, *a, **kw): return ops_graph._remove_structural_facts_for_paths(self, *a, **kw)
    def _invalidate_dangling_refs(self, *a, **kw): return ops_graph._invalidate_dangling_refs(self, *a, **kw)
    def _propagate_def_changes(self, *a, **kw): return ops_graph._propagate_def_changes(self, *a, **kw)
    def _sweep_orphaned_edges(self): return ops_graph._sweep_orphaned_edges(self)
    def _mark_coverage_stale(self, *a, **kw): return ops_graph._mark_coverage_stale(self, *a, **kw)

    # ------------------------------------------------------------------
    # Search delegates (ops_search)
    # ------------------------------------------------------------------

    def score_files_bm25(self, *a, **kw): return ops_search.score_files_bm25(self, *a, **kw)
    async def search(self, *a, **kw) -> SearchResponse: return await ops_search.search(self, *a, **kw)
    async def search_symbols(self, *a, **kw) -> SearchResponse: return await ops_search.search_symbols(self, *a, **kw)

    # ------------------------------------------------------------------
    # Getter delegates (ops_getters)
    # ------------------------------------------------------------------

    async def get_def(self, *a, **kw): return await ops_getters.get_def(self, *a, **kw)
    async def get_all_defs(self, *a, **kw): return await ops_getters.get_all_defs(self, *a, **kw)
    async def get_references(self, *a, **kw): return await ops_getters.get_references(self, *a, **kw)
    async def get_all_references(self, *a, **kw): return await ops_getters.get_all_references(self, *a, **kw)
    async def get_callees(self, *a, **kw): return await ops_getters.get_callees(self, *a, **kw)
    async def get_file_imports(self, *a, **kw): return await ops_getters.get_file_imports(self, *a, **kw)
    async def get_file_state(self, *a, **kw): return await ops_getters.get_file_state(self, *a, **kw)
    async def get_file_stats(self): return await ops_getters.get_file_stats(self)
    async def get_indexed_file_count(self, *a, **kw): return await ops_getters.get_indexed_file_count(self, *a, **kw)
    async def get_indexed_files(self, *a, **kw): return await ops_getters.get_indexed_files(self, *a, **kw)
    async def get_contexts(self): return await ops_getters.get_contexts(self)
    async def get_test_targets(self, *a, **kw): return await ops_getters.get_test_targets(self, *a, **kw)
    async def get_affected_test_targets(self, *a, **kw): return await ops_getters.get_affected_test_targets(self, *a, **kw)
    async def get_coverage_sources(self, *a, **kw): return await ops_getters.get_coverage_sources(self, *a, **kw)
    async def get_coverage_gaps(self): return await ops_getters.get_coverage_gaps(self)
    async def get_lint_tools(self, *a, **kw): return await ops_getters.get_lint_tools(self, *a, **kw)
    async def get_context_runtime(self, *a, **kw): return await ops_getters.get_context_runtime(self, *a, **kw)
    async def get_coverage_capability(self, *a, **kw): return await ops_getters.get_coverage_capability(self, *a, **kw)
    async def map_repo(self, *a, **kw): return await ops_getters.map_repo(self, *a, **kw)
    async def verify_integrity(self): return await ops_getters.verify_integrity(self)
    async def recover(self): return await ops_getters.recover(self)

    @property
    def current_epoch(self) -> int:
        """Return current epoch ID, or 0 if none published."""
        return ops_getters.get_current_epoch(self)

    def get_current_epoch(self): return ops_getters.get_current_epoch(self)
    def publish_epoch(self, *a, **kw) -> EpochStats: return ops_getters.publish_epoch(self, *a, **kw)
    def await_epoch(self, *a, **kw): return ops_getters.await_epoch(self, *a, **kw)
    def close(self): return ops_getters.close(self)

    # ------------------------------------------------------------------
    # Discovery delegates (ops_discovery)
    # ------------------------------------------------------------------

    async def _resolve_context_runtimes(self): return await ops_discovery._resolve_context_runtimes(self)
    async def _discover_test_targets(self): return await ops_discovery._discover_test_targets(self)
    async def _discover_lint_tools(self): return await ops_discovery._discover_lint_tools(self)
    async def _discover_coverage_capabilities(self): return await ops_discovery._discover_coverage_capabilities(self)
    async def _rediscover_test_targets(self): return await ops_discovery._rediscover_test_targets(self)
    async def _rediscover_lint_tools(self): return await ops_discovery._rediscover_lint_tools(self)
    async def _update_test_targets_incremental(self, *a, **kw): return await ops_discovery._update_test_targets_incremental(self, *a, **kw)
    async def _update_lint_tools_incremental(self, *a, **kw): return await ops_discovery._update_lint_tools_incremental(self, *a, **kw)

    # ------------------------------------------------------------------
    # Indexing delegates (ops_indexing)
    # ------------------------------------------------------------------

    async def _index_all_files(self, *a, **kw): return await ops_indexing._index_all_files(self, *a, **kw)
    def _index_splade_vectors(self, *a, **kw): return ops_indexing._index_splade_vectors(self, *a, **kw)
    def _reindex_splade_vectors(self, *a, **kw): return ops_indexing._reindex_splade_vectors(self, *a, **kw)
    def _reindex_semantic_passes(self, *a, **kw): return ops_indexing._reindex_semantic_passes(self, *a, **kw)
    def _get_doc_file_ids(self, *a, **kw): return ops_indexing._get_doc_file_ids(self, *a, **kw)
    def _index_doc_chunks(self, *a, **kw): return ops_indexing._index_doc_chunks(self, *a, **kw)
    def batch_get_defs(self, *a, **kw): return ops_indexing.batch_get_defs(self, *a, **kw)
    def _clear_all_structural_facts(self): return ops_indexing._clear_all_structural_facts(self)
    def _extract_symbols(self, *a, **kw): return ops_indexing._extract_symbols(self, *a, **kw)
    def _safe_read_text(self, *a, **kw): return ops_indexing._safe_read_text(self, *a, **kw)
    def _walk_all_files(self): return ops_indexing._walk_all_files(self)
    def _filter_files_for_context(self, *a, **kw): return ops_indexing._filter_files_for_context(self, *a, **kw)
    def _filter_unclaimed_files(self, *a, **kw): return ops_indexing._filter_unclaimed_files(self, *a, **kw)


__all__ = [
    "IndexCoordinatorEngine",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResponse",
    "SearchResult",
    "_matches_glob",
]
