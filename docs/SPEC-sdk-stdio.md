# CodeRecon SDK Spec — Exported Intelligence Layer

**Status**: Draft  
**Date**: 2026-04-22

---

## 1. Problem

CodeRecon is an intelligence layer for AI coding agents. Today the only integration path is MCP-over-HTTP: the agent host must know about MCP, connect to a running daemon over `streamable-http`, and route tool calls through the MCP protocol.

This locks out a large class of integrators:

- **Agentic orchestration frameworks** that wire tools as plain Python callables (functools, LangChain tools, CrewAI tools, etc.) — they don't speak MCP natively.
- **Platforms** that want to embed CodeRecon as a library and pass its capabilities directly to their LLM function-calling layer, without an intermediate protocol.
- **CI/CD and batch pipelines** that want to call CodeRecon programmatically — index a repo, query it, run analysis — without standing up an HTTP service.

What's needed: **export CodeRecon's capabilities as a Python SDK whose functions can be handed directly to agents as tools**, while the global daemon (always global, always multi-repo) runs as a child process of the SDK consumer, communicating over stdio.

---

## 2. Design Principles

| Principle | Meaning |
|-----------|---------|
| **The daemon is global** | One daemon process, multi-repo catalog, lazy activation — unchanged. No per-repo daemons. |
| **SDK replaces MCP, not wraps it** | SDK functions are native Python callables. They are the tool definitions the integrator passes to their agents. MCP is not involved. |
| **Daemon is a child process** | The SDK spawns the daemon. The integrator owns the process lifecycle. Communication is over stdio pipes. |
| **Functions are functools** | Each SDK function has a typed signature, docstring, and JSON-schema-derivable parameter model. An integrator can hand them to `openai.tools`, LangChain `@tool`, or any function-calling framework. |
| **Thin SDK, fat daemon** | All intelligence, indexing, concurrency, and state live in the daemon. The SDK is a typed RPC client over stdio — serialization, deserialization, and process management. |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Integrator Process                        │
│                                                              │
│  ┌─────────────────────┐     ┌─────────────────────────────┐ │
│  │  Agent Framework     │     │  coderecon SDK              │ │
│  │  (LangChain, OpenAI, │     │                             │ │
│  │   CrewAI, custom)    │     │  sdk = CodeRecon()          │ │
│  │                      │     │  await sdk.start()          │ │
│  │  agent.tools = [     │────▶│                             │ │
│  │    sdk.recon,        │     │  sdk.recon(...)      ──┐    │ │
│  │    sdk.recon_map,    │     │  sdk.refactor_rename ──┤    │ │
│  │    sdk.checkpoint,   │     │  sdk.checkpoint(...) ──┤    │ │
│  │    ...               │     │                        │    │ │
│  │  ]                   │     │      (typed callables) │    │ │
│  └─────────────────────┘     └─────────────────────┬──┘    │ │
│                                                    │ stdio  │ │
│                                    ┌───────────────▼──────┐ │ │
│                                    │  Global Daemon       │ │ │
│                                    │  (child process)     │ │ │
│                                    │                      │ │ │
│                                    │  Catalog ─┐          │ │ │
│                                    │  RepoSlots │          │ │ │
│                                    │    ├ Index  │          │ │ │
│                                    │    ├ Watcher│          │ │ │
│                                    │    ├ Gate   │          │ │ │
│                                    │    └ Ops    │          │ │ │
│                                    │  Sessions ─┘          │ │ │
│                                    └──────────────────────┘ │ │
└──────────────────────────────────────────────────────────────┘
```

### Key relationships

- **SDK process** = integrator's process. The SDK object lives here.
- **Daemon process** = child of SDK process. Spawned by `sdk.start()`, killed by `sdk.stop()`. This is the same `GlobalDaemon` that `recon up` runs — catalog, lazy repo activation, watchers, indexers, all of it.
- **Communication** = bidirectional stdio pipes. A lightweight JSON-RPC-style request/response protocol (NOT MCP). The daemon reads requests from stdin, writes responses to stdout.
- **SDK functions** = async Python callables that serialize arguments, send them over stdin to the daemon, await the response on stdout, and deserialize the result. These are what the integrator hands to their agent framework.

---

## 4. SDK Public API

### 4.1 Initialization

```python
from coderecon import CodeRecon

sdk = CodeRecon(
    # Optional: explicit path to `recon` binary; defaults to PATH lookup
    binary: str | None = None,
    # Optional: explicit coderecon home; defaults to ~/.coderecon
    home: str | Path | None = None,
)

# Start the daemon child process
await sdk.start()

# Register a repo (if not already in catalog)
await sdk.register("/path/to/repo")
```

`start()` spawns the global daemon as a child process. If a daemon is already running (PID file exists and process is alive), `start()` connects to it over stdio instead of spawning a new one — or raises, depending on policy (see Open Questions).

### 4.2 Tool Functions — The Core Surface

Each function is an **async callable** with a typed signature. These are what get passed to agent frameworks.

Every tool function requires a `repo` parameter — the registered repo name (or path). This replaces the URL path routing (`/repos/{name}/worktrees/{wt}/mcp/...`) from the HTTP daemon.

```python
# ── Context Retrieval ──────────────────────────────────────

await sdk.recon(
    repo: str,                      # Registered repo name or path
    task: str,                      # Natural language query
    seeds: list[str] = [],          # Explicit file/symbol hints
    pins: list[str] = [],           # Files to always include
    worktree: str = "main",         # Worktree name
) -> ReconResult

await sdk.recon_map(
    repo: str,
    include: list[str] = [],        # "structure", "languages", "entry_points", etc.
    depth: int = 3,
    limit: int = 100,
    worktree: str = "main",
) -> MapResult

await sdk.recon_impact(
    repo: str,
    target: str,                    # Symbol or file path to analyze
    justification: str,
    include_comments: bool = True,
    worktree: str = "main",
) -> ImpactResult

await sdk.recon_understand(
    repo: str,
    worktree: str = "main",
) -> UnderstandResult

# ── Refactoring ────────────────────────────────────────────

await sdk.refactor_rename(
    repo: str,
    symbol: str,
    new_name: str,
    justification: str,
    include_comments: bool = True,
    contexts: list[str] | None = None,
    worktree: str = "main",
) -> RefactorResult

await sdk.refactor_move(
    repo: str,
    from_path: str,
    to_path: str,
    justification: str,
    include_comments: bool = True,
    worktree: str = "main",
) -> RefactorResult

await sdk.refactor_commit(
    repo: str,
    refactor_id: str,
    inspect_path: str | None = None,
    context_lines: int = 2,
    worktree: str = "main",
) -> RefactorCommitResult

await sdk.refactor_cancel(
    repo: str,
    refactor_id: str,
    worktree: str = "main",
) -> RefactorCancelResult

# ── Analysis ───────────────────────────────────────────────

await sdk.semantic_diff(
    repo: str,
    base: str = "HEAD",
    target: str | None = None,
    paths: list[str] | None = None,
    scope_id: str | None = None,
    worktree: str = "main",
) -> DiffResult

await sdk.graph_cycles(
    repo: str,
    level: str = "file",           # "file" or "def"
    worktree: str = "main",
) -> CyclesResult

await sdk.graph_communities(
    repo: str,
    level: str = "file",
    resolution: float = 1.0,
    worktree: str = "main",
) -> CommunitiesResult

await sdk.graph_export(
    repo: str,
    output_path: str = "",
    resolution: float = 1.0,
    worktree: str = "main",
) -> GraphExportResult

