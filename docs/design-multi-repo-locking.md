---
title: Locking & Concurrency
description: Concurrency design for one server managing N repos × M worktrees
---

## Object Hierarchy

One process. One event loop. All repos and worktrees multiplexed inside it.

```
GlobalDaemon                           ← 1 per process
  ├── CatalogRegistry                  ← repo + worktree discovery (sqlite)
  │
  ├── RepoSlot["myrepo"]              ← 1 per git repo (keyed by git-dir)
  │     ├── IndexCoordinatorEngine     ← owns DB + Tantivy + threading locks
  │     ├── FreshnessGate              ← per-worktree staleness tracking
  │     ├── MutationRouter             ← per-worktree mutation serialization
  │     ├── BackgroundIndexer          ← shared thread pool, debounced flush
  │     │
  │     ├── WorktreeSlot["main"]       ← 1 per worktree
  │     │     ├── FileWatcher          ← inotify/poll on this worktree root
  │     │     ├── AppContext           ← bound to this (repo, worktree)
  │     │     ├── MutationOps          ← writes to this worktree's files
  │     │     ├── SessionManager       ← sessions scoped to this worktree
  │     │     └── FastMCP              ← MCP endpoint for this worktree
  │     │
  │     └── WorktreeSlot["feature-x"]
  │           ├── FileWatcher
  │           ├── AppContext
  │           ├── MutationOps
  │           ├── SessionManager
  │           └── FastMCP
  │
  └── RepoSlot["other-repo"]
        └── ...
```

### Why This Shape

- **IndexCoordinatorEngine per repo, not per worktree.** Tantivy is a
  single-writer index. Running one per worktree wastes memory and disk for repos
  where 95%+ of the files are shared. The coordinator already serializes writes
  via `_reconcile_lock` + `_tantivy_write_lock`; duplicating those writes across
  N worktrees just moves the serialization outside the process where it's harder
  to reason about. One coordinator, one set of threading locks, one DB, one
  Tantivy — shared across all worktrees of that repo.

- **Everything else per worktree.** FileWatcher, MutationOps, AppContext,
  SessionManager, the MCP server itself — all scoped to a specific worktree
  root. A session connecting to `/repos/myrepo/worktrees/feature-x/mcp` gets an
  `AppContext` that can only see and mutate `feature-x`'s filesystem. No request
  routing heuristics. No runtime repo-identity resolution. The MCP endpoint
  *is* the worktree identity.

- **No single-repo mode.** `GlobalDaemon` with one `RepoSlot` containing one
  `WorktreeSlot` is the only code path. The old `run_single_repo_server()` /
  `daemon/app.py` / `daemon/routes.py` are deleted.

---

## New Concurrency Primitives

### 1. `FreshnessGate`

Replaces `IndexCoordinatorEngine._fresh_event`. Lives on `RepoSlot`.

```python
class FreshnessGate:
    """Per-worktree freshness tracking for a shared index.

    When worktree "main" mutates files, only searches on "main" block.
    Searches on "feature-x" proceed unimpeded.
    """

    def __init__(self) -> None:
        self._stale: set[str] = set()
        self._events: dict[str, asyncio.Event] = {}

    def mark_stale(self, worktree: str) -> None:
        """SYNC. Call before scheduling reindex."""
        self._stale.add(worktree)
        self._get_event(worktree).clear()

    def mark_fresh(self, worktree: str) -> None:
        """Call when reindex for this worktree's batch completes."""
        self._stale.discard(worktree)
        self._get_event(worktree).set()

    async def wait_fresh(self, worktree: str, timeout: float = 10.0) -> bool:
        """Block until this worktree's pending mutations are indexed."""
        if worktree not in self._stale:
            return True
        try:
            await asyncio.wait_for(self._get_event(worktree).wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _get_event(self, worktree: str) -> asyncio.Event:
        evt = self._events.get(worktree)
        if evt is None:
            evt = asyncio.Event()
            evt.set()  # default: fresh
            self._events[worktree] = evt
        return evt
```

