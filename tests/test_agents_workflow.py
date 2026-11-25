from __future__ import annotations

import json

from agents.harness import HarnessInvocationResult, InvocationContext
from agents.session import AgentSession
from evidence import EvidenceStore
from gateway.tool_catalog import ToolCatalog, ToolContext
from simulator.engine import ScenarioEngine
from workflow.local import InvestigationCoordinator


class FakeClient:
    def invoke(self, context: InvocationContext, prompt: str) -> HarnessInvocationResult:
        evidence_id = {
            "telemetry-agent": "EVD-METRIC-API-2048",
            "change-agent": "EVD-DEP-2048-NEW",
            "data-agent": "EVD-DB-2048-001",
        }[context.role]
        payload = {
            "findings": [
                {
                    "claim_type": "fact",
                    "statement": f"{context.role} observed a source record.",
                    "evidence_ids": [evidence_id],
                    "confidence": 0.9,
                }
            ],
            "hypotheses": [],
            "unresolved_questions": [],
            "follow_up_requests": [],
        }
        return HarnessInvocationResult(
            context=context,
            text=json.dumps(payload),
            stop_reason="end_turn",
            events=(),
            tool_uses=(),
            usage={},
            metrics={},
            errors=(),
        )


class UnrepairableOutputClient:
    def invoke(self, context: InvocationContext, prompt: str) -> HarnessInvocationResult:
        payload = {
            "findings": [],
            "hypotheses": [{"confidence": 0.8}],
            "unresolved_questions": [],
            "follow_up_requests": [],
            "recommendations": [],
        }
        return HarnessInvocationResult(
            context=context,
            text=json.dumps(payload),
            stop_reason="end_turn",
            events=(),
            tool_uses=(),
            usage={},
            metrics={},
            errors=(),
        )


def test_parallel_coordinator_persists_specialist_findings() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="workflow-run")
    catalog = ToolCatalog(engine)
    evidence = EvidenceStore(engine)
    for role, tool, source in (
        ("telemetry-agent", "query_cloudwatch_metrics", "metrics"),
        ("change-agent", "list_recent_deployments", "deployments"),
        ("data-agent", "get_database_metrics", "database_metrics"),
    ):
        response = catalog.invoke(tool, ToolContext("workflow-run", "acme-retail", "actor", role), {})
        evidence.record_tool_response(response)
    sessions = {role: AgentSession(FakeClient(), role) for role in ("telemetry-agent", "change-agent", "data-agent")}
    result = InvestigationCoordinator(engine, evidence, sessions).run(
        "workflow-run",
        objective="Investigate INC-2048",
        actor_id="actor",
    )
    assert result.succeeded
    assert len(result.task_results) == 3
    assert len(result.findings) == 3
    assert {finding.agent for finding in result.findings} == {"telemetry-agent", "change-agent", "data-agent"}


def test_parallel_coordinator_records_unrepairable_output_without_aborting() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="workflow-invalid-output")
    evidence = EvidenceStore(engine)
    sessions = {
        role: AgentSession(UnrepairableOutputClient(), role)
        for role in ("telemetry-agent", "change-agent", "data-agent")
    }

    result = InvestigationCoordinator(engine, evidence, sessions).run(
        "workflow-invalid-output",
        objective="Investigate INC-2048",
        actor_id="actor",
    )

    assert not result.succeeded
    assert len(result.task_results) == 3
    assert {task.status for task in result.task_results} == {"failed"}
    assert all("hypotheses[0] is missing statement" in (task.error or task.session_result.output_error) for task in result.task_results)
