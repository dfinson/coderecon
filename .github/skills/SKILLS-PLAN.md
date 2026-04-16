# CodeRecon Skills Plan

## Problem

Agents operating on this codebase continuously make wrong assumptions because the repo
is large and context-rich. Examples: assuming "only Python test runner," assuming
refactors apply immediately, assuming the index is always fresh, assuming config uses
single underscores. Skills solve this by packaging domain knowledge that agents load
on-demand when working in a specific area.

## Audit Methodology

Five parallel deep audits covering every `src/coderecon/` module, the test suite, CI/CD,
recon-lab, and all documentation. Each audit catalogued files, key classes, non-obvious
constraints, and specific wrong assumptions an agent would make.

---

## Proposed Skills (12)

Skills are organized by *task domain*—the kind of work an agent is doing—not by source
module. This means one skill may reference multiple modules.

---

### 1. `indexing` — Working with the Index Subsystem

**Trigger**: Agent is asked to modify indexing, add a language, change fact extraction, or
debug index freshness.

**Wrong assumptions this prevents**:
- "Index is just text search" → Two-tier: Tantivy (Tier 0 lexical) + SQLite/Tree-sitter (Tier 1 structural)
- "All languages get full indexing" → Structural indexing only for languages with Tree-sitter grammars in `core/languages.py`; others get lexical-only
- "RefTier improves at query time" → RefTier is immutable; classified at index time only
- "Index reflects current disk" → Freshness model: CLEAN/DIRTY/STALE/PENDING_CHECK/UNINDEXED; must reconcile
- "One reconcile at a time" is a bug → It's by design; violating corrupts RepoState

**Key references to bundle**:
- `src/coderecon/index/models.py` — 60+ enums and fact tables (DefFact, RefFact, ScopeFact, ImportFact, etc.)
- `src/coderecon/index/ops.py` — IndexCoordinatorEngine lifecycle and pipeline
- `src/coderecon/core/languages.py` — Canonical language definitions, grammar mapping
- `src/coderecon/index/_internal/` — Parsing, discovery, state, DB layers
- `docs/SPEC.md` sections on indexing

**Estimated SKILL.md size**: ~300 lines + 2 reference files

---

### 2. `testing` — Test Discovery, Execution, and Runner Packs

**Trigger**: Agent works on test infrastructure, adds a runner pack, debugs test failures,
or modifies test discovery/execution.

**Wrong assumptions this prevents**:
- "Only Python/pytest supported" → 31+ runners: Tier 1 (pytest, jest, vitest, go test, nextest, gradle, maven, dotnet, ctest, rspec, minitest, phpunit) + Tier 2 (kotlin, swift, sbt, dart, bats, pester, busted, mix, cabal, julia, dune)
- "All runners give per-test results" → Output fidelity varies: JUnit XML (full) vs. Coarse (aggregate only)
- "For Rust use cargo test" → Must use `cargo-nextest` for full fidelity
- "Test targets are always file paths" → Target kinds: file, package, project (runner-specific)
- "Runtime re-detected per execution" → ContextRuntime captured once at discovery; persisted to SQLite
- "Parallelism is unlimited" → `min(cpu_count * 2, 16)` workers; 300s per-target timeout

**Key references to bundle**:
- `src/coderecon/testing/runner_pack.py` — RunnerPack base class and plugin interface
- `src/coderecon/testing/packs/tier1.py` — 20 Tier-1 runner definitions
- `src/coderecon/testing/packs/tier2.py` — 11 Tier-2 runner definitions
- `src/coderecon/testing/ops.py` — TestOps orchestrator
- `src/coderecon/testing/models.py` — TestTarget, TestRunStatus, TestResult
- `src/coderecon/testing/runtime.py` — ContextRuntime specification

**Estimated SKILL.md size**: ~350 lines + 1 reference (runner table)

---

### 3. `linting` — Lint Tool Detection, Execution, and Parsing