**Why not keep `_fresh_event` on the coordinator?** Because the coordinator is
shared across worktrees. If worktree A mutates a file and the coordinator
clears `_fresh_event`, worktree B's search blocks even though B has no pending
mutations. `FreshnessGate` decouples staleness per worktree while the
coordinator remains shared.

**Invariant:** `mark_stale()` is called synchronously *before*
`asyncio.create_task(reindex)`. This is the same invariant as today, just
parameterized by worktree name.

### 2. `MutationRouter`

Replaces the bare `on_mutation` callback. Lives on `RepoSlot`.

```python
class MutationRouter:
    """Serializes mutations per worktree. Provides backpressure.

    Two sessions on the same worktree cannot interleave mutations.
    Two sessions on different worktrees CAN mutate concurrently —
    they serialize naturally at the coordinator's _reconcile_lock,
    but the MutationRouter prevents unbounded queueing at the async layer.
    """

    def __init__(
        self,
        coordinator: IndexCoordinatorEngine,
        gate: FreshnessGate,
        indexer: BackgroundIndexer,
        max_inflight: int = 2,
    ) -> None:
        self._coordinator = coordinator
        self._gate = gate
        self._indexer = indexer
        # One async lock per worktree — serializes mutation tool calls.
        self._locks: dict[str, asyncio.Lock] = {}
        # Bounds total concurrent reindex tasks across all worktrees.
        self._reindex_semaphore = asyncio.Semaphore(max_inflight)

    def _get_lock(self, worktree: str) -> asyncio.Lock:
        lock = self._locks.get(worktree)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[worktree] = lock
        return lock

    @asynccontextmanager
    async def mutation(self, worktree: str) -> AsyncIterator[None]:
        """Acquire exclusive mutation access for a worktree.

        Usage in tool handler:
            async with repo_slot.router.mutation(wt_name):
                result = mutation_ops.write_source(edits)
        """
        async with self._get_lock(worktree):
            yield

    async def on_mutation(self, worktree: str, paths: list[Path]) -> None:
        """Called after files are written. Triggers scoped reindex.

        SYNC staleness mark + ASYNC reindex with backpressure.
        """
        self._gate.mark_stale(worktree)

        async def _reindex() -> None:
            async with self._reindex_semaphore:
                try:
                    await self._coordinator.reindex_incremental(paths)
                finally:
                    self._gate.mark_fresh(worktree)

        asyncio.create_task(_reindex())
```

**What `MutationRouter.mutation()` buys us:**

| Scenario | Without `MutationRouter` | With `MutationRouter` |
|----------|--------------------------|----------------------|
| Two sessions on same worktree call `write_source` simultaneously | Both enter MutationOps, interleaved filesystem writes, two racing reindex tasks | Second call waits until first completes. One reindex at a time per worktree. |
| Session A mutates worktree-main while Session B mutates worktree-feature | Both race to `_reconcile_lock` in the coordinator; A or B blocks on a threading lock with no async yield | Both enter `MutationRouter.mutation()` concurrently (different worktree locks). They serialize at `_reconcile_lock` as before, but the async layer remains responsive. |
| Rapid-fire mutations (agent loop doing 10 edits in a row) | 10 `create_task(reindex)` calls queued with no backpressure | `_reindex_semaphore` bounds inflight reindexes to 2. Remaining tasks block at the semaphore, not at the threading lock. |

**Why `max_inflight=2`?** One slot for the hottest worktree, one slot to let a
second worktree make progress. More than 2 doesn't help — Tantivy's
single-writer lock serializes them anyway, and the thread pool has bounded
workers.

### 3. Session–Worktree Binding

Each `WorktreeSlot` owns its own `SessionManager`. There's no session-level
`repo_name` or `worktree_name` field because the session *cannot* exist outside
its worktree's `SessionManager`. The MCP endpoint URL is the identity:

```
/repos/{repo_name}/worktrees/{worktree_name}/mcp
```

