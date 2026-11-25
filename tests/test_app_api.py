from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app import app, runtime
from workflow import WorkflowCheckpoint


def test_operator_api_stops_at_read_only_analysis() -> None:
    client = TestClient(app)
    run_id = "api-analysis-run"
    created = client.post("/api/runs", json={"scenario_id": "INC-2048", "run_id": run_id})
    assert created.status_code == 200
    investigation = client.post(f"/api/runs/{run_id}/investigate", json={"actor_id": "operator"})
    assert investigation.status_code == 200
    assert investigation.json()["status"] == "completed"
    status = client.get(f"/api/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["evaluation"] is not None
    assert status.json()["evaluation"]["checks"]["read_only_boundary"]["passed"] is True
    assert not hasattr(runtime.engine.get_run(run_id), "operation_id")
    telemetry = status.json()["agent_catalog"]["telemetry-agent"]
    assert telemetry["skill_definitions"][0]["name"] == "telemetry-triage"
    assert "Reference skill instructions" in telemetry["system_prompt"]
    assert set(status.json()["agent_catalog"]) == {"orchestrator", "telemetry-agent", "change-agent", "data-agent"}


def test_operator_api_does_not_expose_state_changing_routes() -> None:
    client = TestClient(app)
    for path in (
        "/api/runs/analysis-only/plan",
        "/api/runs/analysis-only/approvals",
        "/api/approvals/approval/decision",
        "/api/runs/analysis-only/execute",
        "/api/runs/analysis-only/advance",
        "/api/runs/analysis-only/verify",
    ):
        assert client.post(path, json={}).status_code == 404


def test_loading_a_legacy_action_checkpoint_migrates_to_analysis() -> None:
    client = TestClient(app)
    run_id = "api-legacy-analysis-run"
    created = client.post("/api/runs", json={"scenario_id": "INC-2048", "run_id": run_id})
    assert created.status_code == 200
    runtime.checkpoints.create(
        WorkflowCheckpoint(
            run_id=run_id,
            stage="awaiting_approval",
            metadata={"legacy_action": "rollback"},
        )
    )

    status = client.get(f"/api/runs/{run_id}")

    assert status.status_code == 200
    assert status.json()["checkpoint"]["stage"] == "completed"
    assert status.json()["evaluation"]["checks"]["read_only_boundary"]["passed"] is True
