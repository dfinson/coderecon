"""Pydantic configuration models with env var support.

Configuration Hierarchy (highest to lowest precedence):
1. Direct kwargs to load_config()
2. Environment variables (CODERECON__SECTION__KEY)
3. Repo YAML (.recon/config.yaml)
4. Global YAML (~/.config/coderecon/config.yaml)
5. Built-in defaults (this file)

Environment Variable Format:
    CODERECON__<SECTION>__<KEY>=<VALUE>

Examples:
    CODERECON__LOGGING__LEVEL=DEBUG
    CODERECON__SERVER__PORT=8080
    CODERECON__LIMITS__SEARCH_DEFAULT=50
    CODERECON__INDEXER__MAX_WORKERS=4

See .recon/config.template.yaml for documented configuration options.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from coderecon.config.constants import PORT_MAX

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogOutputConfig(BaseModel):
    """Single logging output configuration.

    Env vars: Not directly configurable via env (use YAML for multi-output).
    """

    format: Literal["json", "console"] = "console"
    destination: str = "stderr"  # stderr, stdout, or absolute file path
    level: LogLevel | None = None  # Inherits from parent if None

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, v: str) -> str:
        if v in ("stderr", "stdout"):
            return v
        path = Path(v).expanduser()
        if not path.is_absolute():
            raise ValueError(f"File destination must be absolute path: {v}")
        return str(path)


class LoggingConfig(BaseModel):
    """Logging configuration.

    Env vars:
        CODERECON__LOGGING__LEVEL: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """

    level: LogLevel = Field(
        default="INFO",
        description="Root log level. DEBUG is verbose and may impact performance.",
    )
    outputs: list[LogOutputConfig] = Field(default_factory=lambda: [LogOutputConfig()])


class ServerConfig(BaseModel):
    """Server configuration.

    Env vars:
        CODERECON__SERVER__HOST: Bind address (default: 127.0.0.1)
        CODERECON__SERVER__PORT: Port number (default: 7654)
        CODERECON__SERVER__POLL_INTERVAL_SEC: File watcher poll interval
        CODERECON__SERVER__DEBOUNCE_SEC: Change debounce window
    """

    host: str = Field(
        default="127.0.0.1",
        description="Bind address. Use 0.0.0.0 for network access (security risk).",
    )
    port: int = Field(
        default=7654,
        description="Server port. Ensure firewall rules if exposing.",
    )
    shutdown_timeout_sec: int = Field(
        default=5,
        description="Graceful shutdown timeout before force-kill.",
    )
    poll_interval_sec: float = Field(
        default=1.0,
        description="File watcher polling interval for cross-filesystem mounts (WSL /mnt/*). "
        "Lower values increase CPU usage but detect changes faster.",
    )
    debounce_sec: float = Field(
        default=0.3,
        description="Debounce window before triggering reindex. "
        "Lower values may cause excessive reindexing during rapid edits.",
    )
    worktree_idle_timeout_sec: float = Field(
        default=300.0,
        description="Seconds of inactivity before a non-main worktree's watcher "
        "and MCP server are torn down. Set to 0 to disable eviction.",
    )

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (0 <= v <= PORT_MAX):
            raise ValueError(f"Port must be 0-{PORT_MAX}, got {v}")
        return v


class IndexConfig(BaseModel):
    """Index configuration.

    Env vars:
        CODERECON__INDEX__MAX_FILE_SIZE_MB: Skip files larger than this
        CODERECON__INDEX__INDEX_PATH: Override index storage location
    """

    max_file_size_mb: int = Field(
        default=10,
        description="Skip files larger than this (MB). Large files slow indexing. "
        "RISK: Setting too high may cause memory issues with binary files.",
    )
    excluded_extensions: list[str] = Field(
        default_factory=lambda: [".min.js", ".min.css", ".map"],
        description="File extensions to exclude from indexing.",
    )
    index_path: str | None = Field(
        default=None,
        description="Override index storage location. Use for WSL cross-filesystem "
        "performance (store index on native FS). Default: .recon/ in repo.",
    )


class TimeoutsConfig(BaseModel):
    """Timeout configuration for daemon components.

    Env vars:
        CODERECON__TIMEOUTS__EPOCH_AWAIT_SEC: Max wait for epoch freshness
        CODERECON__TIMEOUTS__SESSION_IDLE_SEC: Session idle timeout
    """

    server_stop_sec: float = Field(
        default=5.0,
        description="Server shutdown timeout.",
    )
    force_exit_sec: float = Field(
        default=3.0,
        description="Force exit timeout after graceful shutdown fails.",
    )
    watcher_stop_sec: float = Field(
        default=2.0,
        description="File watcher shutdown timeout.",
    )
    epoch_await_sec: float = Field(
        default=5.0,
        description="Max wait for index freshness after changes. "
        "RISK: Too low may return stale results; too high delays responses.",
    )
    session_idle_sec: float = Field(
        default=1800.0,
        description="Session idle timeout (30 min default). After this, session state is cleared.",
    )
    dry_run_ttl_sec: float = Field(
        default=60.0,
        description="TTL for dry-run refactoring previews.",
    )


class IndexerConfig(BaseModel):
    """Background indexer configuration.

    Env vars:
        CODERECON__INDEXER__DEBOUNCE_SEC: Indexer debounce window
        CODERECON__INDEXER__MAX_WORKERS: Parallel indexing workers
        CODERECON__INDEXER__QUEUE_MAX_SIZE: Max queued paths before dropping
    """

    debounce_sec: float = Field(
        default=0.5,
        description="Indexer-level debounce. Combined with watcher debounce for defense-in-depth.",
    )
    max_workers: int = Field(
        default=1,
        description="Parallel indexing workers. "
        "RISK: >1 may cause SQLite contention; recommended for SSDs only.",
    )
    queue_max_size: int = Field(
        default=10000,
        description="Max queued file paths. Excess paths are dropped (logged). "
        "RISK: Too low loses changes during bulk operations.",
    )


class LimitsConfig(BaseModel):
    """Query limit defaults.

    These are DEFAULT values - tools may allow per-request overrides up to max.
    See constants.py for hard maximums that cannot be exceeded.

    Env vars:
        CODERECON__LIMITS__SEARCH_DEFAULT: Default search results
        CODERECON__LIMITS__MAP_DEPTH_DEFAULT: Default repo map depth
        CODERECON__LIMITS__MAP_LIMIT_DEFAULT: Default repo map entries
        CODERECON__LIMITS__FILES_LIST_DEFAULT: Default file list entries
    """

    search_default: int = Field(
        default=20,
        description="Default search results. Industry standard: 10-50. "
        "TRADEOFF: Higher values increase response size and latency.",
    )
    map_depth_default: int = Field(
        default=3,
        description="Default repo map tree depth. "
        "TRADEOFF: Deeper = more complete but larger responses.",
    )
    map_limit_default: int = Field(
        default=100,
        description="Default repo map entries. "
        "TRADEOFF: More entries = larger context but may overwhelm agents.",
    )
    files_list_default: int = Field(
        default=200,
        description="Default file listing limit.",
    )
    operation_records_max: int = Field(
        default=1000,
        description="Max ledger operation records to return.",
    )


class TestingConfig(BaseModel):
    """Testing subsystem configuration.

    Env vars:
        CODERECON__TESTING__DEFAULT_PARALLELISM: Parallel test workers
        CODERECON__TESTING__DEFAULT_TIMEOUT_SEC: Test execution timeout
    """

    default_parallelism: int = Field(
        default=4,
        description="Default parallel test workers. Adjust based on CPU cores.",
    )
    default_timeout_sec: int = Field(
        default=300,
        description="Default test timeout (5 min). "
        "RISK: Too low may kill slow integration tests; too high wastes CI time.",
    )
    memory_reserve_mb: int = Field(
        default=1024,
        description="Memory (MB) to keep free. Test runner pauses launching new "
        "targets when available memory drops below this threshold.",
    )
    subprocess_memory_limit_mb: int | None = Field(
        default=None,
        description="Per-subprocess memory ceiling (MB). Injected via runtime-specific "
        "env vars (e.g. -Xmx for JVM, --max-old-space-size for Node). "
        "If None, computed dynamically from available memory at launch time.",
    )


class TelemetryConfig(BaseModel):
    """OpenTelemetry configuration.

    Env vars:
        CODERECON__TELEMETRY__ENABLED: Enable/disable telemetry
        CODERECON__TELEMETRY__OTLP_ENDPOINT: OTLP collector endpoint
        CODERECON__TELEMETRY__SERVICE_NAME: Service name for traces

    Note: Also respects standard OTEL_* env vars when enabled.
    """

    enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry. Set to true and configure endpoint to activate.",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP collector endpoint (e.g., http://localhost:4317). "
        "Required when enabled=true.",
    )
    service_name: str = Field(
        default="coderecon",
        description="Service name for traces/metrics.",
    )


class DatabaseConfig(BaseModel):
    """Database connection configuration.

    Env vars:
        CODERECON__DATABASE__BUSY_TIMEOUT_MS: SQLite busy timeout
        CODERECON__DATABASE__MAX_RETRIES: Max retry attempts for locked DB
        CODERECON__DATABASE__POOL_SIZE: Connection pool size
        CODERECON__DATABASE__CHECKPOINT_INTERVAL: Transactions between checkpoints
    """

    busy_timeout_ms: int = Field(
        default=30000,
        description="SQLite busy timeout (ms). How long to wait for locks. "
        "RISK: Too low causes failures under contention; too high delays errors.",
    )
    max_retries: int = Field(
        default=3,
        description="Max retry attempts for locked database errors.",
    )
    retry_base_delay_sec: float = Field(
        default=0.1,
        description="Base delay between retries (exponential backoff).",
    )
    pool_size: int = Field(
        default=5,
        description="Connection pool size. Higher values use more memory. "
        "RISK: SQLite handles concurrency differently than client-server DBs.",
    )
    checkpoint_interval: int = Field(
        default=1000,
        description="Transactions between WAL checkpoints. "
        "TRADEOFF: Lower = more frequent I/O; higher = larger WAL files.",
    )


class DebugConfig(BaseModel):
    """Debug and development configuration.

    Env vars:
        CODERECON__DEBUG__ENABLED: Enable debug mode
        CODERECON__DEBUG__VERBOSE_ERRORS: Include stack traces in error responses
    """

    enabled: bool = Field(
        default=False,
        description="Enable debug mode. Increases logging verbosity significantly.",
    )
    verbose_errors: bool = Field(
        default=False,
        description="Include stack traces in MCP error responses. "
        "SECURITY RISK: May leak sensitive path/code information.",
    )


class GovernancePolicyRule(BaseModel):
    """A single governance policy rule.

    Each rule defines a condition that must hold at checkpoint time.
    Violations can block commits (level="error") or warn (level="warning").
    """

    enabled: bool = Field(default=True, description="Whether this rule is active.")
    level: str = Field(
        default="warning",
        description="Severity: 'error' (blocks commit), 'warning' (advisory), 'info' (log only).",
    )
    threshold: float | None = Field(
        default=None,
        description="Numeric threshold (e.g., minimum coverage %, max cycle count).",
    )
    message: str = Field(
        default="",
        description="Custom message shown when rule fires.",
    )


class GovernanceConfig(BaseModel):
    """Governance policies for checkpoint gating.

    Defines rules that are evaluated at checkpoint time. Failed rules can
    block commit (error level) or warn (warning level).

    Env vars:
        CODERECON__GOVERNANCE__COVERAGE_FLOOR: Minimum test coverage %
        CODERECON__GOVERNANCE__LINT_CLEAN: Require lint-clean before commit
    """

    coverage_floor: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=False,
            level="warning",
            threshold=80.0,
            message="Test coverage below {threshold}%.",
        ),
        description="Minimum test coverage percentage.",
    )
    lint_clean: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=False,
            level="error",
            message="Lint errors must be resolved before commit.",
        ),
        description="Require all changed files to be lint-clean.",
    )
    no_new_cycles: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=False,
            level="warning",
            message="New circular dependencies detected.",
        ),
        description="Block introduction of new dependency cycles.",
    )
    test_debt: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=True,
            level="warning",
            message="Source files changed without updating corresponding tests.",
        ),
        description="Warn when source files change without test updates.",
    )
    coverage_regression: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=False,
            level="warning",
            threshold=0.0,
            message="Coverage decreased by more than {threshold}%.",
        ),
        description="Block coverage regressions beyond threshold.",
    )
    module_boundary: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=False,
            level="info",
            message="Cross-community import detected.",
        ),
        description="Warn on imports crossing community boundaries.",
    )
    centrality_impact: GovernancePolicyRule = Field(
        default_factory=lambda: GovernancePolicyRule(
            enabled=False,
            level="info",
            threshold=0.8,
            message="Change affects a high-centrality symbol (top {threshold}).",
        ),
        description="Warn when changes affect high-PageRank symbols.",
    )


class CodeReconConfig(BaseModel):
    """Root configuration for CodeRecon.

    All settings can be configured via:
    1. Environment variables: CODERECON__SECTION__KEY
    2. YAML config files (repo or global)
    3. Direct kwargs to load_config()

    See .recon/config.template.yaml for full documentation.
    """

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    testing: TestingConfig = Field(default_factory=TestingConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
