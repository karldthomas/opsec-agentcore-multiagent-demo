"""FastAPI adapter for the synthetic simulator and local Gateway target."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException

from gateway.tool_catalog import ToolCatalog, ToolContext

from .engine import SimulatorError
from .runtime import build_engine


engine = build_engine()
catalog = ToolCatalog(engine)
app = FastAPI(title="OpsFusion Synthetic Simulator", version="0.1.0")


def _tenant(value: str | None) -> str:
    return str(value or "acme-retail").strip() or "acme-retail"


def _actor(value: str | None) -> str:
    return str(value or "local-operator").strip() or "local-operator"


def _handle(error: SimulatorError) -> HTTPException:
    status = {
        "RUN_NOT_FOUND": 404,
        "SCENARIO_NOT_FOUND": 404,
        "TENANT_ACCESS_DENIED": 403,
        "RUN_ACCESS_DENIED": 403,
    }.get(error.code, 400)
    return HTTPException(status_code=status, detail={"code": error.code, "message": str(error)})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "opsfusion-simulator"}


@app.get("/scenarios")
def scenarios() -> dict[str, Any]:
    return {"scenarios": engine.list_scenarios()}


@app.post("/runs")
def create_run(
    body: dict[str, Any],
    x_opsfusion_tenant_id: str | None = Header(default=None),
    x_opsfusion_actor_id: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        run = engine.create_run(
            str(body.get("scenario_id") or ""),
            run_id=body.get("run_id"),
            actor_id=_actor(x_opsfusion_actor_id),
            tenant_id=_tenant(x_opsfusion_tenant_id),
            seed=body.get("seed"),
        )
        return {"run": run.to_dict()}
    except SimulatorError as exc:
        raise _handle(exc) from exc


@app.get("/runs/{run_id}")
def get_run(run_id: str, x_opsfusion_tenant_id: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        state = engine.get_run(run_id, tenant_id=_tenant(x_opsfusion_tenant_id))
        return {"run": state.run.to_dict(), "health": engine.service_health(run_id, tenant_id=_tenant(x_opsfusion_tenant_id))}
    except SimulatorError as exc:
        raise _handle(exc) from exc


@app.post("/runs/{run_id}/tools/{tool_name}")
def invoke_tool(
    run_id: str,
    tool_name: str,
    body: dict[str, Any],
    x_opsfusion_tenant_id: str | None = Header(default=None),
    x_opsfusion_actor_id: str | None = Header(default=None),
) -> dict[str, Any]:
    tenant_id = _tenant(x_opsfusion_tenant_id)
    response = catalog.invoke(
        tool_name,
        ToolContext(
            run_id=run_id,
            tenant_id=tenant_id,
            actor_id=_actor(x_opsfusion_actor_id),
            role=str(body.pop("role", "orchestrator")),
            request_id=str(body.pop("request_id", "")),
        ),
        body,
    )
    return response.to_dict()


try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover - local contract tests do not require Mangum.
    handler = None
