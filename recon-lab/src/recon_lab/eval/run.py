#!/usr/bin/env python3
"""Run Inspect AI evaluation — loads tasks and invokes eval().

Usage (standalone):
    cd recon-lab/src/recon_lab/eval
    python run.py                        # ranking pipeline tasks
    python run.py llm                    # llm reranker tasks

Via Inspect CLI:
    inspect eval recon_lab/eval/tasks.py@ranking_baseline --model mockllm/model
    inspect eval recon_lab/eval/tasks.py@ranking_trained --model mockllm/model

Or via the recon-lab CLI:
    recon-lab eval
    recon-lab eval --experiment diagnostic
    recon-lab micro-eval
"""

from __future__ import annotations

import sys
from pathlib import Path

from inspect_ai import eval as inspect_eval

from recon_lab.eval.tasks import (
    ranking_baseline,
    ranking_diagnostic,
    ranking_micro,
    ranking_micro_ce_only,
    ranking_micro_rrf,
    ranking_trained,
)
from recon_lab.eval.gt_discovery.task import gt_discovery


def _resolve_azure_model(model_override: str | None = None) -> str:
    """Resolve the Inspect AI model string for Azure OpenAI.

    Bridges the lab's AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_VERSION
    env vars to Inspect AI's AZUREAI_OPENAI_BASE_URL / AZUREAI_OPENAI_API_VERSION.
    Uses the lab's existing AAD token helper for auth.
    """
    import os

    if model_override and not model_override.startswith("openai/azure/"):
        return model_override  # user knows what they're doing

    # Bridge env vars from lab convention → Inspect convention
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    if endpoint and not os.environ.get("AZUREAI_OPENAI_BASE_URL"):
        os.environ["AZUREAI_OPENAI_BASE_URL"] = endpoint

    api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
    if api_version and not os.environ.get("AZUREAI_OPENAI_API_VERSION"):
        os.environ["AZUREAI_OPENAI_API_VERSION"] = api_version

    # Provide AAD token via lab's existing helper if no API key is set
    if not os.environ.get("AZUREAI_OPENAI_API_KEY"):
        from recon_lab.llm.llm_client import _get_azure_token

        token = _get_azure_token()
        if token:
            os.environ["AZUREAI_OPENAI_API_KEY"] = token

    if model_override:
        return model_override
    return os.environ.get("INSPECT_EVAL_MODEL", "openai/azure/gpt-4o-mini")


def run(experiment: str | None = None, *, model_override: str | None = None) -> None:
    """Entry point callable from recon-lab CLI.

    Args:
        experiment: Which experiment set to run.
            None or "ranking" → ranking pipeline (baseline + trained).
            "micro" → offline sanity check from merged parquet.
        model_override: Override the default model for LLM experiments.
    """
    log_dir = str(Path("~/.recon/recon-lab/eval/logs").expanduser())

    if experiment is None or experiment == "ranking":
        tasks = [ranking_baseline(), ranking_trained()]
    elif experiment == "diagnostic":
        tasks = [ranking_diagnostic()]
    elif experiment == "micro":
        tasks = [ranking_micro()]
    elif experiment == "micro-compare":
        tasks = [ranking_micro(), ranking_micro_rrf(), ranking_micro_ce_only()]
    elif experiment == "gt-discovery":
        tasks = [gt_discovery()]
    else:
        raise ValueError(
            f"Unknown experiment: {experiment!r}. "
            "Use 'ranking' (default), 'diagnostic', 'micro', 'micro-compare', or 'gt-discovery'."
        )

    # gt-discovery uses a real LLM; others use mockllm for deterministic eval.
    model = "mockllm/model"
    max_msg = 1
    if experiment == "gt-discovery":
        model = _resolve_azure_model(model_override)
        max_msg = 35

    inspect_eval(
        tasks,
        model=model,
        log_dir=log_dir,
        max_messages=max_msg,
    )


if __name__ == "__main__":
    exp = sys.argv[1] if len(sys.argv) > 1 else None
    run(exp)