# ── Checkpoint ─────────────────────────────────────────────

await sdk.checkpoint(
    repo: str,
    changed_files: list[str],
    lint: bool = True,
    autofix: bool = True,
    tests: bool = True,
    test_filter: str | None = None,
    max_test_hops: int | None = None,
    commit_message: str | None = None,
    push: bool = False,
    worktree: str = "main",
) -> CheckpointResult

# ── Raw Signals (training / evaluation) ────────────────────

await sdk.raw_signals(
    repo: str,
    query: str,                    # Same as recon task
    seeds: list[str] = [],
    pins: list[str] = [],
    worktree: str = "main",
) -> RawSignalsResult

# ── Introspection ──────────────────────────────────────────

await sdk.describe(
    action: str,                   # "tool" or "error"
    name: str | None = None,
    code: str | None = None,
) -> DescribeResult

# ── Daemon Management ─────────────────────────────────────

await sdk.register(path: str | Path) -> RegisterResult
await sdk.unregister(path: str | Path) -> bool
await sdk.catalog() -> list[CatalogEntry]
await sdk.status(repo: str | None = None) -> StatusResult
await sdk.reindex(repo: str, worktree: str = "main") -> None
```

### 4.3 Functions as Agent Tools

The SDK functions are designed to be passed directly to agent frameworks:

```python
from coderecon import CodeRecon

sdk = CodeRecon()
await sdk.start()
await sdk.register("/path/to/my-project")

# OpenAI function calling
from openai import AsyncOpenAI
client = AsyncOpenAI()

response = await client.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    tools=sdk.as_openai_tools(repo="my-project"),
)

# LangChain
from langchain_core.tools import StructuredTool
tools = sdk.as_langchain_tools(repo="my-project")

# Or just raw callables — any framework that accepts async functions
tools = [sdk.recon, sdk.recon_map, sdk.refactor_rename, sdk.checkpoint]
```

**`as_openai_tools(repo)`** — Returns OpenAI-compatible tool definitions (JSON schema for parameters, function references for execution). The `repo` parameter is pre-bound so the agent doesn't need to specify it per call.

**`as_langchain_tools(repo)`** — Returns `StructuredTool` instances wrapping each SDK function.

**Raw callables** — For frameworks that just want `async (kwargs) -> result`, the methods themselves work. The integrator binds `repo` via `functools.partial` or passes it explicitly.

### 4.4 Convenience: Repo-Bound Handle

For integrators working with a single repo, a bound handle avoids repeating `repo=` on every call:

```python
project = sdk.repo("my-project")

# These are the same callables, with repo pre-bound
result = await project.recon(task="find auth middleware")
plan = await project.refactor_rename(symbol="Foo", new_name="Bar", justification="...")
tools = project.as_openai_tools()
```

`sdk.repo(name)` returns a `RepoHandle` — a lightweight object where every tool method is the same SDK function with `repo` pre-bound via `functools.partial`.

---

## 5. Stdio Protocol

### 5.1 Why Not MCP Over Stdio?

MCP adds overhead and constraints that don't serve the SDK use case:

| MCP constraint | SDK need |
|----------------|----------|
| MCP tools have flat `arguments` dict, string content responses | SDK wants typed Python inputs/outputs |
| MCP session lifecycle (`initialize`, capabilities negotiation) | SDK knows exactly what the daemon supports |
| MCP tool discovery (`tools/list`) | SDK functions are statically defined |
| MCP content types (text, image, resource) | SDK returns structured Python objects |
| MCP requires an MCP client library | SDK should have zero protocol dependencies |

The stdio protocol is a **minimal, private, internal** wire format — not a public API. Only the SDK client and the daemon speak it.

### 5.2 Wire Format

Newline-delimited JSON (NDJSON). Each message is a single JSON object followed by `\n`.

**Request** (SDK → daemon stdin):
```json
{"id": "r1", "method": "recon", "params": {"repo": "my-project", "task": "find auth", "worktree": "main"}}
```

**Response** (daemon stdout → SDK):
```json
{"id": "r1", "result": {"recon_id": "abc123", "spans": [...], "metrics": {...}}}
```

**Error response:**
```json
{"id": "r1", "error": {"code": "REPO_NOT_FOUND", "message": "No repo 'foo' in catalog"}}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Request correlation ID. Response carries the same `id`. |
| `method` | `string` | Tool name: `"recon"`, `"refactor_rename"`, `"checkpoint"`, `"register"`, etc. |
| `params` | `object` | Tool parameters — matches the SDK function signature. |
| `result` | `object` | Success payload — tool-specific structured data. |
| `error` | `object` | Error payload — `code` (string enum) + `message`. |

### 5.3 Daemon-Initiated Events (Streaming)

