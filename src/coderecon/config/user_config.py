"""Minimal user-facing configuration.

This module defines only the config fields that users should care about.
Everything else uses opinionated defaults.

User config is stored in .recon/config.yaml
Runtime state is stored in .recon/state.yaml (auto-generated, not user-editable)
"""

from pathlib import Path
import structlog
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from coderecon.files.ops import atomic_write_text

log = structlog.get_logger(__name__)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Default values - kept in sync with UserConfig defaults
DEFAULT_PORT = 7654
DEFAULT_MAX_FILE_SIZE_MB = 20
DEFAULT_LOG_LEVEL: LogLevel = "INFO"

class UserConfig(BaseModel):
    """User-facing configuration options.

    Only contains settings that users may reasonably want to customize.
    Everything else uses sensible defaults.
    """

    port: int = Field(
        default=7654,
        description="Server port. Override with: recon up --port <N>",
    )
    max_file_size_mb: int = Field(
        default=20,  # Increased from 10 - users rarely need to change this
        description="Skip files larger than this (MB) during indexing.",
    )
    log_level: LogLevel = Field(
        default="INFO",
        description="Log level. DEBUG is very verbose.",
    )

class RuntimeState(BaseModel):
    """Auto-generated runtime state. NOT user-editable.

    Stored in .recon/state.yaml with clear warnings.
    """

    index_path: str = Field(
        description="Path where index files are stored. Auto-detected.",
    )

STATE_HEADER = """\
# AUTO-GENERATED - DO NOT EDIT MANUALLY
# This file tracks runtime state for this repository.
# Delete .recon/ and run 'recon init' to regenerate.

"""

def write_user_config(path: Path, config: UserConfig | None = None) -> None:
    """Write user config file with helpful comments.

    Args:
        path: Path to write config.yaml
        config: Config values (uses defaults if None)
    """
    cfg = config or UserConfig()

    lines = [
        "# CodeRecon Configuration",
        "# Docs: https://coderecon.dev/config",
        "",
    ]

    # Port
    lines.append("# Server port (can also use: recon up --port <N>)")
    lines.append(f"port: {cfg.port}")
    lines.append("")

    # Max file size - write active if non-default, else as comment
    lines.append("# Maximum file size to index (MB). Files larger than this are skipped.")
    lines.append("# Increase for repos with large source files, but may slow indexing.")
    if cfg.max_file_size_mb != DEFAULT_MAX_FILE_SIZE_MB:
        lines.append(f"max_file_size_mb: {cfg.max_file_size_mb}")
    else:
        lines.append(f"# max_file_size_mb: {cfg.max_file_size_mb}")
    lines.append("")

    # Log level - write active if non-default, else as comment
    lines.append("# Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL")
    lines.append("# DEBUG is very verbose - use only for troubleshooting.")
    if cfg.log_level != DEFAULT_LOG_LEVEL:
        lines.append(f"log_level: {cfg.log_level}")
    else:
        lines.append(f"# log_level: {cfg.log_level}")
    lines.append("")

    atomic_write_text(path, "\n".join(lines))

def write_runtime_state(path: Path, state: RuntimeState) -> None:
    """Write runtime state file with warning header."""
    data = state.model_dump()
    content = STATE_HEADER + yaml.dump(data, default_flow_style=False, sort_keys=False)
    atomic_write_text(path, content)

def load_user_config(path: Path) -> UserConfig:
    """Load user config from YAML file."""
    if not path.exists():
        return UserConfig()
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return UserConfig(**data)
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        log.debug("user_config_parse_failed", exc_info=True)
        return UserConfig()

def load_runtime_state(path: Path) -> RuntimeState | None:
    """Load runtime state from YAML file."""
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return RuntimeState(**data)
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        log.debug("runtime_state_parse_failed", exc_info=True)
        return None