A session created via this endpoint gets the `WorktreeSlot`'s `AppContext`. The
`AppContext` has the worktree's `repo_root`. There's nothing to validate at
runtime — the type system enforces it.

```python
@dataclass
class WorktreeSlot:
    name: str
    repo_root: Path           # worktree root (not git-dir)
    repo_slot: RepoSlot       # parent ref for shared resources
    watcher: FileWatcher
    app_ctx: AppContext
    session_manager: SessionManager
    mcp: FastMCP
```

`AppContext` changes:

```python
@dataclass
class AppContext:
    worktree_name: str                    # NEW — which worktree
    repo_root: Path
    git_ops: GitOps
    coordinator: IndexCoordinatorEngine   # shared (from RepoSlot)
    gate: FreshnessGate                   # shared (from RepoSlot)
    router: MutationRouter                # shared (from RepoSlot)
    file_ops: FileOps
    mutation_ops: MutationOps
    refactor_ops: RefactorOps
    test_ops: TestOps
    lint_ops: LintOps
    session_manager: SessionManager       # per-worktree
```

The `AppContext.create()` factory is removed. `WorktreeSlot` construction wires
everything explicitly.

---

## Lock Hierarchy

Every lock in the system, in strict acquisition order. Violating this order is
a deadlock.

```
Level 0: session._exclusive_lock       asyncio.Lock    per session
Level 1: router._locks[worktree]       asyncio.Lock    per worktree
Level 2: router._reindex_semaphore     asyncio.Sem     per repo (bounds inflight)
Level 3: coordinator._reconcile_lock   threading.Lock  per repo
Level 4: coordinator._tantivy_write_lock threading.Lock per repo
```

**Rules:**
- A higher-level lock may be held while acquiring a lower-level lock.
- Never acquire a higher-level lock while holding a lower-level lock.
- `FreshnessGate` events are **not locks** — `wait_fresh()` is an `await` on
  an `Event`, which releases the event loop. It doesn't participate in the lock
  hierarchy and cannot deadlock.

### Tool → Lock Mapping

| Tool category | Locks acquired |
|---------------|---------------|
| `recon`, `describe`, `graph` | `session._exclusive_lock` (brief) |
| `search` (text/semantic) | `session._exclusive_lock` (brief) → `gate.wait_fresh(wt)` |
| `write_source`, `refactor_edit` | `session._exclusive_lock` (brief) → `router.mutation(wt)` → [reindex path: semaphore → reconcile → tantivy] |
| `refactor_plan`, `refactor_move`, `refactor_rename` | `session._exclusive_lock` (brief) → `router.mutation(wt)` |
| `checkpoint`, `semantic_diff` | `session._exclusive_lock` (held for duration) |

---

## Mutation Flow (End-to-End)

```
Agent calls write_source(edits) via MCP on /repos/myrepo/worktrees/main/mcp
  │
  │  ToolMiddleware resolves session from WorktreeSlot["main"].session_manager
  │
  ├─ async with session.exclusive("write_source"):     # Level 0
  │    │
  │    ├─ async with app_ctx.router.mutation("main"):  # Level 1
  │    │    │
  │    │    ├─ app_ctx.mutation_ops.write_source(edits) # filesystem writes
  │    │    │   ├─ validate all edits
  │    │    │   ├─ write files atomically
  │    │    │   └─ compute FileDelta
  │    │    │
  │    │    ├─ await app_ctx.router.on_mutation("main", changed_paths)
  │    │    │   ├─ gate.mark_stale("main")             # SYNC — blocks search immediately
  │    │    │   └─ create_task(_reindex)                # ASYNC — bounded by semaphore
  │    │    │        ├─ async with _reindex_semaphore:  # Level 2
  │    │    │        │    ├─ coordinator.reindex_incremental(paths)
  │    │    │        │    │    ├─ with _reconcile_lock:  # Level 3
  │    │    │        │    │    │    └─ reconcile + extract + persist
  │    │    │        │    │    └─ with _tantivy_write_lock:  # Level 4
  │    │    │        │    │         └─ stage + commit
  │    │    │        │    └─ (returns IndexStats)
  │    │    │        └─ gate.mark_fresh("main")
  │    │    │
  │    │    └─ return MutationResult
  │    │
  │    └─ (mutation lock released — next mutation on "main" can proceed)
  │
  └─ (session exclusive released)
```

