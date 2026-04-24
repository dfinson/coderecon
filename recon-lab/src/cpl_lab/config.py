"""Configuration resolution for cpl-lab.

Priority (highest wins):
  1. CLI flags (--workspace, etc.)
  2. lab.toml in the recon-lab project root
  3. Built-in defaults
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 12):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

# Repo-local paths (versioned source).
LAB_ROOT = Path(__file__).resolve().parent.parent.parent  # recon-lab/
CODERECON_ROOT = LAB_ROOT.parent  # coderecon/
REPOS_DIR = LAB_ROOT / "repos"
ROLES_DIR = LAB_ROOT / "roles"
INFRA_DIR = LAB_ROOT / "infra"

# Default lab.toml location.
_DEFAULT_CONFIG = LAB_ROOT / "lab.toml"


def recon_binary() -> str:
    """Resolve the ``recon`` CLI binary from the coderecon venv."""
    venv_bin = CODERECON_ROOT / ".venv" / "bin" / "recon"
    if venv_bin.is_file():
        return str(venv_bin)
    import shutil
    found = shutil.which("recon")
    if found:
        return found
    raise FileNotFoundError(
        f"recon binary not found at {venv_bin} or on PATH"
    )


def _load_toml(path: Path) -> dict:
    """Load a TOML file, returning {} if it doesn't exist."""
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_workspace(cli_override: str | None = None) -> Path:
    """Resolve the workspace directory.

    Priority: CLI flag > lab.toml > default (~/.recon/recon-lab).
    """
    if cli_override:
        return Path(cli_override).expanduser().resolve()

    cfg = _load_toml(_DEFAULT_CONFIG)
    toml_path = cfg.get("workspace", {}).get("path")
    if toml_path:
        return Path(toml_path).expanduser().resolve()

    return Path.home() / ".recon" / "recon-lab"


def get_config(cli_override: str | None = None) -> dict:
    """Return merged config dict with resolved workspace paths."""
    cfg = _load_toml(_DEFAULT_CONFIG)
    ws = resolve_workspace(cli_override)
    pr_cfg = cfg.get("pr_select", {})

    return {
        "workspace": ws,
        "clones_dir": ws / "clones",
        "data_dir": ws / "data",
        "models_dir": ws / "models",
        "repos_dir": REPOS_DIR,
        "roles_dir": ROLES_DIR,
        "infra_dir": INFRA_DIR,
        "lab_root": LAB_ROOT,
        "clone": {
            "jobs": cfg.get("clone", {}).get("jobs", 4),
        },
        "index": {
            "timeout": cfg.get("index", {}).get("timeout", 1800),
        },
        "pr_select": {
            "prs_per_repo": pr_cfg.get("prs_per_repo", 30),
            "max_files_changed": pr_cfg.get("max_files_changed", 50),
            "min_files_changed": pr_cfg.get("min_files_changed", 1),
            "llm_model": pr_cfg.get("llm_model", "openai/gpt-4-1-nano"),
        },
        "eval": {
            "default_experiment": cfg.get("eval", {}).get(
                "default_experiment", "recon_ranking.yaml"
            ),
        },
    }
