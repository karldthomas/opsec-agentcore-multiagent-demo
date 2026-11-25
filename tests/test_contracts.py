from __future__ import annotations

import json

import pytest

from contracts import ContractError, EvidenceReference, Finding, Hypothesis, ToolResponse
from contracts.serialization import safe_json


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id="METRIC-DB-001",
        source_type="database_metrics",
        source_record_id="db-001",
        scenario_run_id="run-1",
        tenant_id="acme-retail",
        observed_at="2026-11-23T14:00:00Z",
        query={"metric": "connections"},
    )


def test_finding_requires_evidence_and_serializes() -> None:
    finding = Finding(
        finding_id="FIND-001",
        agent="telemetry-agent",
        claim_type="fact",
        statement="Database connections reached the configured maximum.",
        evidence_ids=("METRIC-DB-001",),
        confidence=0.98,
        scenario_run_id="run-1",
        tenant_id="acme-retail",
    )
    payload = finding.to_dict()
    assert payload["contract_version"] == "opsfusion.v1"
    assert payload["evidence_ids"] == ["METRIC-DB-001"]


def test_hypothesis_requires_supporting_findings() -> None:
    with pytest.raises(ContractError, match="supporting finding"):
        Hypothesis(
            hypothesis_id="H-001",
            statement="A deployment caused the incident.",
            supporting_finding_ids=(),
            contradicting_finding_ids=(),
            confidence=0.5,
            scenario_run_id="run-1",
            tenant_id="acme-retail",
        )


def test_tool_response_rejects_hidden_root_cause() -> None:
    with pytest.raises(ContractError, match="hidden evaluator field"):
        ToolResponse(
            request_id="req-1",
            tool_name="query_cloudwatch_metrics",
            scenario_run_id="run-1",
            tenant_id="acme-retail",
            status="ok",
            data={"hidden_root_cause": "database_connection_pool_regression"},
        )


def test_safe_json_rejects_nested_evaluator_fields() -> None:
    with pytest.raises(ContractError, match="hidden evaluator field"):
        safe_json({"observations": [{"expected_outcome": "rollback"}]})


def test_tool_response_is_json_serializable() -> None:
    response = ToolResponse(
        request_id="req-1",
        tool_name="get_database_metrics",
        scenario_run_id="run-1",
        tenant_id="acme-retail",
        status="ok",
        data={"connections": 500, "max_connections": 500},
        evidence=(_evidence(),),
    )
    decoded = json.loads(response.to_json())
    assert decoded["data"]["connections"] == 500
    assert decoded["evidence"][0]["source_type"] == "database_metrics"