Meanwhile, a search on worktree "feature-x" proceeds without blocking:

```
Agent calls search("foo") via MCP on /repos/myrepo/worktrees/feature-x/mcp
  │
  ├─ async with session.exclusive("search"):  # different session, different worktree
  │    ├─ await gate.wait_fresh("feature-x")  # "feature-x" is not stale → returns True immediately
  │    └─ coordinator.search("foo")            # reads Tantivy (concurrent readers OK)
  └─ done
```

---

## Watcher → Indexer → Gate Integration

FileWatcher is per-worktree. BackgroundIndexer is per-repo (shared). The
watcher needs to tag changes with their worktree name so the indexer can
propagate it to the gate.

```python
@dataclass
class TaggedPaths:
    """File changes tagged with their source worktree."""
    worktree: str
    paths: list[Path]

@dataclass
class BackgroundIndexer:
    coordinator: IndexCoordinatorEngine
    gate: FreshnessGate
    config: IndexerConfig

    _pending: dict[str, set[Path]]   # worktree → pending paths
    _pending_lock: threading.Lock

    def queue_paths(self, worktree: str, paths: list[Path]) -> None:
        with self._pending_lock:
            bucket = self._pending.setdefault(worktree, set())
            bucket.update(paths)
        self._schedule_flush()

    async def _flush(self) -> None:
        with self._pending_lock:
            snapshot = {wt: list(ps) for wt, ps in self._pending.items() if ps}
            self._pending.clear()

        if not snapshot:
            return

        # Flatten for indexing (coordinator doesn't care about worktree)
        all_paths = [p for ps in snapshot.values() for p in ps]

        # Mark all affected worktrees stale BEFORE indexing
        for wt in snapshot:
            self.gate.mark_stale(wt)

        try:
            stats = await loop.run_in_executor(self._executor, self._index_sync, all_paths)
        finally:
            # All affected worktrees become fresh
            for wt in snapshot:
                self.gate.mark_fresh(wt)

        if self._on_complete:
            await self._on_complete(stats, all_paths)
```

**Why flatten?** The coordinator indexes files by content, not by worktree.  A
file at `src/foo.py` changed in worktree "main" and the same relative path
changed in worktree "feature-x" should be indexed once if their content hashes
match (dedup). Flattening + dedup is correct here.

**Why per-worktree staleness even for flattened indexing?** Because the
*consumer* of freshness is the search tool, which knows which worktree it's on.
Worktree "feature-x" shouldn't wait for "main"'s changes to be indexed if
"feature-x" itself has no pending changes.

---

## Coordinator Changes

`IndexCoordinatorEngine` drops `_fresh_event`. It keeps `_reconcile_lock` and
`_tantivy_write_lock` unchanged — those are the correct granularity for
serializing writes to a shared index.

```python
class IndexCoordinatorEngine:
    def __init__(self, repo_root, db_path, tantivy_path):
        self.repo_root = repo_root
        self.db = Database(db_path)
        self._reconcile_lock = threading.Lock()
        self._tantivy_write_lock = threading.Lock()
        # _fresh_event REMOVED — freshness tracking is now in FreshnessGate
        ...

    async def reindex_incremental(self, changed_paths: list[Path]) -> IndexStats:
        # No _fresh_event.clear() / .set() here anymore.
        # Caller (MutationRouter / BackgroundIndexer) manages freshness.
        return await self._reindex_incremental_impl(changed_paths)
```

This is cleaner: the coordinator is a pure index engine. Freshness is a
higher-level concern handled by the concurrency layer above it.

---

## `MutationOps` Changes

`MutationOps` drops the `on_mutation` callback. The callback was a design smell:
it coupled "I wrote files" with "now reindex", leaking coordinator concerns into
a filesystem-level class.

