---
title: Configuration
description: Config files, schema, environment variables, and precedence rules
---

CodeRecon uses two config files per repo, both inside `.recon/`.

---

## `.recon/config.yaml`

User-editable. Created by `recon register`. Persists across re-indexing.

```yaml
# Port the daemon listens on for this repo's MCP slot.
# Override via: recon register --port <N>
# Or env var: CODERECON__SERVER__PORT=<N>
port: 7654
```

### Full schema

```yaml
# ── Server ─────────────────────────────────────────────────
server:
  host: "127.0.0.1"              # str — bind address
  port: 7654                     # int — daemon port
  shutdown_timeout_sec: 5        # int — graceful shutdown timeout
  poll_interval_sec: 1.0         # float — health poll interval
  debounce_sec: 0.3              # float — file-change debounce window
  worktree_idle_timeout_sec: 300 # float — idle worktree eviction

# ── Logging ────────────────────────────────────────────────
logging:
  level: INFO                    # DEBUG | INFO | WARN | ERROR
  outputs:
    - format: console            # "console" or "json"
      destination: stderr        # stderr, stdout, or absolute file path
      level: null                # optional per-output override

# ── Index ──────────────────────────────────────────────────
index:
  max_file_size_mb: 10           # int — skip files larger than this
  excluded_extensions:           # list — file extensions to skip
    - ".min.js"
    - ".min.css"
    - ".map"
  index_path: null               # str — override index storage location

# ── Background Indexer ─────────────────────────────────────
indexer:
  debounce_sec: 0.5              # float — debounce between incremental reindexes
  max_workers: 1                 # int — concurrent indexer threads (1 recommended)
  queue_max_size: 10000          # int — max pending file changes in queue

# ── Timeouts ───────────────────────────────────────────────
timeouts:
  server_stop_sec: 5.0           # float — server shutdown grace period
  force_exit_sec: 3.0            # float — force-kill after grace period
  watcher_stop_sec: 2.0          # float — file watcher shutdown timeout
  epoch_await_sec: 5.0           # float — max wait for index epoch completion
  session_idle_sec: 1800.0       # float — session eviction after idle (30 min)
  dry_run_ttl_sec: 60.0          # float — refactor preview expiry

# ── Query Limits ───────────────────────────────────────────
limits:
  search_default: 20             # int — default search result count
  map_depth_default: 3           # int — default tree depth for recon_map
  map_limit_default: 100         # int — default file limit for recon_map
  files_list_default: 200        # int — default file listing limit
  operation_records_max: 1000    # int — max stored operation records

# ── Testing ────────────────────────────────────────────────
testing:
  default_parallelism: 4         # int — parallel test workers
  default_timeout_sec: 300       # int — per-target timeout
  memory_reserve_mb: 1024        # int — RAM reserved for test runner
  subprocess_memory_limit_mb: null  # int — optional per-subprocess limit

  runners:
    python: pytest
    javascript: vitest           # use vitest instead of detected jest

  custom:
    - pattern: "e2e/**/*.spec.ts"
      runner: playwright
      cmd: ["npx", "playwright", "test", "{path}"]
      timeout_sec: 120

  exclude:
    - "**/fixtures/**"
    - "**/mocks/**"

# ── Refactor Engine ────────────────────────────────────────
refactor:
  enabled: true                  # set false to disable structural refactors

  doc_sweep:
    enabled: true                # update comments/docs on rename
    auto_apply: true
    min_confidence: medium       # "low" | "medium" | "high"
    scan_extensions:
      - .md
      - .rst
      - .adoc

# ── Database ───────────────────────────────────────────────
database:
  busy_timeout_ms: 30000         # int — SQLite busy-wait timeout
  max_retries: 3                 # int — retry count on transient errors
  retry_base_delay_sec: 0.1      # float — base delay between retries
  pool_size: 5                   # int — connection pool size
  checkpoint_interval: 1000      # int — WAL checkpoint frequency

# ── Telemetry (OpenTelemetry) ──────────────────────────────
telemetry:
  enabled: false                 # bool — enable OTLP export
  otlp_endpoint: null            # str — collector URL
  service_name: coderecon        # str — service name in traces

# ── Debug ──────────────────────────────────────────────────
debug:
  enabled: false                 # bool — enable debug endpoints
  verbose_errors: false          # bool — include stack traces (SECURITY RISK)

# ── Governance Policies ────────────────────────────────────
governance:
  coverage_floor:
    enabled: false
    level: warning               # "info" | "warning" | "error"
    threshold: 80.0
  lint_clean:
    enabled: false
    level: error
  no_new_cycles:
    enabled: false
    level: warning
  test_debt:
    enabled: true
    level: warning
  coverage_regression:
    enabled: false
    level: warning
    threshold: 0.0
  module_boundary:
    enabled: false
    level: info
  centrality_impact:
    enabled: false
    level: info
    threshold: 0.8
```

---

## `.recon/state.yaml`

Auto-generated. **Do not edit.** Stores runtime paths (index location for cross-filesystem setups).

---

## `.recon/.reconignore`

Controls what CodeRecon indexes. Follows `.gitignore` syntax. Created automatically by `recon register`.

```gitignore
# Noise directories
node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc
*.log
coverage/
.pytest_cache/
```

### Customizing ignore rules

Edit `.recon/.reconignore` directly, or add a `.reconignore` file at the **repo root**. Root-level patterns are merged in automatically so they survive `--reindex`.

```bash
# Add a pattern to repo-root .reconignore
echo "generated/" >> .reconignore
```

!!! tip "Sensitive files"
    Index artifacts in `.recon/` are gitignored by design, so it is **safe** to index files like `.env`. They are never committed or shared. Exclude them via `.reconignore` only if you don't want them in the semantic search index.

---

## Global Configuration

Global config lives at `~/.config/coderecon/config.yaml` and applies to all repos. Per-repo config takes precedence.

```yaml
# ~/.config/coderecon/config.yaml
port: 7654
```

---

## Config Precedence

1. One-off: `recon up --set key=value` or environment variables (`CODERECON__*`)
2. Per-repo: `.recon/config.yaml`
3. Global: `~/.config/coderecon/config.yaml`
4. Built-in defaults

---

## Environment Variables

All config keys are overridable via env vars. Use `CODERECON__` prefix with double-underscore separators for nesting:

```bash
CODERECON__SERVER__PORT=4200
CODERECON__LOGGING__LEVEL=DEBUG
CODERECON__REFACTOR__ENABLED=false
```

---

## Cross-Filesystem Detection (WSL)

When your repo is on a Windows filesystem path (e.g. `/mnt/c/...`), CodeRecon automatically moves the index to `~/.local/share/coderecon/indices/<hash>/` to avoid performance problems with WSL cross-filesystem I/O. This is transparent — no configuration needed.