**Trigger**: Agent works on lint infrastructure, adds a tool definition, debugs lint
failures, or modifies tool detection/parsing.

**Wrong assumptions this prevents**:
- "Lint tools auto-detected by installation" → Detection via config file presence only; installed-but-unconfigured = invisible
- "All tools use JSON output" → Some use SARIF, some use custom/regex parsing
- "Tools run in shell" → Subprocess list, not shell (safer, deterministic)
- "Section-aware TOML" → `pyproject.toml:tool.ruff` checks for `[tool.ruff]` section, not just file existence
- "dry_run and fix are the same flow" → Different flag sets per tool (`--check` vs `--fix`, `--diff` vs `--write`)
- "Paths always go at end" → `paths_position`: end, after_executable, or none (tool-specific)

**Key references to bundle**:
- `src/coderecon/lint/definitions.py` — 40+ tool registrations
- `src/coderecon/lint/tools.py` — LintTool dataclass and ToolRegistry
- `src/coderecon/lint/ops.py` — LintOps orchestrator
- `src/coderecon/lint/parsers.py` — Output parsers per format

**Estimated SKILL.md size**: ~250 lines + 1 reference (tool table)

---

### 4. `mcp-tools` — MCP Server, Tool Implementations, and Session Model

**Trigger**: Agent works on MCP tool definitions, modifies tool behavior, debugs session
state, or changes delivery/middleware.

**Wrong assumptions this prevents**:
- "Refactor tools apply directly" → Preview-before-apply pattern; must call `refactor_commit(refactor_id)` to apply
- "recon() can be called repeatedly" → Hard-gated to 1 call per task; 2nd call blocked unconditionally
- "All tools run concurrently" → `checkpoint` and `semantic_diff` hold exclusive lock; other tools block
- "Session state persists across connections" → Each MCP connection = isolated session
- "Impact analysis returns a refactor_id" → `recon_impact()` is read-only; no refactor_id returned
- "Responses are unlimited size" → >30KB trimmed from bottom-up (inline-only delivery)
- "Docket task queue is active" → Disabled via monkey-patch to eliminate 15% idle CPU

**Key references to bundle**:
- `src/coderecon/mcp/server.py` — FastMCP server creation
- `src/coderecon/mcp/session.py` — SessionState, SessionManager, exclusive locking
- `src/coderecon/mcp/tools/` — All tool implementations
- `src/coderecon/mcp/delivery.py` — Response trimming rules
- `src/coderecon/mcp/context.py` — AppContext (all ops + infrastructure)

**Estimated SKILL.md size**: ~400 lines + 2 references (tool table, session lifecycle)

---

### 5. `refactoring` — Rename, Move, Impact Analysis, and Certainty Model

**Trigger**: Agent works on the refactoring engine, modifies certainty scoring, changes
comment scanning, or debugs refactor divergence.

**Wrong assumptions this prevents**:
- "Refactoring is regex-based" → Index-based discovery using DefFact/RefFact with certainty tiers
- "All matches are equal" → HIGH (PROVEN/STRONG ref), MEDIUM (ANCHORED), LOW (UNKNOWN) — low requires inspection
- "Comments are ignored" → `include_comments=True` triggers separate lexical scan per language
- "path:line:col is a valid symbol" → Detected and extracted via regex; issues warning; may fail
- "Previews persist forever" → In-memory `_pending` dict; not persisted; cleared on apply/cancel/timeout
- "Move is just rename" → Move handles file relocation + import rewriting

**Key references to bundle**:
- `src/coderecon/refactor/ops.py` — RefactorOps (rename, move, impact, inspect, apply)
- `src/coderecon/refactor/__init__.py` — RefactorPreview, RefactorResult, EditHunk models
- `src/coderecon/index/models.py` — RefTier enum (PROVEN/STRONG/ANCHORED/UNKNOWN)

**Estimated SKILL.md size**: ~250 lines

---