```python
@dataclass
class MutationOps:
    _repo_root: Path
    # on_mutation REMOVED — caller handles reindex

    def write_source(self, edits: list[Edit], *, dry_run: bool = False) -> MutationResult:
        # Validate, apply, compute delta.
        # Returns MutationResult. Does NOT trigger reindex.
        ...
```

The tool handler becomes the orchestrator:

```python
async def write_source_tool(edits, *, app_ctx: AppContext, session: SessionState):
    async with app_ctx.router.mutation(app_ctx.worktree_name):
        result = app_ctx.mutation_ops.write_source(edits)
        if result.applied:
            await app_ctx.router.on_mutation(app_ctx.worktree_name, result.changed_paths)
    return result
```

This makes the mutation → reindex → freshness flow explicit and testable.

---

## Endpoint Structure

```
/health                                        → global health
/catalog                                       → list repos + worktrees
/catalog/register                              → POST: register repo

/repos/{repo}/health                           → repo health
/repos/{repo}/status                           → repo indexer status
/repos/{repo}/reindex                          → POST: trigger full reindex

/repos/{repo}/worktrees/{worktree}/mcp         → MCP (streamable-http)
/repos/{repo}/worktrees/{worktree}/health      → worktree health + session count
```

The old `/repos/{name}/mcp` endpoint (repo-level, no worktree) is gone. Every
MCP connection targets a specific worktree. For repos with no explicit
worktrees, there's exactly one `WorktreeSlot` named `"main"` pointing at the
repo root.

---

## `GlobalDaemon.activate_repo()` Rewrite

```python
async def activate_repo(self, name: str, repo_root: Path, ...) -> RepoSlot:
    config = load_config(repo_root)

    # Shared per-repo resources
    coordinator = IndexCoordinatorEngine(repo_root, db_path, tantivy_path)
    await coordinator.load_existing()

    gate = FreshnessGate()
    indexer = BackgroundIndexer(coordinator, gate, config.indexer)
    router = MutationRouter(coordinator, gate, indexer)
    indexer.start()

    repo_slot = RepoSlot(name, coordinator, gate, router, indexer)

    # Discover worktrees from git
    git_ops = GitOps(repo_root)
    worktrees = git_ops.worktrees()  # includes main

    for wt in worktrees:
        wt_slot = await self._activate_worktree(repo_slot, wt, config)
        repo_slot.worktrees[wt.name] = wt_slot

    self._slots[name] = repo_slot
    return repo_slot

async def _activate_worktree(self, repo_slot, wt, config) -> WorktreeSlot:
    wt_root = Path(wt.root_path)

    # Per-worktree ops
    git_ops = GitOps(wt_root)
    file_ops = FileOps(wt_root)
    mutation_ops = MutationOps(wt_root)  # no callback
    refactor_ops = RefactorOps(wt_root, repo_slot.coordinator)
    test_ops = TestOps(wt_root, repo_slot.coordinator)
    lint_ops = LintOps(wt_root, repo_slot.coordinator)
    session_manager = SessionManager(config.timeouts)

    app_ctx = AppContext(
        worktree_name=wt.name,
        repo_root=wt_root,
        git_ops=git_ops,
        coordinator=repo_slot.coordinator,  # shared
        gate=repo_slot.gate,                # shared
        router=repo_slot.router,            # shared
        file_ops=file_ops,
        mutation_ops=mutation_ops,
        refactor_ops=refactor_ops,
        test_ops=test_ops,
        lint_ops=lint_ops,
        session_manager=session_manager,
    )

    mcp = create_mcp_server(app_ctx)

    watcher = FileWatcher(
        repo_root=wt_root,
        on_change=lambda paths, wt=wt.name: repo_slot.indexer.queue_paths(wt, paths),
    )
    await watcher.start()

    # Wire analysis pipeline per worktree
    pipeline = AnalysisPipeline(
        coordinator=repo_slot.coordinator,
        lint_ops=lint_ops,
        test_ops=test_ops,
        repo_root=wt_root,
    )
    # pipeline callback attached to indexer (shared), keyed by worktree? 
    # Actually: pipeline runs on index completion which is repo-wide.
    # Tier 1 lint only re-lints the specific changed files, so it's path-scoped
    # already. No worktree conflict.

    return WorktreeSlot(
        name=wt.name,
        repo_root=wt_root,
        repo_slot=repo_slot,
        watcher=watcher,
        app_ctx=app_ctx,
        session_manager=session_manager,
        mcp=mcp,
    )
```

