# Configuration

CodeRecon uses two config files per repo, both inside `.recon/`.

---

## `.recon/config.yaml`

User-editable. Created by `recon register`. Persists across re-indexing.

```yaml
# Port the daemon listens on for this repo's MCP slot.
# Override via: recon register --port <N>
# Or env var: CODEPLANE__SERVER__PORT=<N>
port: 3100
```

### Full schema

```yaml
port: 3100          # int — daemon port

# Test runner configuration
tests:
  runners:
    # Override auto-detected runner per language
    python: pytest
    javascript: vitest   # use vitest instead of detected jest

  # Custom runners for specific file patterns
  custom:
    - pattern: "e2e/**/*.spec.ts"
      runner: playwright
      cmd: ["npx", "playwright", "test", "{path}"]
      timeout_sec: 120

  # Files/directories to exclude from test discovery
  exclude:
    - "**/fixtures/**"
    - "**/mocks/**"

# Refactor engine
refactor:
  enabled: true     # set false to disable structural refactors

  doc_sweep:
    enabled: true          # update comments/docs on rename
    auto_apply: true
    min_confidence: medium # "low" | "medium" | "high"
    scan_extensions:
      - .md
      - .rst
      - .adoc
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
port: 3100
```

---

## Config Precedence

1. One-off: `recon up --set key=value` or environment variables (`CODEPLANE__*`)
2. Per-repo: `.recon/config.yaml`
3. Global: `~/.config/coderecon/config.yaml`
4. Built-in defaults

---

## Environment Variables

All config keys are overridable via env vars. Use `CODEPLANE__` prefix with double-underscore separators for nesting:

```bash
CODEPLANE__SERVER__PORT=4200
CODEPLANE__LOGGING__LEVEL=DEBUG
CODEPLANE__REFACTOR__ENABLED=false
```

---

## Cross-Filesystem Detection (WSL)

When your repo is on a Windows filesystem path (e.g. `/mnt/c/...`), CodeRecon automatically moves the index to `~/.local/share/coderecon/indices/<hash>/` to avoid performance problems with WSL cross-filesystem I/O. This is transparent — no configuration needed.