The daemon pushes **events** to stdout interleaved with RPC responses. Events have no `id` (they're not responses to a request) and carry an `event` field identifying the event type.

```json
{"event": "index.progress", "data": {"repo": "my-project", "worktree": "main", "phase": "indexing", "indexed": 847, "total": 2103, "files_by_ext": {".py": 412, ".ts": 201, ".go": 234}}}
```

The SDK client's read loop distinguishes events from responses by the presence of `event` vs `id`.

#### Event Categories

**Index lifecycle** — full visibility into the indexing pipeline:

| Event | When | Data Fields |
|-------|------|-------------|
| `index.started` | Full or incremental reindex begins | `repo`, `worktree`, `mode` ("full"\|"incremental"), `file_count` |
| `index.progress` | Periodically during indexing | `repo`, `worktree`, `phase`, `indexed`, `total`, `files_by_ext` |
| `index.phase` | Phase transition within the pipeline | `repo`, `worktree`, `phase` (see below), `detail` |
| `index.complete` | Reindex finished successfully | `repo`, `worktree`, `stats` (files_processed, files_added, files_updated, files_removed, symbols_indexed, duration_ms) |
| `index.error` | Reindex failed | `repo`, `worktree`, `error` |

Index phases (from `IndexCoordinatorEngine.on_index_progress`):
- `indexing` — tree-sitter extraction of structural facts
- `resolving_cross_file` — cross-file import/export resolution
- `resolving_refs` — reference resolution pass
- `resolving_types` — type inference pass
- `encoding_splade` — SPLADE sparse vector encoding
- `semantic_resolve` — semantic reference resolution
- `semantic_neighbors` — neighbor graph construction
- `doc_chunk_linking` — documentation chunk linkage

**Watcher** — filesystem change detection:

| Event | When | Data Fields |
|-------|------|-------------|
| `watcher.changes` | Debounced batch of file changes | `repo`, `worktree`, `count`, `summary` (e.g. "3 Python files, 1 YAML file") |
| `watcher.mode` | Watch mode changed (native → polling fallback) | `repo`, `worktree`, `mode` ("native"\|"polling"), `reason` |

**Repo activation** — lazy-loaded repo/worktree lifecycle:

| Event | When | Data Fields |
|-------|------|-------------|
| `repo.activating` | Lazy activation triggered | `repo` |
| `repo.activated` | Repo fully activated (index loaded, watcher started) | `repo`, `worktrees` |
| `repo.deactivated` | Repo evicted or unregistered | `repo` |
| `worktree.activated` | Worktree lazily activated | `repo`, `worktree` |
| `worktree.evicted` | Idle worktree evicted | `repo`, `worktree`, `idle_sec` |

**Analysis pipeline** — background lint/test tiers:

| Event | When | Data Fields |
|-------|------|-------------|
| `analysis.tier1.complete` | Lint pass finished | `repo`, `worktree`, `files`, `diagnostics`, `elapsed_ms` |
| `analysis.tier2.complete` | Test pass finished | `repo`, `worktree`, `affected_tests`, `passed`, `failed`, `elapsed_ms` |

**Freshness** — index staleness transitions:

| Event | When | Data Fields |
|-------|------|-------------|
| `freshness.stale` | Worktree index marked stale (changes pending reindex) | `repo`, `worktree` |
| `freshness.fresh` | Worktree index up-to-date | `repo`, `worktree` |

**Daemon lifecycle**:

| Event | When | Data Fields |
|-------|------|-------------|
| `daemon.ready` | Daemon initialized and accepting requests | `version`, `repos` (list of registered repo names) |
| `daemon.stopping` | Graceful shutdown initiated | `reason` ("eof"\|"signal") |

#### Example: Full Index Run (Event Sequence)

```jsonl
{"event": "repo.activating", "data": {"repo": "my-project"}}
{"event": "index.started", "data": {"repo": "my-project", "worktree": "main", "mode": "full", "file_count": 2103}}
{"event": "index.progress", "data": {"repo": "my-project", "worktree": "main", "phase": "indexing", "indexed": 500, "total": 2103, "files_by_ext": {".py": 280, ".ts": 120, ".go": 100}}}
{"event": "index.progress", "data": {"repo": "my-project", "worktree": "main", "phase": "indexing", "indexed": 1000, "total": 2103, "files_by_ext": {".py": 510, ".ts": 260, ".go": 230}}}
{"event": "index.phase", "data": {"repo": "my-project", "worktree": "main", "phase": "resolving_cross_file"}}
{"event": "index.phase", "data": {"repo": "my-project", "worktree": "main", "phase": "encoding_splade"}}
{"event": "index.progress", "data": {"repo": "my-project", "worktree": "main", "phase": "encoding_splade", "indexed": 800, "total": 2103}}
{"event": "index.complete", "data": {"repo": "my-project", "worktree": "main", "stats": {"files_processed": 2103, "files_added": 2103, "symbols_indexed": 18420, "duration_ms": 8340}}}
{"event": "freshness.fresh", "data": {"repo": "my-project", "worktree": "main"}}
{"event": "repo.activated", "data": {"repo": "my-project", "worktrees": ["main"]}}
{"event": "analysis.tier1.complete", "data": {"repo": "my-project", "worktree": "main", "files": 2103, "diagnostics": 47, "elapsed_ms": 210}}
{"event": "analysis.tier2.complete", "data": {"repo": "my-project", "worktree": "main", "affected_tests": 34, "passed": 31, "failed": 3, "elapsed_ms": 4200}}
```

#### SDK Event Subscription API

```python
sdk = CodeRecon()

# Callback-based: register handlers for specific event patterns
sdk.on("index.progress", lambda e: print(f"Indexing {e['indexed']}/{e['total']}"))
sdk.on("index.complete", lambda e: print(f"Done: {e['stats']['symbols_indexed']} symbols"))
sdk.on("freshness.*", lambda e: update_status_bar(e))

# Or subscribe to all events
sdk.on("*", lambda e: my_telemetry.log(e))

# Async iterator: consume events as a stream
async for event in sdk.events():
    if event.type == "index.progress":
        progress_bar.update(event.data["indexed"], event.data["total"])
    elif event.type == "index.complete":
        progress_bar.close()

# Filtered stream
async for event in sdk.events("index.*", "freshness.*"):
    ...
```

Both interfaces are available simultaneously. Callbacks fire on the background read loop; the async iterator yields from an internal `asyncio.Queue`.

#### Event Emission on the Daemon Side

The daemon's existing internal signals are bridged to stdio events via an `EventBus`:

```python
class EventBus:
    """Collects internal daemon signals and writes them as NDJSON events to stdout."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    async def emit(self, event: str, data: dict) -> None:
        line = json.dumps({"event": event, "data": data}, separators=(",", ":")) + "\n"
        self._writer.write(line.encode())
        await self._writer.drain()
```

Wired into existing hooks:

| Internal Signal | EventBus Binding |
|-----------------|------------------|
| `IndexCoordinatorEngine.on_index_progress` callback | `emit("index.progress", ...)` |
| `BackgroundIndexer.add_on_complete` callback | `emit("index.complete", ...)` |
| `FreshnessGate.mark_stale()` / `mark_fresh()` | `emit("freshness.stale\|fresh", ...)` |
| `FileWatcher.on_change` callback | `emit("watcher.changes", ...)` |
| `AnalysisPipeline.on_index_complete` → tier completion | `emit("analysis.tier{1,2}.complete", ...)` |
| `GlobalDaemon.activate_repo` / `_activate_worktree` | `emit("repo.activated", ...)` etc. |

The EventBus hooks are injected at daemon startup in stdio mode. In HTTP mode, they don't exist — the HTTP daemon doesn't need them (clients poll `/status` instead).

### 5.4 Session Management

Every request carries a `session_id` field. The daemon uses it to bind `SessionState` — the same object that MCP tools use today (candidate maps, mutation context, exclusive locks, read-only intent).

```json
{"id": "r1", "session_id": "s_abc123", "method": "recon", "params": {...}}
```

#### Why Sessions Matter

Not all tools are stateless. The `recon → refactor → checkpoint` workflow **accumulates state** within a session:

1. `recon` populates `candidate_maps` (which symbols are addressable) and sets `read_only` intent.
2. `refactor_rename` / `refactor_move` reads `candidate_maps` to resolve symbols, writes `pending_refactors` into `mutation_ctx`.
3. `checkpoint` reads `read_only` (warns if mutations happened in a read-only session), clears `mutation_ctx` on success.

Additionally, `checkpoint` and `semantic_diff` are **exclusive** — they acquire a per-session lock that blocks all other tool calls on the same session for their duration.

Stateless tools (`graph_*`, `recon_map`, `recon_understand`, `describe`, `raw_signals`) don't read or write session state. They still receive a session for uniform dispatch, but the session ID doesn't affect their behavior.

#### Session Scope: Per (repo, worktree) Pair

On the daemon side, `SessionManager` is **per-worktree** — each `WorktreeSlot` has its own instance. Two sessions with the same `session_id` sent to different worktrees create completely independent `SessionState` objects (different `SessionManager` instances).

The SDK's **default strategy** is one session per `(repo, worktree)` pair:

```python
# SDK auto-generates session IDs keyed by (repo, worktree)
# All calls to the same (repo, worktree) share one session

sdk = CodeRecon()
await sdk.start()

# These two calls share one session — recon populates candidate_maps,
# refactor reads them:
await sdk.recon(repo="myrepo", task="find auth middleware")
await sdk.refactor_rename(repo="myrepo", symbol="AuthMiddleware",
                          new_name="AuthGuard", justification="...")

# This goes to a different worktree → different session automatically:
await sdk.recon(repo="myrepo", task="find auth", worktree="feature-x")
```

Internally, the `CodeRecon` client maintains a `dict[tuple[str, str], str]` mapping `(repo_name, worktree)` → `session_id`. On the first call to a new `(repo, worktree)` pair, a session ID is generated (`"sess_" + 12 hex chars`). All subsequent calls to the same pair reuse it.

This works because:
- The daemon's `SessionManager` is per-worktree — natural alignment.
- The typical agent workflow operates on one worktree — state accumulates correctly.
- Different worktrees never share state — no cross-contamination.

#### Explicit Sessions: Multi-Agent Escape Hatch

The default strategy breaks when **multiple agents operate on the same worktree concurrently**. If two agents share one session:
- Agent A's `recon` populates `candidate_maps`. Agent B's `recon` overwrites them.
- Agent A's `refactor_rename` sees Agent B's candidates — wrong results.
- Agent A's `checkpoint` (exclusive) blocks Agent B's tools for its entire duration.

For this case, the integrator creates **explicit sessions**:

```python
# Two agents, same repo, same worktree — explicit sessions prevent interference
agent_a = sdk.session("agent-a")
agent_b = sdk.session("agent-b")

# Each gets its own candidate_maps, mutation_ctx, exclusive lock:
await agent_a.recon(repo="myrepo", task="find auth middleware")
await agent_b.recon(repo="myrepo", task="find caching layer")

# Refactors operate on their own candidate maps:
await agent_a.refactor_rename(repo="myrepo", symbol="AuthMiddleware", ...)
await agent_b.refactor_rename(repo="myrepo", symbol="CacheStore", ...)

# Checkpoints are exclusive per session — A's checkpoint doesn't block B:
await agent_a.checkpoint(repo="myrepo", ...)  # locks session "agent-a" only
await agent_b.checkpoint(repo="myrepo", ...)  # locks session "agent-b" only
```

`sdk.session(name)` returns a `SessionHandle` — a thin proxy where every tool method injects the given session ID instead of the auto-generated one. The `name` is used directly as the `session_id` (prefixed to avoid collisions: `"ext_{name}"`).

**Note**: Even with separate sessions, mutations on the same worktree are still serialized by the daemon's `MutationRouter` (per-worktree lock). Two concurrent `checkpoint` calls on the same worktree will queue, not fail. The session-level exclusive lock only prevents *other tools on the same session* from interleaving — the worktree-level lock prevents actual file system conflicts.

#### RepoHandle and Sessions

`RepoHandle` inherits the session strategy of whatever created it:

```python
# Default sessions (per repo+worktree):
project = sdk.repo("myrepo")
await project.recon(task="find auth")       # session for ("myrepo", "main")
await project.refactor_rename(...)          # same session — sees candidate_maps

# Explicit sessions via handle:
agent_a = sdk.session("agent-a").repo("myrepo")
await agent_a.recon(task="find auth")       # session "ext_agent-a" on ("myrepo", "main")
```

#### Session Lifecycle

| Event | Behavior |
|-------|----------|
| First call to `(repo, worktree)` | SDK generates session ID, daemon creates `SessionState` via `get_or_create()` |
| Subsequent calls | Same session ID reused, daemon calls `session.touch()` updating `last_active` |
| `sdk.stop()` / `async with` exit | SDK sends `session_close` for all active session IDs |
| SDK crash (no clean shutdown) | Daemon's idle timeout GCs sessions after 30 min (`cleanup_stale()`) |
| Integrator calls `sdk.close_session(repo, worktree)` | Explicit session teardown — clears candidate_maps, mutation_ctx on daemon side |
| Explicit session handle GC'd | No automatic cleanup — integrator should close explicitly, or rely on idle timeout |

The `session_close` request is a management method (like `register` / `catalog`):

```json
{"id": "r99", "method": "session_close", "params": {"session_id": "sess_abc123"}}
```

The daemon calls `session_manager.close(session_id)` — removes the `SessionState` immediately.

#### Wire Format

Every tool request includes `session_id`:

```json
{"id": "r1", "session_id": "sess_a1b2c3d4e5f6", "method": "recon", "params": {"repo": "myrepo", "task": "find auth"}}
{"id": "r2", "session_id": "sess_a1b2c3d4e5f6", "method": "refactor_rename", "params": {"repo": "myrepo", "symbol": "Foo", ...}}
```

Management methods (`register`, `catalog`, `status`, `session_close`) don't need `session_id`.

---

## 6. Daemon Side — Stdio Transport

### 6.1 New Daemon Mode

The global daemon gains a `--stdio` transport option. It replaces the HTTP listener with a stdio read/write loop. Everything else — catalog, `GlobalDaemon`, lazy activation, watchers, indexers, concurrency, sessions — is unchanged.

```
recon up --stdio          # Stdio mode (child process of SDK)
recon up --port 7654      # HTTP mode (existing, unchanged)
```

`--stdio` and `--port` are mutually exclusive. The daemon is still global, still multi-repo, still uses the catalog.

### 6.2 Implementation

```
src/coderecon/
├── daemon/
│   ├── global_lifecycle.py    # Existing: run_global_server() for HTTP
│   ├── stdio_transport.py     # NEW: stdio read/write loop
│   └── dispatch.py            # NEW: route method → tool handler
```

**`stdio_transport.py`** — Reads NDJSON from stdin, dispatches to tool handlers, writes NDJSON responses and events to stdout. All logging goes to stderr or file.

```python
async def run_stdio_loop(daemon: GlobalDaemon, registry: CatalogRegistry) -> None:
    """Main stdio event loop.
    
    Reads JSON requests from stdin, dispatches to the appropriate tool
    handler via the same AppContext/ops infrastructure as HTTP mode,
    writes JSON responses and events to stdout.
    
    Lifecycle:
    - Stdin EOF → graceful shutdown (stop_all)
    - SIGTERM → graceful shutdown
    - Unhandled exception in handler → error response (daemon stays alive)
    """
    reader = asyncio.StreamReader()
    await asyncio.get_event_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout.buffer
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, asyncio.get_event_loop())
    
    # Stdout is shared between RPC responses and events.
    # The write lock ensures NDJSON lines are never interleaved.
    write_lock = asyncio.Lock()
    
    async def write_message(msg: dict) -> None:
        async with write_lock:
            writer.write(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
    
    # Wire EventBus into daemon hooks (see §5.3)
    event_bus = EventBus(write_message)
    wire_event_hooks(daemon, event_bus)
    
    # Signal readiness
    await write_message({"event": "daemon.ready", "data": {
        "version": __version__,
        "repos": [r.name for r in registry.list_repos()],
    }})
    
    while True:
        line = await reader.readline()
        if not line:
            break  # EOF → shutdown
        request = json.loads(line)
        # Dispatch concurrently — multiple requests can be in flight
        asyncio.create_task(_handle_request(daemon, registry, request, write_message))
    
    await write_message({"event": "daemon.stopping", "data": {"reason": "eof"}})

async def _handle_request(daemon, registry, request, write_message):
    response = await dispatch(daemon, registry, request)
    await write_message(response)
```

**`dispatch.py`** — Routes `method` to the right core function. Uses the **shared `resolve_worktree()`** helper (see §6.6) for repo/worktree resolution — the same function that `_DynamicMcpRouter.handle()` will be refactored to use.

Critically, dispatch does **not** maintain a parallel handler layer. It calls the same extracted core functions that the MCP `register_tools()` wrappers call (see §6.7 for the prerequisite refactoring).

```python
from coderecon.daemon.resolve import resolve_worktree

async def dispatch(daemon: GlobalDaemon, registry: CatalogRegistry, request: dict) -> dict:
    method = request["method"]
    params = request.get("params", {})
    request_id = request.get("id")
    session_id = request.get("session_id")
    
    try:
        # Management methods (no repo context needed)
        if method == "register":
            return await handle_register(daemon, registry, params, request_id)
        if method == "catalog":
            return await handle_catalog(registry, request_id)
        if method == "status":
            return await handle_status(daemon, params, request_id)
        
        # Tool methods — resolve repo + worktree via shared helper
        repo_name = params.pop("repo")
        worktree = params.pop("worktree", "main")
        
        wt_slot = await resolve_worktree(daemon, repo_name, worktree)
        if wt_slot is None:
            return error_response(request_id, "NOT_FOUND",
                f"No repo '{repo_name}' / worktree '{worktree}'")
        
        app_ctx = wt_slot.app_ctx
        session = app_ctx.session_manager.get_or_create(session_id)
        
        # Call the SAME core function that MCP tools call
        core_fn = CORE_FUNCTIONS[method]
        result = await core_fn(app_ctx=app_ctx, session=session, **params)
        return success_response(request_id, result)
        
    except Exception as exc:
        return error_response(request_id, "INTERNAL", str(exc))
```

**`CORE_FUNCTIONS`** maps method names directly to the extracted core functions that already exist (or will be extracted as a prerequisite — see §6.7):

```python
from coderecon.mcp.tools.recon import recon_pipeline, recon_map_core, raw_signals_pipeline
from coderecon.mcp.tools.checkpoint import checkpoint_pipeline
from coderecon.mcp.tools.diff import semantic_diff_core
from coderecon.mcp.tools.refactor import (
    refactor_rename_core, refactor_move_core, refactor_commit_core,
    refactor_cancel_core, recon_impact_core,
)
from coderecon.mcp.tools.graph import (
    graph_cycles_core, graph_communities_core, graph_export_core,
    recon_understand_core,
)
from coderecon.mcp.tools.introspection import describe_core

CORE_FUNCTIONS: dict[str, CoreFn] = {
    "recon":              recon_pipeline,
    "recon_map":          recon_map_core,
    "raw_signals":        raw_signals_pipeline,
    "recon_impact":       recon_impact_core,
    "recon_understand":   recon_understand_core,
    "semantic_diff":      semantic_diff_core,
    "graph_cycles":       graph_cycles_core,
    "graph_communities":  graph_communities_core,
    "graph_export":       graph_export_core,
    "refactor_rename":    refactor_rename_core,
    "refactor_move":      refactor_move_core,
    "refactor_commit":    refactor_commit_core,
    "refactor_cancel":    refactor_cancel_core,
    "checkpoint":         checkpoint_pipeline,
    "describe":           describe_core,
}
```

There is **no `handlers.py`**. No parallel handler layer. The dispatch table points directly at the core functions that MCP tools also call. This is the critical DRY property.

### 6.6 Shared Repo/Worktree Resolution

The repo/worktree lazy-activation logic currently lives inline in `_DynamicMcpRouter.handle()`. The SDK dispatch needs the same logic. To avoid duplication, extract it into a shared function:

```python
# daemon/resolve.py — NEW (extracted from global_app.py)

async def resolve_worktree(
    daemon: GlobalDaemon,
    repo_name: str,
    worktree: str = "main",
) -> WorktreeSlot | None:
    """Resolve repo + worktree with lazy activation.
    
    Shared by both HTTP (_DynamicMcpRouter) and stdio (dispatch.py).
    Returns None if the repo or worktree can't be found/activated.
    """
    slot = daemon.get_slot(repo_name)
    if slot is None:
        slot = await daemon.lazy_activate_repo(repo_name)
    if slot is None:
        return None

    wt_slot = slot.worktrees.get(worktree)
    if wt_slot is None:
        wt_slot = await daemon.lazy_activate_worktree(repo_name, worktree)
    if wt_slot is None:
        return None

    wt_slot.last_request_at = time.time()
    return wt_slot
```

`_DynamicMcpRouter.handle()` is refactored to call `resolve_worktree()` instead of inlining the lookup. Zero behavior change, just extraction.

### 6.7 Prerequisite: Core Function Extraction (DRY)

The SDK and MCP must call the **same** core functions. Today, some tool modules already have clean extracted core functions; others have business logic inlined inside `@mcp.tool()` closures. Before building the stdio transport, each module needs a core function with the signature:

```python
async def tool_core(app_ctx: AppContext, session: SessionState, **tool_params) -> dict
```

Current state by module:

| Module | Core function exists? | Work needed |
|--------|----------------------|-------------|
| `mcp/tools/recon/pipeline.py` | ✅ `recon_pipeline()` | None — already extracted |
| `mcp/tools/recon/raw_signals.py` | ✅ `raw_signals_pipeline()` | None — already extracted |
| `mcp/tools/refactor.py` | ✅ Ops layer is clean | Extract thin `refactor_rename_core()` etc. that do session lookup + ops call + serialization (~5 lines each) |
| `mcp/tools/graph.py` | ✅ Ops imports are clean | Extract `graph_cycles_core()` etc. (~10 lines each) |
| `mcp/tools/diff.py` | ✅ Helpers extracted | Extract `semantic_diff_core()` that dispatches to `_run_git_diff` / `_run_epoch_diff` (~15 lines) |
| `mcp/tools/checkpoint.py` | ❌ **400+ lines inlined** | **Extract `checkpoint_pipeline()`** — the lint→test→commit orchestration. This is the only significant extraction. |
| `mcp/tools/introspection.py` | ✅ Trivial | Extract `describe_core()` (~10 lines) |

After extraction, each MCP `@mcp.tool()` closure becomes:

```python
@mcp.tool(...)
async def checkpoint(ctx: Context, changed_files: list[str], ...):
    session = app_ctx.session_manager.get_or_create(ctx.session_id)
    return await checkpoint_pipeline(app_ctx=app_ctx, session=session,
        changed_files=changed_files, ...)
```

The stdio dispatch table (`CORE_FUNCTIONS`) points at the same `checkpoint_pipeline`. **One function, two callers, zero duplication.**

### 6.3 Logging Constraint

In stdio mode, **stdout is the protocol channel**. All logging MUST go to stderr or a log file. The daemon's logging config is adjusted at startup:

```python
if stdio_mode:
    configure_logging(config=LoggingConfig(
        level=log_level,
        outputs=[
            LogOutputConfig(destination="stderr", format="json", level=log_level),
        ],
    ))
```

### 6.4 PID File Behavior

In stdio mode, **no PID file is written**. The integrator owns the process — it knows the PID (it spawned it). PID files are for the HTTP daemon case where clients need to discover a running daemon.

### 6.5 CLI Surface

```
# Existing — HTTP daemon (unchanged)
recon up [--port PORT] [--dev]
recon down
recon status

# New — stdio daemon (child process mode)
recon up --stdio [--log-file PATH] [--log-level LEVEL] [--dev]
```

`--stdio` makes the daemon read from stdin, write to stdout, log to stderr. No port binding, no PID files. The process exits when stdin closes (parent died) or on SIGTERM.

---

## 7. SDK Client Implementation

```
src/coderecon/
├── sdk/
│   ├── __init__.py         # Exports: CodeRecon, RepoHandle, SessionHandle, result types
│   ├── client.py           # CodeRecon class — spawn daemon, stdio RPC, session management
│   ├── handle.py           # RepoHandle, SessionHandle — bound convenience objects
│   ├── types.py            # Result dataclasses
│   ├── protocol.py         # Stdio JSON wire format: encode, decode, correlate
│   └── frameworks.py       # as_openai_tools(), as_langchain_tools()
```

### 7.1 `CodeRecon` class

```python
class CodeRecon:
    """CodeRecon SDK — spawns the global daemon and exposes tools as callables."""
    
    def __init__(
        self,
        *,
        binary: str | None = None,
        home: str | Path | None = None,
    ) -> None: ...
    
    async def start(self) -> None:
        """Spawn the daemon child process. Blocks until daemon.ready event."""
    
    async def stop(self) -> None:
        """Send session_close for all active sessions, then shut down the daemon."""
    
    # ── Session management (see §5.4) ──
    
    def session(self, name: str) -> SessionHandle:
        """Create an explicit named session for multi-agent scenarios.
        
        Returns a SessionHandle with the same tool methods as CodeRecon,
        but all calls use session_id "ext_{name}" instead of the auto-generated one.
        """
    
    async def close_session(self, repo: str, worktree: str = "main") -> None:
        """Explicitly close the auto-generated session for a (repo, worktree) pair.
        
        Clears candidate_maps, mutation_ctx, and exclusive locks on the daemon side.
        A new session is auto-created on the next call to that (repo, worktree).
        """
    
    def repo(self, name: str, worktree: str = "main") -> RepoHandle:
        """Return a repo-bound handle with pre-bound tool methods."""
    
    # ── Event subscription (see §5.3) ──
    def on(self, pattern: str, callback: Callable[[Event], None]) -> None:
        """Register a callback for events matching a glob pattern.
        
        Patterns: "index.progress", "index.*", "freshness.*", "*"
        """
    
    def events(self, *patterns: str) -> AsyncIterator[Event]:
        """Async iterator over daemon events, optionally filtered by pattern."""
    
    # ── Tool methods (each sends RPC to daemon) ──
    async def recon(self, repo: str, task: str, ...) -> ReconResult: ...
    async def recon_map(self, repo: str, ...) -> MapResult: ...
    async def recon_impact(self, repo: str, ...) -> ImpactResult: ...
    async def recon_understand(self, repo: str, ...) -> UnderstandResult: ...
    async def semantic_diff(self, repo: str, ...) -> DiffResult: ...
    async def graph_cycles(self, repo: str, ...) -> CyclesResult: ...
    async def graph_communities(self, repo: str, ...) -> CommunitiesResult: ...
    async def graph_export(self, repo: str, ...) -> GraphExportResult: ...
    async def refactor_rename(self, repo: str, ...) -> RefactorResult: ...
    async def refactor_move(self, repo: str, ...) -> RefactorResult: ...
    async def refactor_commit(self, repo: str, ...) -> RefactorCommitResult: ...
    async def refactor_cancel(self, repo: str, ...) -> RefactorCancelResult: ...
    async def checkpoint(self, repo: str, ...) -> CheckpointResult: ...
    async def describe(self, ...) -> DescribeResult: ...
    
    # ── Daemon management ──
    async def register(self, path: str | Path) -> RegisterResult: ...
    async def unregister(self, path: str | Path) -> bool: ...
    async def catalog(self) -> list[CatalogEntry]: ...
    async def status(self, repo: str | None = None) -> StatusResult: ...
    async def reindex(self, repo: str, worktree: str = "main") -> None: ...
    
    # ── Framework adapters ──
    def as_openai_tools(self, repo: str, ...) -> list[dict]: ...
    def as_langchain_tools(self, repo: str, ...) -> list[StructuredTool]: ...
    
    # ── Context manager ──
    async def __aenter__(self) -> CodeRecon: ...
    async def __aexit__(self, *exc) -> None: ...
```

### 7.2 Process Lifecycle

```python
async def start(self) -> None:
    cmd = [self._binary or "recon", "up", "--stdio"]
    if self._home:
        cmd.extend(["--home", str(self._home)])
    
    self._process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    # Start background reader for stdout (responses + notifications)
    self._reader_task = asyncio.create_task(self._read_loop())
    
    # Wait for daemon ready signal
    await self._wait_ready(timeout=30.0)

async def stop(self) -> None:
    # Close all active sessions before shutdown
    for session_id in list(self._sessions.values()):
        try:
            await self._call("session_close", {"session_id": session_id},
                             session_id=None)
        except Exception:
            pass  # Best effort — daemon may already be exiting
    
    if self._process and self._process.returncode is None:
        self._process.stdin.close()                    # EOF → daemon shuts down
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.terminate()
            await self._process.wait()
    self._reader_task.cancel()
```

### 7.3 Session ID Resolution

```python
# Internal state:
#   _sessions: dict[tuple[str, str], str]  — (repo, worktree) → session_id
#   _explicit_session: str | None          — set on SessionHandle, None on CodeRecon

def _resolve_session_id(self, repo: str, worktree: str) -> str:
    """Get or create the session ID for this (repo, worktree) pair.
    
    If this is a SessionHandle (explicit session), always returns that handle's
    fixed session ID. Otherwise, auto-generates per (repo, worktree).
    """
    if self._explicit_session is not None:
        return self._explicit_session
    
    key = (repo, worktree)
    if key not in self._sessions:
        self._sessions[key] = f"sess_{secrets.token_hex(6)}"
    return self._sessions[key]
```

### 7.4 RPC Internals

```python
async def _call(self, method: str, params: dict,
                session_id: str | None = _SENTINEL) -> dict:
    """Send a request to the daemon and await the response.
    
    session_id is auto-resolved from (repo, worktree) in params unless
    explicitly passed (e.g., for management methods that don't need one).
    """
    request_id = self._next_id()
    request = {"id": request_id, "method": method, "params": params}
    
    # Attach session_id for tool methods (not management methods)
    if session_id is _SENTINEL:
        repo = params.get("repo")
        worktree = params.get("worktree", "main")
        if repo is not None:
            request["session_id"] = self._resolve_session_id(repo, worktree)
    elif session_id is not None:
        request["session_id"] = session_id
    
    future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
    self._pending[request_id] = future
    
    line = json.dumps(request, separators=(",", ":")) + "\n"
    self._process.stdin.write(line.encode())
    await self._process.stdin.drain()
    
    response = await asyncio.wait_for(future, timeout=self._timeout)
    
    if "error" in response:
        raise ReconError(response["error"]["code"], response["error"]["message"])
    
    return response["result"]

async def _read_loop(self) -> None:
    """Background task reading daemon stdout.
    
    Every line from stdout is either:
    - An RPC response (has "id") → resolve the pending future
    - An event (has "event") → dispatch to callbacks and event queue
    """
    while True:
        line = await self._process.stdout.readline()
        if not line:
            break  # Daemon exited
        msg = json.loads(line)
        
        if "event" in msg:
            event = Event(type=msg["event"], data=msg.get("data", {}))
            # Fire registered callbacks (pattern-matched)
            for pattern, callback in self._callbacks:
                if _matches_pattern(pattern, event.type):
                    try:
                        callback(event)
                    except Exception:
                        pass  # Callbacks must not break the read loop
            # Push to async iterator queues
            for pattern, queue in self._event_queues:
                if _matches_pattern(pattern, event.type):
                    queue.put_nowait(event)
        elif "id" in msg:
            # Response to a pending request
            future = self._pending.pop(msg["id"], None)
            if future and not future.done():
                future.set_result(msg)
```

### 7.4 Each Tool Method

Every tool method is a thin typed wrapper around `_call`:

```python
async def recon(
    self,
    repo: str,
    task: str,
    seeds: list[str] | None = None,
    pins: list[str] | None = None,
    worktree: str = "main",
) -> ReconResult:
    """Search the repository index for code relevant to a task.
    
    Args:
        repo: Registered repository name.
        task: Natural language description of what you're looking for.
        seeds: Explicit file paths or symbol names to seed the search.
        pins: File paths to always include in results.
        worktree: Git worktree name.
    
    Returns:
        Ranked code spans with relevance scores.
    """
    raw = await self._call("recon", {
        "repo": repo,
        "task": task,
        "seeds": seeds or [],
        "pins": pins or [],
        "worktree": worktree,
    })
    return ReconResult.from_dict(raw)
```

The docstring, type annotations, and parameter names are what agent frameworks consume to generate tool schemas.

### 7.6 `SessionHandle`

Returned by `sdk.session(name)`. Same tool methods as `CodeRecon`, but all calls use a fixed session ID.

```python
class SessionHandle:
    """Explicit session for multi-agent isolation.
    
    Every tool call from this handle uses session_id "ext_{name}",
    bypassing the auto-generated per-(repo, worktree) strategy.
    """
    
    def __init__(self, client: CodeRecon, name: str) -> None:
        self._client = client
        self._explicit_session = f"ext_{name}"
    
    def repo(self, name: str, worktree: str = "main") -> RepoHandle:
        """Repo-bound handle that inherits this explicit session."""
    
    # All tool methods delegate to client._call with self._explicit_session:
    async def recon(self, repo: str, ...) -> ReconResult: ...
    async def refactor_rename(self, repo: str, ...) -> RefactorResult: ...
    async def checkpoint(self, repo: str, ...) -> CheckpointResult: ...
    # ... (same surface as CodeRecon)
    
    async def close(self) -> None:
        """Send session_close for this explicit session."""
        await self._client._call("session_close",
            {"session_id": self._explicit_session}, session_id=None)
```

`SessionHandle` does NOT have `start()`, `stop()`, `on()`, `events()`, `register()`, or `catalog()` — those are daemon-level operations, not session-scoped.

---

## 8. Result Types

Stable, public dataclasses in `sdk/types.py`. These are the SDK's contract — they don't leak internal model classes.

```python
@dataclass(frozen=True)
class CodeSpan:
    file: str               # Relative path from repo root
    start_line: int
    end_line: int
    content: str            # Source text (or signature if truncated)
    symbol: str | None      # Symbol name if applicable
    score: float            # Relevance score (0.0–1.0)

@dataclass(frozen=True)
class ReconResult:
    recon_id: str
    spans: list[CodeSpan]
    metrics: dict[str, Any]

@dataclass(frozen=True)
class MapResult:
    structure: dict | None
    languages: list[dict] | None
    entry_points: list[dict] | None
    dependencies: dict | None
    test_layout: dict | None
    public_api: list[dict] | None

@dataclass(frozen=True)
class ImpactResult:
    references: list[dict]
    total_references: int
    files_affected: int
    summary: str

@dataclass(frozen=True)
class RefactorResult:
    refactor_id: str
    status: str             # "pending", "previewed", "applied", "cancelled"
    preview: dict | None
    agentic_hint: str | None

@dataclass(frozen=True)
class RefactorCommitResult:
    refactor_id: str
    applied: bool
    files_modified: list[str]
    inspection: dict | None  # Non-None when inspect_path was given

@dataclass(frozen=True)
class RefactorCancelResult:
    refactor_id: str
    cancelled: bool

@dataclass(frozen=True)
class DiffResult:
    base_description: str
    target_description: str
    structural_changes: list[dict]
    scope: dict | None

@dataclass(frozen=True)
class CyclesResult:
    level: str
    cycles: list[dict]
    summary: str

@dataclass(frozen=True)
class CommunitiesResult:
    level: str
    communities: list[dict]
    summary: str

@dataclass(frozen=True)
class GraphExportResult:
    path: str
    message: str

@dataclass(frozen=True)
class CheckpointResult:
    passed: bool
    lint: dict | None
    tests: dict | None
    commit: dict | None
    summary: str
    agentic_hint: str

@dataclass(frozen=True)
class DescribeResult:
    found: bool
    description: str | None

@dataclass(frozen=True)
class CatalogEntry:
    name: str
    git_dir: str
    active: bool
    worktrees: list[dict]

@dataclass(frozen=True)
class RegisterResult:
    repo: str
    worktree: str

@dataclass(frozen=True)
class StatusResult:
    daemon_healthy: bool
    repos: list[dict]

@dataclass(frozen=True)
class RawSignalsResult:
    """Raw retrieval signals for training and evaluation.
    
    Contains the full feature matrix for every candidate definition
    returned by the harvester pipeline, BEFORE ranking model application.
    Used by recon-lab for model training, evaluation, and data collection.
    """
    query_features: dict[str, Any]      # query_len, has_identifier, intent, term_count, ...
    repo_features: dict[str, Any]       # object_count, file_count
    candidates: list[dict[str, Any]]    # 50+ fields per candidate (see below)
    diagnostics: dict[str, Any]         # elapsed_ms, candidate_count, per-harvester hit counts

# Candidate fields (per entry in candidates[]):
#   Identity:    def_uid, path, kind, name, lexical_path, qualified_name
#   Structural:  start_line, end_line, object_size_lines, nesting_depth,
#                has_docstring, docstring, signature_text
#   Metadata:    language_family, is_test, is_barrel, is_endpoint,
#                hub_score, test_coverage_count
#   Signals:     from_term_match, from_explicit, from_graph, from_coverage,
#                term_match_count, lex_hit_count, bm25_file_score,
#                splade_score, graph_edge_type, graph_seed_rank,
#                import_direction, symbol_source
#   Computed:    retriever_hits, seed_path_distance, same_package,
#                package_distance, shares_file_with_seed,
#                is_callee_of_top, is_imported_by_top, rrf_score

@dataclass(frozen=True)
class Event:
    """A daemon-initiated event (see §5.3 for full event catalog)."""
    type: str               # e.g. "index.progress", "freshness.fresh"
    data: dict[str, Any]    # Event-specific payload
```

---

## 9. Framework Adapters

### 9.1 OpenAI Function Calling

```python
def as_openai_tools(self, repo: str, worktree: str = "main") -> list[dict]:
    """Generate OpenAI-compatible tool definitions with repo pre-bound.
    
    Returns a list of dicts suitable for the `tools` parameter of
    `chat.completions.create()`. Each tool's function is pre-bound
    to the given repo/worktree.
    """
```

Generates JSON schemas from the typed signatures. The `repo` and `worktree` params are pre-bound and excluded from the schema — the agent sees only the tool-relevant parameters.

### 9.2 LangChain

```python
def as_langchain_tools(self, repo: str, worktree: str = "main") -> list[StructuredTool]:
    """Generate LangChain StructuredTool instances with repo pre-bound."""
```

### 9.3 Generic

For any framework that takes `(name, description, schema, callable)`:

```python
def tool_definitions(self, repo: str, worktree: str = "main") -> list[ToolDef]:
    """Return framework-agnostic tool definitions.
    
    Each ToolDef has:
      - name: str
      - description: str
      - parameters_schema: dict (JSON Schema)
      - call: async (dict) -> dict
    """
```

---

## 10. Error Handling

```python
class ReconError(Exception):
    """Base error for all SDK errors."""
    code: str
    message: str

class RepoNotFound(ReconError): ...
class WorktreeNotFound(ReconError): ...
class ToolError(ReconError): ...          # Tool execution failed
class DaemonError(ReconError): ...        # Daemon process died or unreachable
class TimeoutError(ReconError): ...       # RPC timeout
```

Error codes from the daemon map directly to exception subclasses. The SDK never exposes raw protocol errors.

---

## 11. Packaging

### 11.1 Install Targets

```toml
[project.optional-dependencies]
# Full install — daemon + SDK + everything
# (default: `pip install coderecon` or `uv add coderecon`)

# Client-only: just the SDK client, no daemon dependencies.
# For integrators who install the daemon separately or have it in PATH.
sdk-client = []  # Zero extra deps — only stdlib (json, asyncio, subprocess)
```

The SDK client (`coderecon.sdk.client`) uses only stdlib. No `mcp`, no `fastmcp`, no `starlette`. This means integrators can `pip install coderecon` and import just the SDK without pulling in the full daemon dependency tree — provided `recon` is on PATH.

### 11.2 Package Exports

```python
# src/coderecon/__init__.py
from coderecon.sdk.client import CodeRecon
from coderecon.sdk.handle import RepoHandle

__all__ = ["CodeRecon", "RepoHandle"]
```

---

## 12. Interaction With Existing HTTP Daemon

The SDK and the HTTP daemon share all internal code but use different transports:

| Aspect | HTTP Daemon (`recon up`) | SDK Daemon (`recon up --stdio`) |
|--------|--------------------------|--------------------------------|
| Transport | Starlette + uvicorn + streamable-http | Async stdio NDJSON |
| Discovery | PID + port files | Parent process holds pipes |
| Client | MCP client (any language) | `CodeRecon` Python class (or raw stdio) |
| Routing | URL path: `/repos/{name}/worktrees/{wt}/mcp/...` | JSON field: `"repo"`, `"worktree"` |
| Session binding | MCP session ID (per connection) | `"session_id"` in request — auto per (repo, worktree), or explicit via `SessionHandle` (§5.4) |
| Events / Progress | Clients poll `GET /status` | Streamed as NDJSON events on stdout (§5.3) |
| Lifecycle | Persistent service | Child process of integrator |
| Multi-client | Yes (HTTP) | Single client (stdin owner) |

Both modes use the exact same `GlobalDaemon`, `CatalogRegistry`, `RepoSlot`, `WorktreeSlot`, `AppContext`, ops classes, and tool logic. The only difference is the transport layer that sits in front.

---

## 13. Implementation Plan

### Phase 0 — Prerequisite: Core Function Extraction

Before any new files are created, extract core functions from MCP tool modules (§6.7). This is a refactor of existing code with zero behavior change:

| Component | File | Description |
|-----------|------|-------------|
| Resolve helper | `daemon/resolve.py` | Extract `resolve_worktree()` from `_DynamicMcpRouter.handle()` |
| Checkpoint core | `mcp/tools/checkpoint.py` | Extract `checkpoint_pipeline()` from inlined `@mcp.tool` closure |
| Refactor cores | `mcp/tools/refactor.py` | Extract `refactor_rename_core()`, `refactor_move_core()`, etc. |
| Diff core | `mcp/tools/diff.py` | Extract `semantic_diff_core()` |
| Graph cores | `mcp/tools/graph.py` | Extract `graph_cycles_core()`, `recon_understand_core()`, etc. |
| Introspection core | `mcp/tools/introspection.py` | Extract `describe_core()` |
| HTTP router refactor | `daemon/global_app.py` | Refactor `_DynamicMcpRouter.handle()` to call `resolve_worktree()` |

**Validation**: All existing MCP tests must pass unchanged after extraction.

### Phase 1 — Stdio Transport in Daemon

| Component | File | Description |
|-----------|------|-------------|
| Stdio read/write loop | `daemon/stdio_transport.py` | NDJSON over async stdin/stdout, write lock for interleaved events+responses |
| Event bus | `daemon/event_bus.py` | `EventBus` class + `wire_event_hooks()` — bridges internal signals (indexer callbacks, FreshnessGate transitions, watcher changes, analysis pipeline) to NDJSON events on stdout |
| Request dispatch | `daemon/dispatch.py` | Method → `CORE_FUNCTIONS` table routing, calls `resolve_worktree()`, session binding. **No parallel handler layer** — points directly at extracted core functions |
| CLI flag | `cli/up.py` | `--stdio` flag on `recon up` |
| Lifecycle | `daemon/global_lifecycle.py` | `run_global_server_stdio()` alongside existing `run_global_server()` |

### Phase 2 — SDK Client

| Component | File | Description |
|-----------|------|-------------|
| Protocol layer | `sdk/protocol.py` | NDJSON encode/decode, request correlation, event/response demux |
| Event subscription | `sdk/events.py` | `on()` callback registration, glob pattern matching, `events()` async iterator with `asyncio.Queue` fan-out |
| CodeRecon client | `sdk/client.py` | Process spawn, typed tool methods, lifecycle, `_read_loop` dispatching events + responses |
| RepoHandle | `sdk/handle.py` | Repo-bound convenience wrapper |
| Result types | `sdk/types.py` | Public frozen dataclasses including `Event` |
| Package exports | `__init__.py` | Top-level exports |

### Phase 3 — Framework Adapters

| Component | File | Description |
|-----------|------|-------------|
| OpenAI adapter | `sdk/frameworks.py` | `as_openai_tools()` |
| LangChain adapter | `sdk/frameworks.py` | `as_langchain_tools()` |
| Generic adapter | `sdk/frameworks.py` | `tool_definitions()` |
| Schema generation | `sdk/frameworks.py` | JSON Schema from type annotations |

### Phase 4 — Tests & Docs

| Component | Location | Description |
|-----------|----------|-------------|
| Protocol tests | `tests/sdk/test_protocol.py` | NDJSON encoding, correlation, error handling |
| Client tests | `tests/sdk/test_client.py` | Spawn, tool calls, shutdown, crash recovery |
| Integration | `tests/integration/test_sdk_roundtrip.py` | Full spawn → register → recon → refactor → checkpoint flow |
| Framework tests | `tests/sdk/test_frameworks.py` | Schema generation, tool binding |
| Docs | `docs/sdk.md` | Usage guide |

---

## 14. Open Questions

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | If an HTTP daemon is already running when `sdk.start()` is called, should the SDK (a) refuse, (b) connect to the HTTP daemon, or (c) spawn stdio daemon anyway (coexist)? | **(c)** — coexist. They share the catalog DB but have independent process lifecycles. The stdio daemon reads the same catalog. |
| 2 | ~~Should the dispatch layer share code with the MCP tool registration?~~ | **Resolved (§6.7)** — Extract core functions from MCP tool modules. Both MCP `@mcp.tool()` wrappers and stdio `CORE_FUNCTIONS` dispatch table call the same functions. No parallel handler layer. |
| 3 | Should `as_openai_tools()` / `as_langchain_tools()` live in the core package or in optional extras? | **Core** for `as_openai_tools()` (schema-only, no import needed). **Optional** for `as_langchain_tools()` (requires `langchain-core` import). |
| 4 | Should the protocol support request cancellation? | **Not in v1** — add later if needed. The daemon can be killed. Individual long-running ops (reindex) can be interrupted via `SIGTERM` to the daemon. |
| 5 | Should the daemon support concurrent requests over stdio, or serial? | **Concurrent** — the SDK may have multiple agents or multiple pending calls. Use `id` for correlation. The daemon runs handlers concurrently via `asyncio.create_task`. |
| 6 | Ready signal: what does the daemon write to stdout when it's ready to accept requests? | `{"event": "daemon.ready", "data": {"version": "...", "repos": [...]}}` — the SDK waits for this before returning from `start()`. |
