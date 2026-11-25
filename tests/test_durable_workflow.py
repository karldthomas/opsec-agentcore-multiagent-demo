from __future__ import annotations

import json

import pytest

from agents.harness import HarnessInvocationResult, InvocationContext
from agents.session import AgentSession
from evidence import EvidenceStore
from gateway.tool_catalog import ToolCatalog, ToolContext
from simulator.engine import ScenarioEngine
from workflow import (
    DurableInvestigationWorkflow,
    InMemoryCheckpointStore,
    WorkflowInterrupted,
    WorkflowCheckpoint,
)
from workflow.local import InvestigationCoordinator


class DurableFakeClient:
    def invoke(self, context: InvocationContext, prompt: str) -> HarnessInvocationResult:
        evidence_id = {
            "telemetry-agent": "EVD-METRIC-API-2048",
            "change-agent": "EVD-DEP-2048-NEW",
            "data-agent": "EVD-DB-2048-001",
        }.get(context.role, "EVD-DEP-2048-NEW")
        hypothesis = (
            [{
                "statement": "The deployment change and connection saturation are consistent with a regression.",
                "supporting_finding_ids": [],
                "contradicting_finding_ids": [],
                "confidence": 0.8,
            }]
            if context.role == "change-agent"
            else []
        )
        payload = {
            "findings": [{
                "claim_type": "fact",
                "statement": f"{context.role} observed a source record.",
                "evidence_ids": [evidence_id],
                "confidence": 0.9,
            }],
            "hypotheses": hypothesis,
            "unresolved_questions": [],
            "follow_up_requests": [],
            "recommendations": [],
        }
        return HarnessInvocationResult(context, json.dumps(payload), "end_turn", (), (), {}, {}, ())


class PartiallyInvalidClient(DurableFakeClient):
    def invoke(self, context: InvocationContext, prompt: str) -> HarnessInvocationResult:
        if context.role != "telemetry-agent":
            return super().invoke(context, prompt)
        return HarnessInvocationResult(
            context,
            json.dumps(
                {
                    "findings": [],
                    "hypotheses": [{"confidence": 0.8}],
                    "unresolved_questions": [],
                    "follow_up_requests": [],
                    "recommendations": [],
                }
            ),
            "end_turn",
            (),
            (),
            {},
            {},
            (),
        )


class OneTimeFollowUpClient(DurableFakeClient):
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def invoke(self, context: InvocationContext, prompt: str) -> HarnessInvocationResult:
        self.calls[context.role] = self.calls.get(context.role, 0) + 1
        result = super().invoke(context, prompt)
        if context.role != "telemetry-agent" or self.calls[context.role] not in {1, 2}:
            return result
        payload = json.loads(result.text)
        if self.calls[context.role] == 1:
            payload["follow_up_requests"] = [{
                "agent": "telemetry-agent",
                "objective": "Confirm the timeout and resource-pressure evidence before choosing recovery.",
            }]
        else:
            payload["follow_up_requests"] = [{
                "agent": "change-agent",
                "objective": "Request another deployment check after the evidence-gap check.",
            }]
        return HarnessInvocationResult(
            context,
            json.dumps(payload),
            result.stop_reason,
            result.events,
            result.tool_uses,
            result.usage,
            result.metrics,
            result.errors,
        )


def _workflow(client_type=DurableFakeClient) -> tuple[ScenarioEngine, EvidenceStore, DurableInvestigationWorkflow]:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="durable-run")
    catalog = ToolCatalog(engine)
    evidence = EvidenceStore(engine)
    for role, tool in (
        ("telemetry-agent", "query_cloudwatch_metrics"),
        ("change-agent", "list_recent_deployments"),
        ("data-agent", "get_database_metrics"),
    ):
        response = catalog.invoke(tool, ToolContext("durable-run", "acme-retail", "operator", role), {})
        evidence.record_tool_response(response)
    sessions = {
        role: AgentSession(client_type(), role)
        for role in ("telemetry-agent", "change-agent", "data-agent")
    }
    coordinator = InvestigationCoordinator(engine, evidence, sessions)
    workflow = DurableInvestigationWorkflow(
        engine,
        evidence,
        coordinator,
        InMemoryCheckpointStore(),
    )
    return engine, evidence, workflow


