from __future__ import annotations

import pytest

from simulator.engine import ScenarioEngine, SimulatorError


def test_all_scenarios_are_available_without_exposing_ground_truth() -> None:
    engine = ScenarioEngine()
    scenarios = engine.list_scenarios()
    assert {item["scenario_id"] for item in scenarios} == {"INC-2048", "INC-2051", "INC-2054", "INC-2057"}
    assert all("hidden_root_cause" not in item for item in scenarios)


def test_primary_scenario_is_deterministic_and_observable() -> None:
    engine = ScenarioEngine()
    first = engine.create_run("INC-2048", run_id="run-one", actor_id="operator")
    second_engine = ScenarioEngine()
    second = second_engine.create_run("INC-2048", run_id="run-two", actor_id="operator")
    first_metrics = engine.observe(first.run_id, "metrics")
    second_metrics = second_engine.observe(second.run_id, "metrics")
    assert first_metrics == second_metrics
    assert any(
        item["record_id"] == "DB-2048-001"
        for item in engine.observe(first.run_id, "database_metrics")
    )
    assert engine.hidden_truth(first.run_id)["hidden_root_cause"] == "database_connection_pool_regression"
    assert all("hidden_root_cause" not in record for record in first_metrics)


def test_tenant_boundary_is_enforced() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="run-tenant", tenant_id="acme-retail")
    with pytest.raises(SimulatorError, match="tenant"):
        engine.get_run("run-tenant", tenant_id="other-tenant")


def test_incident_observations_are_read_only() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="run-analysis")
    before = engine.service_health("run-analysis")
    engine.observe("run-analysis", "metrics")
    engine.observe("run-analysis", "queue_metrics")
    after = engine.service_health("run-analysis")
    assert before == after
    assert not hasattr(engine.get_run("run-analysis"), "operation_id")
