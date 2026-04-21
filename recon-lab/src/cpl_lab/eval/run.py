#!/usr/bin/env python3
"""Run Inspect AI evaluation — loads tasks and invokes eval().

Usage (standalone):
    cd recon-lab/src/cpl_lab/eval
    python run.py                        # ranking pipeline tasks
    python run.py llm                    # llm reranker tasks

Via Inspect CLI:
    inspect eval cpl_lab/eval/tasks.py@ranking_baseline --model mockllm/model
    inspect eval cpl_lab/eval/tasks.py@ranking_trained --model mockllm/model

Or via the recon-lab CLI:
    recon-lab eval
    recon-lab eval --experiment llm
"""

from __future__ import annotations

import sys
from pathlib import Path

from inspect_ai import eval as inspect_eval

from cpl_lab.eval.tasks import (
    ranking_baseline,
    ranking_diagnostic,
    ranking_trained,
)


def run(experiment: str | None = None) -> None:
    """Entry point callable from cpl-lab CLI.

    Args:
        experiment: Which experiment set to run.
            None or "ranking" → ranking pipeline (baseline + trained).
    """
    log_dir = str(Path("~/.recon/recon-lab/eval/logs").expanduser())

    if experiment is None or experiment == "ranking":
        tasks = [ranking_baseline(), ranking_trained()]
    elif experiment == "diagnostic":
        tasks = [ranking_diagnostic()]
    else:
        raise ValueError(
            f"Unknown experiment: {experiment!r}. "
            "Use 'ranking' (default) or 'diagnostic'."
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