def test_failed_specialist_is_saved_as_a_follow_up_instead_of_advancing() -> None:
    engine, evidence, workflow = _workflow(PartiallyInvalidClient)

    result = workflow.start("durable-run", objective="Investigate INC-2048", actor_id="operator")

    assert result.status == "needs_follow_up"
    checkpoint = workflow.checkpoints.get("durable-run")
    assert checkpoint.stage == "awaiting_follow_up"
    assert checkpoint.metadata["follow_up_requests"] == [
        {
            "agent": "telemetry-agent",
            "objective": "Confirm service health, resource pressure, and timeout evidence before analysis is complete.",
        }
    ]
    assert any(task["status"] == "failed" for task in checkpoint.task_results.values())

    retry = workflow.request_follow_up("durable-run")

    assert retry.status == "needs_follow_up"
    retry_checkpoint = workflow.checkpoints.get("durable-run")
    assert retry_checkpoint.stage == "awaiting_follow_up"
    assert retry_checkpoint.metadata["follow_up_requests"] == [
        {
            "agent": "telemetry-agent",
            "objective": "Confirm service health, resource pressure, and timeout evidence before analysis is complete.",
        }
    ]


def test_read_only_follow_up_runs_automatically_before_analysis_completes() -> None:
    _engine, _evidence, workflow = _workflow(OneTimeFollowUpClient)

    result = workflow.start("durable-run", objective="Investigate INC-2048", actor_id="operator")

    assert result.status == "completed"
    checkpoint = workflow.checkpoints.get("durable-run")
    assert checkpoint.stage == "completed"
    assert "follow_up_investigation" in checkpoint.completed_stages
    assert checkpoint.metadata["follow_up_requests"] == []
    assert {item["role"] for item in checkpoint.metadata["follow_up_results"]} == {"telemetry-agent"}


def test_interruption_resumes_after_persisted_parallel_stage() -> None:
    engine, evidence, workflow = _workflow()
    engine.inject_failure("durable-run", "after_telemetry")
    with pytest.raises(WorkflowInterrupted):
        workflow.start("durable-run", objective="Investigate INC-2048", actor_id="operator")

    checkpoint = workflow.checkpoints.get("durable-run")
    assert checkpoint.stage == "reconcile_evidence"
    assert len(checkpoint.task_results) == 3
    assert len(evidence.runs["durable-run"].findings) == 3

    resumed = workflow.resume("durable-run")
    assert resumed.status == "completed"
    assert resumed.checkpoint.completed_stages.count("parallel_investigation") == 1
    assert len(evidence.runs["durable-run"].findings) == 3


def test_successful_follow_up_commits_and_advances_from_the_saved_checkpoint() -> None:
    engine, evidence, workflow = _workflow()
    workflow.checkpoints.create(
        WorkflowCheckpoint(
            run_id="durable-run",
            stage="awaiting_follow_up",
            completed_stages=("parallel_investigation",),
            metadata={
                "objective": "Investigate INC-2048",
                "actor_id": "operator",
                "tenant_id": "acme-retail",
                "incident_id": "INC-2048",
                "follow_up_requests": [
                    {
                        "agent": "change-agent",
                        "objective": "Confirm the deployment configuration before analysis is complete.",
                    }
                ],
            },
        )
    )

    result = workflow.request_follow_up("durable-run")

    assert result.status == "completed"
    checkpoint = workflow.checkpoints.get("durable-run")
    assert checkpoint.stage == "completed"
    assert checkpoint.completed_stages == (
        "parallel_investigation",
        "follow_up_investigation",
        "reconcile_evidence",
        "analysis_complete",
    )
    assert checkpoint.metadata["follow_up_requests"] == []
    assert evidence.runs["durable-run"].findings


def test_legacy_action_checkpoint_is_migrated_to_analysis_without_mutation() -> None:
    engine, _evidence, workflow = _workflow()
    before = engine.service_health("durable-run")
    workflow.checkpoints.create(
        WorkflowCheckpoint(
            run_id="durable-run",
            stage="awaiting_approval",
            metadata={"legacy_action": "rollback"},
        )
    )

    result = workflow.resume("durable-run")

    assert result.status == "completed"
    checkpoint = workflow.checkpoints.get("durable-run")
    assert checkpoint.stage == "completed"
    assert checkpoint.metadata["analysis_complete"] is True
    assert checkpoint.metadata["read_only_mode"] is True
    assert checkpoint.metadata["legacy_action_stage"] == "awaiting_approval"
    assert engine.service_health("durable-run") == before


def test_legacy_customer_impact_role_is_routed_to_data_agent() -> None:
    _engine, _evidence, workflow = _workflow()
    workflow.checkpoints.create(
        WorkflowCheckpoint(
            run_id="durable-run",
            stage="awaiting_follow_up",
            completed_stages=("parallel_investigation",),
            metadata={
                "objective": "Investigate INC-2048",
                "actor_id": "operator",
                "tenant_id": "acme-retail",
                "incident_id": "INC-2048",
                "follow_up_requests": [
                    {
                        "agent": "customer-impact-agent",
                        "objective": "Calculate affected order volume and SLA breach percentages",
                    }
                ],
            },
        )
    )

    result = workflow.request_follow_up("durable-run")

    assert result.status == "completed"
    checkpoint = workflow.checkpoints.get("durable-run")
    assert checkpoint.metadata["follow_up_results"][0]["role"] == "data-agent"
