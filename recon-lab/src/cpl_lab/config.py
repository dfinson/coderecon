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
REPOS_DIR = LAB_ROOT / "repos"
ROLES_DIR = LAB_ROOT / "roles"
INFRA_DIR = LAB_ROOT / "infra"

# Default lab.toml location.
_DEFAULT_CONFIG = LAB_ROOT / "lab.toml"


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
    swebench_cfg = cfg.get("swebench", {})

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
            "depth": cfg.get("clone", {}).get("depth", 1),
        },
        "index": {
            "timeout": cfg.get("index", {}).get("timeout", 1800),
        },
        "swebench": {
            "training_dataset": swebench_cfg.get(
                "training_dataset", "princeton-nlp/SWE-bench"
            ),
            "training_split": swebench_cfg.get("training_split", "dev"),
            "eval_dataset": swebench_cfg.get(
                "eval_dataset", "princeton-nlp/SWE-bench_Verified"
            ),
            "eval_split": swebench_cfg.get("eval_split", "test"),
            "llm_model": swebench_cfg.get("llm_model", "openai/gpt-4.1-mini"),
            "filter_model": swebench_cfg.get("filter_model", "openai/gpt-4.1-mini"),
            "max_instances": swebench_cfg.get("max_instances", 0),
            "cutoff_mod": swebench_cfg.get("cutoff_mod", 5),
            "cutoff_remainder": swebench_cfg.get("cutoff_remainder", 4),
        },
        "eval": {
            "default_experiment": cfg.get("eval", {}).get(
                "default_experiment", "recon_ranking.yaml"
            ),
        },
    }
