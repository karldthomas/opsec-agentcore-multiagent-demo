from __future__ import annotations

from fastapi.testclient import TestClient

from simulator.api import app, engine


def setup_function() -> None:
    engine.runs.clear()


def test_simulator_api_exposes_run_and_tool_contracts() -> None:
    client = TestClient(app)
    created = client.post(
        "/runs",
        json={"scenario_id": "INC-2048", "run_id": "api-run"},
        headers={"x-opsfusion-tenant-id": "acme-retail"},
    )
    assert created.status_code == 200
    assert created.json()["run"]["scenario_id"] == "INC-2048"

    response = client.post(
        "/runs/api-run/tools/get_database_metrics",
        json={"role": "data-agent"},
        headers={"x-opsfusion-tenant-id": "acme-retail"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["observations"][0]["max_connections"] == 500


def test_simulator_api_rejects_cross_tenant_reads() -> None:
    client = TestClient(app)
    client.post(
        "/runs",
        json={"scenario_id": "INC-2048", "run_id": "api-run"},
        headers={"x-opsfusion-tenant-id": "acme-retail"},
    )
    response = client.get(
        "/runs/api-run",
        headers={"x-opsfusion-tenant-id": "other-tenant"},
    )
    assert response.status_code == 403
