from __future__ import annotations

from types import SimpleNamespace

from gateway import lambda_handler
from gateway.tool_catalog import ToolCatalog
from simulator.engine import ScenarioEngine


def test_gateway_lambda_accepts_agentcore_lambda_target_shape() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="gateway-shape-run")
    lambda_handler.engine = engine
    lambda_handler.catalog = ToolCatalog(engine)
    context = SimpleNamespace(
        aws_request_id="lambda-request",
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreToolName": "opsfusion-simulator___get_incident_context",
                "bedrockAgentCoreAwsRequestId": "gateway-request",
            }
        ),
    )

    response = lambda_handler.handler(
        {
            "run_id": "gateway-shape-run",
            "tenant_id": "acme-retail",
            "actor_id": "operator-1",
            "role": "telemetry-agent",
        },
        context,
    )

    assert response["status"] == "ok"
    assert response["tool_name"] == "get_incident_context"
    assert response["scenario_run_id"] == "gateway-shape-run"
    assert response["request_id"] == "gateway-request"