### 6. `mutation` — Atomic File Edits and Delta Tracking

**Trigger**: Agent works on mutation operations, modifies the delta computation, or
debugs write failures.

**Wrong assumptions this prevents**:
- "Edits apply partially if some fail" → Atomic: ALL validated before ANY apply; entire operation aborts on failure
- "dry_run modifies files" → `dry_run=True` computes deltas only; `applied=False, changed_paths=[]`
- "Hashes are full SHA256" → SHA256[:12] (first 12 hex chars)
- "Line counting is diff-based" → `insertions = max(0, new - old)`, `deletions = max(0, old - new)`
- "Mutation triggers reindex" → NOT implemented in module; caller must handle

**Key references to bundle**:
- `src/coderecon/mutation/ops.py` — MutationOps, Edit, FileDelta, MutationDelta, MutationResult

**Estimated SKILL.md size**: ~150 lines

---

### 7. `config` — Configuration System, Loading, and Environment Overrides

**Trigger**: Agent works on configuration, adds a config field, modifies loading logic,
or debugs config precedence issues.

**Wrong assumptions this prevents**:
- "Env var prefix is CODERECON_" → Prefix is `CODEPLANE__` with double-underscore nesting
- "Typos in env vars raise errors" → Silently ignored; defaults used
- "All config is user-facing" → UserConfig exposes only port, max_file_size_mb, log_level; most fields internal
- "WSL needs manual config" → Cross-filesystem auto-detected; index moved to native Linux path transparently
- "max_workers > 1 is better" → Causes SQLite contention; 1 is safe default
- "verbose_errors is harmless" → SECURITY RISK: leaks code paths

**Key references to bundle**:
- `src/coderecon/config/models.py` — All Pydantic config models
- `src/coderecon/config/loader.py` — Precedence merging, YAML source
- `src/coderecon/config/constants.py` — Hard API limits (non-configurable)
- `docs/configuration.md`

**Estimated SKILL.md size**: ~250 lines + 1 reference (config schema)

---

### 8. `git-ops` — Git Integration Layer

**Trigger**: Agent works on git operations, modifies commit/merge/rebase flows, adds
worktree support, or debugs git errors.

**Wrong assumptions this prevents**:
- "Uses libgit2 bindings" → Subprocess wrapper over `git` CLI binary
- "Merges auto-resolve conflicts" → Must call `abort_merge()` explicitly; conflicts leave repo in conflicted state
- "current_branch() always returns a string" → Returns `None` on detached HEAD
- "Reset defaults to hard" → Default is "mixed" (moves HEAD + index, not working tree)
- "Remote operations are fire-and-forget" → Exponential backoff retry on network errors; auth errors NOT retried
- "Worktrees share state" → Each worktree gets its own `GitOps` instance; fully isolated

**Key references to bundle**:
- `src/coderecon/git/ops.py` — GitOps (~50 methods)
- `src/coderecon/git/models.py` — CommitInfo, BranchInfo, DiffInfo, MergeResult, etc.
- `src/coderecon/git/errors.py` — Typed exception hierarchy

**Estimated SKILL.md size**: ~250 lines + 1 reference (error types)

---

### 9. `daemon` — Server Lifecycle, Concurrency, and File Watching

**Trigger**: Agent works on daemon startup/shutdown, modifies the watcher, changes
freshness gates, or debugs concurrency issues.

**Wrong assumptions this prevents**:
- "Daemon is backgrounded" → Foreground process; user must keep shell open
- "All worktrees block on one mutation" → Per-worktree staleness; editing "main" doesn't block "feature-x"
- "Mutations can interleave per worktree" → Serialized per-worktree via MutationRouter exclusive lock
- "Reindex is unbounded" → Semaphore max 2 inflight; 3rd waits
- "File watcher uses recursive mode" → `recursive=False`; one inotify per directory; restarts on new dirs
- "WSL uses inotify" → Falls back to mtime polling for `/mnt/[a-z]/` paths
- "Linters/tests are agent-triggered only" → analysis_pipeline.py auto-runs Tier 1 + Tier 2 post-index

