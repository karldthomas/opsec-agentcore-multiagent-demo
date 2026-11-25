"""Incident orchestrator facade.

The orchestrator delegates analysis work to the durable workflow. The Harness
session is available for evidence reconciliation and cannot change simulator
state.
"""

from __future__ import annotations

from typing import Any

from .session import AgentSession
from workflow.durable import DurableInvestigationWorkflow, WorkflowResult


class IncidentOrchestrator(AgentSession):
    def __init__(self, client, workflow: DurableInvestigationWorkflow | None = None) -> None:
        super().__init__(client, "orchestrator")
        self.workflow = workflow

    def start_investigation(
        self,
        run_id: str,
        *,
        objective: str,
        actor_id: str,
        incident_id: str | None = None,
    ) -> WorkflowResult:
        if self.workflow is None:
            raise RuntimeError("a durable workflow is required for orchestration")
        return self.workflow.start(
            run_id,
            objective=objective,
            actor_id=actor_id,
            incident_id=incident_id,
        )

    def reconcile(self, objective: str, **kwargs: Any):
        """Invoke the managed orchestrator Harness session when needed."""

        return self.invoke(objective, **kwargs)
