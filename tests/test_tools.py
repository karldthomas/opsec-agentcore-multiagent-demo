from __future__ import annotations

from gateway.tool_catalog import ToolCatalog, ToolContext
from simulator.engine import ScenarioEngine


def _context(role: str = "telemetry-agent") -> ToolContext:
    return ToolContext(run_id="tool-run", tenant_id="acme-retail", actor_id="actor-1", role=role, request_id="req-tool")


def test_catalog_has_more_than_fifteen_bounded_tools() -> None:
    catalog = ToolCatalog(ScenarioEngine())
    assert len(catalog.names()) >= 15
    assert "query_cloudwatch_metrics" in catalog.names()
    assert "execute_approved_remediation" not in catalog.names()


def test_read_only_observation_contains_evidence_not_a_root_cause() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="tool-run")
    catalog = ToolCatalog(engine)
    response = catalog.invoke(
        "get_database_metrics",
        _context("data-agent"),
        {},
    )
    payload = response.to_dict()
    assert response.status == "ok"
    assert payload["data"]["observations"][0]["connections"] == 500
    assert "hidden_root_cause" not in response.to_json()
    assert payload["evidence"][0]["source_record_id"] == "DB-2048-001"


def test_service_health_observation_has_stable_evidence_provenance() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="tool-run")
    catalog = ToolCatalog(engine)

    response = catalog.invoke("get_service_health", _context(), {})

    assert response.status == "ok"
    assert response.evidence[0].evidence_id == "EVD-SERVICE-2048-001"
    assert response.evidence[0].source_type == "service_health"


def test_state_changing_tool_is_not_registered() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="tool-run")
    catalog = ToolCatalog(engine)
    response = catalog.invoke(
        "execute_approved_remediation",
        _context("telemetry-agent"),
        {"action": "rollback_deployment", "approval_id": "a", "idempotency_key": "b"},
    )
    assert response.status == "error"
    assert response.errors[0]["code"] == "TOOL_NOT_FOUND"