**Lock hierarchy (must be respected)**:
```
session._exclusive_lock → MutationRouter.mutation() → _reindex_semaphore →
coordinator._reconcile_lock → coordinator._tantivy_write_lock
```

**Key references to bundle**:
- `src/coderecon/daemon/concurrency.py` — FreshnessGate, MutationRouter
- `src/coderecon/daemon/global_app.py` — GlobalDaemon, RepoSlot, WorktreeSlot
- `src/coderecon/daemon/lifecycle.py` — ServerController
- `src/coderecon/daemon/watcher.py` — File watcher design
- `src/coderecon/daemon/indexer.py` — BackgroundIndexer

**Estimated SKILL.md size**: ~350 lines + 1 reference (lock hierarchy diagram)

---

### 10. `ranking` — LightGBM Models, Feature Pipeline, and RRF Fallback

**Trigger**: Agent works on ranking models, modifies feature extraction, changes the
gate/cutoff logic, or debugs scoring issues.

**Wrong assumptions this prevents**:
- "Models are required" → All optional; graceful fallback: Ranker→zeros, Gate→OK, Cutoff→20
- "Gate is binary (good/bad)" → Multiclass: OK/UNSAT/BROAD/AMBIG; non-OK exits early without scoring
- "RRF is a fallback only" → RRF always runs; `rrf_score` is a feature fed to the ranker
- "Features are numeric" → Many one-hot categorical (graph_is_callee, sym_agent_seed, etc.)
- "Models download at runtime" → Baked into wheel as package data; not updateable post-install
- "K=60 in RRF is configurable" → Hardcoded; tuning requires code change
- "Cutoff is unclamped" → Clamped to [3, 30] (elbow) or model-specific bounds

**Key references to bundle**:
- `src/coderecon/ranking/features.py` — 40+ feature extraction pipeline
- `src/coderecon/ranking/ranker.py` — LightGBM LambdaMART
- `src/coderecon/ranking/gate.py` — Multiclass gate
- `src/coderecon/ranking/cutoff.py` — Cutoff regressor
- `src/coderecon/ranking/rrf.py` — RRF fusion algorithm

**Estimated SKILL.md size**: ~250 lines + 1 reference (feature table)

---

### 11. `writing-tests` — Test Conventions, Fixtures, and CI Integration

**Trigger**: Agent writes new tests, modifies existing tests, or needs to understand the
test infrastructure.

