"""Construct the simulator engine for local tests or deployed Lambdas."""

from __future__ import annotations

import os

from .engine import ScenarioEngine
from .persistence import DynamoRunRepository


def build_engine() -> ScenarioEngine:
    """Use shared DynamoDB state only when the deployment opts into it.

    Keeping the opt-in explicit preserves fast, isolated unit tests while the
    API, simulator, and Gateway target Lambdas share one authoritative run
    state in the deployed environment.
    """

    table_name = str(os.getenv("OPSFUSION_STATE_TABLE") or "").strip()
    use_dynamo = str(os.getenv("OPSFUSION_USE_DYNAMODB") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if table_name and use_dynamo:
        return ScenarioEngine(
            persistence=DynamoRunRepository(
                table_name,
                region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
            )
        )
    return ScenarioEngine()