---

## Deleted Code

| File | Reason |
|------|--------|
| `daemon/app.py` | Single-repo Starlette app. Replaced by `GlobalDaemon.build_app()`. |
| `daemon/routes.py` | Single-repo routes. Replaced by `GlobalDaemon` route tree. |
| `daemon/lifecycle.py: run_single_repo_server()` | Single-repo entrypoint. `run_global_server()` is the only entrypoint. |
| `AppContext.create()` classmethod | Factory that wired `on_mutation` callback. Construction is now explicit in `_activate_worktree()`. |
| `IndexCoordinatorEngine._fresh_event` | Moved to `FreshnessGate` on `RepoSlot`. |
| `MutationOps._on_mutation` | Callback removed. Tool handler calls `router.on_mutation()` explicitly. |

`ServerController` is absorbed into `RepoSlot` — it was just a bag holding
coordinator + indexer + watcher + shutdown event. `RepoSlot` is that bag now.

---

## Concurrency Invariants (Testable)

These must be verified by integration tests.

1. **Mutation serialization within a worktree.**
   Two concurrent `write_source` calls on the same worktree must not
   interleave filesystem writes. The second call must block until the first
   completes (enforced by `MutationRouter._locks[wt]`).

2. **Cross-worktree mutation independence.**
   `write_source` on worktree A must not block `write_source` on worktree B
   at the async layer. (They will serialize at `_reconcile_lock` in the thread
   pool, but the async caller is not blocked.)

3. **Search freshness isolation.**
   After `write_source` on worktree A, `search()` on worktree B must not block.
   `search()` on worktree A must block until reindex completes.

4. **Reindex backpressure.**
   With `max_inflight=2`, a third concurrent reindex must wait at
   `_reindex_semaphore` — not at `_reconcile_lock` or in unbounded task
   queueing.

5. **Session confinement.**
   A session created on worktree A's MCP endpoint cannot access worktree B's
   `SessionManager`, `MutationOps`, or `FileOps`. This is structural (separate
   `AppContext` instances), not runtime-validated.

6. **Exclusive tool isolation.**
   `checkpoint` on session X blocks all other tools on session X (same as
   today). It does NOT block tools on session Y, even on the same worktree.

7. **Watcher → gate consistency.**
   `BackgroundIndexer._flush()` must call `gate.mark_stale(wt)` for all
   affected worktrees BEFORE starting the reindex, and `gate.mark_fresh(wt)`
   AFTER it completes (including on error).

---

## Open Questions

1. **AnalysisPipeline per-repo vs per-worktree.** Tier 1 lint operates on
   specific file paths, so it's naturally per-worktree. Tier 2 test discovery
   also runs per-worktree (`pytest --collect-only` against the worktree root).
   Pipeline should be per-worktree, with the `on_complete` callback on the
   indexer dispatching to the correct worktree's pipeline based on the paths
   that were reindexed.

2. **Worktree addition/removal at runtime.** `git worktree add` while the
   daemon is running must trigger `_activate_worktree()`. Options: (a) poll
   `git worktree list` periodically, (b) watch `.git/worktrees/` for changes,
   (c) explicit `POST /repos/{name}/worktrees/add` API. Recommend (b) + (c).

3. **Dedup across worktrees.** The existing dedup layer (shared
   `IndexCoordinatorEngine`) deduplicates at the file-content-hash level.  This
   works when worktrees share most files. When they diverge significantly,
   the shared index grows to hold both versions. This is correct but may affect
   Tantivy memory. Monitor and document the tradeoff.
