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


def run(experiment: str | None = None) -> None:
    """Entry point callable from recon-lab CLI.

    Args:
        experiment: Which experiment set to run.
            None or "ranking" → ranking pipeline (baseline + trained).
            "micro" → offline sanity check from merged parquet.
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
    else:
        raise ValueError(
            f"Unknown experiment: {experiment!r}. "
            "Use 'ranking' (default), 'diagnostic', 'micro', or 'micro-compare'."
        )

    inspect_eval(
        tasks,
        model="mockllm/model",
        log_dir=log_dir,
        max_messages=1,
    )


if __name__ == "__main__":
    exp = sys.argv[1] if len(sys.argv) > 1 else None
    run(exp)

