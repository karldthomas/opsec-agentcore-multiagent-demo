"""Deterministic local Harness substitute for contract tests and the console demo.

This is explicitly opt-in.  Cloud and integration runs use the managed
AgentCore Harness client; the stub only makes the synthetic vertical slice
repeatable without model calls or AWS credentials.
"""

from __future__ import annotations

import json
from typing import Any

from evidence import EvidenceStore
from gateway.tool_catalog import ToolCatalog, ToolContext

from .harness import HarnessInvocationResult, InvocationContext
from .roles import AGENT_ROLES


class LocalStubClient:
    def __init__(self, catalog: ToolCatalog, evidence: EvidenceStore) -> None:
        self.catalog = catalog
        self.evidence = evidence

    def _call(self, context: InvocationContext, tool_name: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.catalog.invoke(
            tool_name,
            ToolContext(context.scenario_run_id, context.tenant_id, context.actor_id, context.role),
            parameters or {},
        )
        if response.status == "ok":
            self.evidence.record_tool_response(response)
        return response.to_dict()

    @staticmethod
    def _evidence_ids(*responses: dict[str, Any]) -> list[str]:
        return [
            str(item["evidence_id"])
            for response in responses
            for item in response.get("evidence", [])
        ]

    def invoke(self, context: InvocationContext, prompt: str) -> HarnessInvocationResult:
        role = context.role
        responses: list[dict[str, Any]] = []
        if role == "telemetry-agent":
            responses = [
                self._call(context, "query_cloudwatch_metrics", {"service": "orders-service"}),
                self._call(context, "search_application_logs", {"query": "timeout"}),
                self._call(context, "get_active_alarms"),
            ]
            statement = "Telemetry shows degradation and timeout observations that should be ordered by timestamp."
        elif role == "change-agent":
            responses = [
                self._call(context, "list_recent_deployments"),
                self._call(context, "get_configuration_changes"),
                self._call(context, "search_runbooks", {"query": "rollback"}),
            ]
            statement = "A recent deployment includes an observable configuration change temporally consistent with the incident."
        elif role == "data-agent":
            responses = [
                self._call(context, "get_database_metrics"),
                self._call(context, "get_queue_metrics"),
                self._call(context, "calculate_customer_impact"),
            ]
            statement = "Database, queue, and order observations show service impact and delayed business processing."
        else:
            statement = "The orchestrator coordinates specialist evidence and workflow state."

        finding = {
            "claim_type": "fact",
            "statement": statement,
            "evidence_ids": self._evidence_ids(*responses),
            "confidence": 0.85,
        }
        payload: dict[str, Any] = {
            "findings": [finding] if finding["evidence_ids"] else [],
            "hypotheses": [],
            "unresolved_questions": [],
            "follow_up_requests": [],
            "recommendations": [],
        }
        if role == "change-agent":
            payload["hypotheses"] = [{
                "statement": "The deployment change is a plausible contributor and should be compared with the telemetry ordering before action.",
                "supporting_finding_ids": [],
                "contradicting_finding_ids": [],
                "confidence": 0.78,
            }]
        result = HarnessInvocationResult(
            context,
            json.dumps(payload),
            "end_turn",
            (),
            (),
            {},
            {},
            (),
            {
                "skills": list(AGENT_ROLES[role].skills),
                "definitions": list(AGENT_ROLES[role].skill_definitions()),
                "status": "not_applied",
                "applied_via": "deterministic local stub",
                "authority": "reference-only",
                "direct_harness_attachments": [],
            },
            {
                "status": "not_applied",
                "system_prompt": "",
                "user_prompt": prompt,
                "system_prompt_source": "deterministic local stub",
            },
        )
        return result
