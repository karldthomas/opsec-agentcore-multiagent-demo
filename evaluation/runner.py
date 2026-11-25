"""Small evaluation runner used by local smoke tests and deployment jobs."""

from __future__ import annotations

from typing import Any

from .deterministic import DeterministicEvaluator


def evaluate_run(evaluator: DeterministicEvaluator, run_id: str) -> dict[str, Any]:
    """Return the read-only analysis result for a run.

    Evaluation reads hidden scenario truth separately from the evidence
    workspace and never depends on a report, approval, or recovery state.
    """
    return evaluator.evaluate(run_id).to_dict()
