"""Step Functions worker boundary.

Cloud workers persist durable state in DynamoDB in the deployed adapter.  The
local implementation is intentionally side-effect free so template validation
and state-machine retries can be tested without invoking a model.
"""

from __future__ import annotations

import os
from typing import Any

from .checkpoints import CheckpointNotFound, DynamoCheckpointStore, WorkflowCheckpoint


VALID_STAGES = {
    "initialize",
    "parallel_investigation",
    "reconcile_evidence",
    "completed",
}

STAGE_ORDER = {
    "initialize": 0,
    "parallel_investigation": 1,
    "reconcile_evidence": 2,
    "completed": 3,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    stage = str(event.get("stage") or "").strip()
    if stage not in VALID_STAGES:
        raise ValueError(f"unsupported workflow stage: {stage}")
    original = dict(event.get("input") or {})
    run_id = str(original.get("run_id") or event.get("run_id") or "").strip()
    if run_id and os.getenv("OPSFUSION_WORKFLOW_TABLE"):
        store = DynamoCheckpointStore(
            os.environ["OPSFUSION_WORKFLOW_TABLE"],
            region_name=os.getenv("AWS_REGION"),
        )
        try:
            current = store.get(run_id)
        except CheckpointNotFound:
            current = store.create(WorkflowCheckpoint(run_id=run_id, stage="initialize"))
        if current.stage == stage or STAGE_ORDER.get(current.stage, -1) > STAGE_ORDER[stage]:
            checkpoint = current
        else:
            completed = tuple(dict.fromkeys((*current.completed_stages, current.stage)))
            checkpoint = store.save(
                WorkflowCheckpoint(
                    run_id=run_id,
                    stage=stage,
                    completed_stages=completed,
                    retry_count=current.retry_count,
                    metadata={**current.metadata, "last_worker_input": original},
                ),
                expected_version=current.version,
            )
        original["checkpoint_version"] = checkpoint.version
        original["completed_stages"] = list(checkpoint.completed_stages)
    original.update({
        "stage": stage,
        "worker_status": "checkpointed",
        "analysis_complete": stage == "completed",
        "read_only_mode": True,
    })
    return original