**Wrong assumptions this prevents**:
- "Tests import installed package" → conftest.py inserts local `src/` at sys.path[0]; editable install takes priority
- "Use mocking for file I/O" → Preferred: real temp files/repos; never mock git operations
- "All tests run in CI" → e2e tests disabled (issue #130); integration needs `@pytest.mark.integration`
- "Integration test failures are bugs" → Some contain intentional failures (nested pytest runs); not uploaded to Codecov
- "Coverage threshold fails CI" → 90% threshold is warning-only (::warning::), not blocking
- "Line ranges are 0-based" → 1-based inclusive: `(2, 4)` means lines 2, 3, 4

**Naming convention**: `test_given_<precondition>_when_<action>_then_<assertion>`

**Fixture patterns**:
- Temp repos: `git init` + initial commit (CI sets `GIT_AUTHOR_NAME`, `GIT_COMMITTER_NAME`)
- Autouse reset: clear logging/structlog/env between tests
- File creation: real pathlib writes, not mocking

**Key references to bundle**:
- `tests/conftest.py` — Root fixtures and sys.path setup
- `.github/workflows/ci.yml` — CI pipeline markers and coverage
- Representative tests from each module

**Estimated SKILL.md size**: ~250 lines

---

### 12. `recon-lab` — ML Training Pipeline and Model Production

**Trigger**: Agent works on the training pipeline, modifies data collection, changes
model hyperparameters, or debugs data quality.

**Wrong assumptions this prevents**:
- "Training data is random split" → Hash-based deterministic split: `hash(run_id) % 5 == 4` → cutoff shard
- "SWE-bench instances are all Python" → Python + Rust + Java
- "Train/eval overlap is harmless" → 500 Verified ⊂ test split; training on Verified = data leakage
- "Import stage is expensive" → 108 dev instances have cached LLM queries; import skips them
- "DVC caches everything" → `cache: false` on large outputs; `persist: true` on clones
- "Pipeline stages run sequentially" → DVC DAG: some stages parallelize (e.g., swebench-import + index)
- "Env vars use CODEPLANE__" → recon-lab uses `CPL_LAB_*` prefix

**Key references to bundle**:
- `recon-lab/lab.toml` — Pipeline configuration
- `recon-lab/dvc.yaml` — 8-stage DAG definition
- `recon-lab/src/cpl_lab/schema.py` — Ground truth + signal tables
- `recon-lab/pipeline-design.md` — Full pipeline architecture
- `recon-lab/aml/` — Azure ML component definitions

**Estimated SKILL.md size**: ~300 lines + 2 references (schema, pipeline diagram)

---

## File Structure

```
.github/skills/
├── indexing/
│   ├── SKILL.md
│   └── references/
│       ├── fact-tables.md
│       └── language-tiers.md
├── testing/
│   ├── SKILL.md
│   └── references/
│       └── runner-packs.md
├── linting/
│   ├── SKILL.md
│   └── references/
│       └── tool-registry.md
├── mcp-tools/
│   ├── SKILL.md
│   └── references/
│       ├── tool-schemas.md
│       └── session-lifecycle.md
├── refactoring/
│   └── SKILL.md
├── mutation/
│   └── SKILL.md
├── config/
│   ├── SKILL.md
│   └── references/
│       └── config-schema.md
├── git-ops/
│   ├── SKILL.md
│   └── references/
│       └── error-types.md
├── daemon/
│   ├── SKILL.md
│   └── references/
│       └── lock-hierarchy.md
├── ranking/
│   ├── SKILL.md
│   └── references/
│       └── feature-table.md
├── writing-tests/
│   └── SKILL.md
└── recon-lab/
    ├── SKILL.md
    └── references/
        ├── schema.md
        └── pipeline-stages.md
```

## Priority Order

| Priority | Skill | Rationale |
|----------|-------|-----------|
| P0 | `mcp-tools` | Agents interact with MCP tools most frequently; wrong assumptions here cause immediate failures |
| P0 | `writing-tests` | Every code change needs tests; wrong conventions waste cycles |
| P0 | `indexing` | Core subsystem; language/tier confusion is the #1 reported issue |
| P1 | `testing` | Runner pack assumptions ("only pytest") are the motivating example |
| P1 | `refactoring` | Preview-before-apply pattern trips up every agent |
| P1 | `config` | CODEPLANE__ prefix and silent env var failures cause subtle bugs |
| P2 | `daemon` | Less frequently modified; lock hierarchy matters for concurrency work |
| P2 | `ranking` | Specialized ML knowledge; needed when touching scoring logic |
| P2 | `linting` | Tool detection nuances matter when adding new tools |
| P2 | `git-ops` | Subprocess-based design matters for error handling work |
| P3 | `mutation` | Small module; fewer wrong assumptions |
| P3 | `recon-lab` | Separate pipeline; rarely touched alongside main codebase |

## Implementation Notes

- Each `SKILL.md` body should be <500 lines (progressive loading)
- Reference files for large tables (feature lists, runner packs, tool registries)
- Descriptions must be keyword-rich for agent discovery
- Use `disable-model-invocation: false` (default) so agents auto-load when relevant
- Reference files use relative paths (`./references/fact-tables.md`)
- No scripts needed — these are pure knowledge skills, not workflow automation
