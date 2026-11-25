"""Small same-origin operator API for the OpsFusion console.

The local runtime uses the deterministic stub sessions.  The deployed worker
configuration injects real AgentCore Harness sessions behind the same workflow
boundary; this API never exposes hidden scenario state or private reasoning.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents.session import AgentSession
from agents.stub import LocalStubClient
from agents.harness import AgentCoreHarnessClient
from agents.roles import AGENT_ROLES
from evaluation import DeterministicEvaluator
from evidence import EvidenceStore, EvidenceStoreError
from gateway.tool_catalog import ToolCatalog
from simulator.engine import ScenarioEngine, SimulatorError
from simulator.runtime import build_engine
from workflow import (
    DurableInvestigationWorkflow,
    DynamoCheckpointStore,
    InMemoryCheckpointStore,
    WorkflowError,
)
from workflow.local import InvestigationCoordinator

from .auth import Principal, require_http_auth
from .auth_routes import router as auth_router


logger = logging.getLogger(__name__)


class ApplicationRuntime:
    def __init__(self) -> None:
        self.engine = build_engine()
        self.evidence = self._build_evidence()
        self.catalog = ToolCatalog(self.engine, evidence=self.evidence)
        self.checkpoints = self._build_checkpoints()
        self.registry_resolver = self._build_registry_resolver()

    def _build_evidence(self) -> EvidenceStore:
        table_name = str(os.getenv("OPSFUSION_EVIDENCE_TABLE") or "").strip()
        use_dynamo = str(os.getenv("OPSFUSION_USE_DYNAMODB") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if table_name and use_dynamo:
            return EvidenceStore.from_dynamodb(
                self.engine,
                table_name,
                region_name=os.getenv("AWS_REGION", "us-east-1"),
            )
        return EvidenceStore(self.engine)

    @staticmethod
    def _build_checkpoints():
        table_name = str(os.getenv("OPSFUSION_STATE_TABLE") or "").strip()
        use_dynamo = str(os.getenv("OPSFUSION_USE_DYNAMODB") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if table_name and use_dynamo:
            return DynamoCheckpointStore(table_name, region_name=os.getenv("AWS_REGION", "us-east-1"))
        return InMemoryCheckpointStore()

    @staticmethod
    def _build_registry_resolver():
        registry_id = str(os.getenv("OPSFUSION_REGISTRY_ID") or "").strip()
        if not registry_id:
            return None
        from registry.client import RegistryClient
        from registry.discovery import RegistryResolver

        client = RegistryClient(region=os.getenv("AWS_REGION", "us-east-1"))
        return RegistryResolver(
            client,
            registry_id=registry_id,
            environment=os.getenv("OPSFUSION_ENVIRONMENT", "dev"),
        )

    @property
    def execution_mode(self) -> str:
        return "agentcore-harness" if os.getenv("OPSFUSION_HARNESS_ARN") else "synthetic-stub"

    def client(self):
        harness_arn = str(os.getenv("OPSFUSION_HARNESS_ARN") or "").strip()
        if harness_arn:
            return AgentCoreHarnessClient(
                harness_arn,
                region=os.getenv("AWS_REGION", "us-east-1"),
                gateway_arn=os.getenv("OPSFUSION_GATEWAY_ARN"),
            )
        return LocalStubClient(self.catalog, self.evidence)

    def workflow(self) -> DurableInvestigationWorkflow:
        client = self.client()
        sessions = {
            role: AgentSession(
                client,
                role,
                registry_resolver=self.registry_resolver,
                catalog=self.catalog,
                evidence=self.evidence,
            )
            for role in (
                "telemetry-agent",
                "change-agent",
                "data-agent",
            )
        }
        coordinator = InvestigationCoordinator(
            self.engine,
            self.evidence,
            {role: sessions[role] for role in ("telemetry-agent", "change-agent", "data-agent")},
        )
        return DurableInvestigationWorkflow(
            self.engine,
            self.evidence,
            coordinator,
            self.checkpoints,
            orchestrator_session=AgentSession(
                client,
                "orchestrator",
                registry_resolver=self.registry_resolver,
                catalog=self.catalog,
                evidence=self.evidence,
            ),
        )


runtime = ApplicationRuntime()
app = FastAPI(title="OpsFusion Operator API", version="0.1.0")
app.include_router(auth_router)


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": type(exc).__name__, "message": str(exc)})


def _agent_catalog() -> dict[str, dict[str, Any]]:
    """Expose inspectable role configuration without exposing model reasoning."""

    return {
        role_id: {
            "name": role.name,
            "skills": list(role.skills),
            "skill_definitions": list(role.skill_definitions()),
            "allowed_tools": list(role.allowed_tools),
            "system_prompt": role.system_prompt(),
        }
        for role_id, role in AGENT_ROLES.items()
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "opsfusion-api", "mode": runtime.execution_mode}


@app.get("/api/scenarios")
def list_scenarios(principal: Principal | None = Depends(require_http_auth)) -> dict[str, Any]:
    return {"scenarios": runtime.engine.list_scenarios()}


@app.post("/api/runs")
def create_run(
    body: dict[str, Any],
    principal: Principal | None = Depends(require_http_auth),
) -> dict[str, Any]:
    try:
        run = runtime.engine.create_run(
            str(body.get("scenario_id") or ""),
            run_id=body.get("run_id"),
            actor_id=principal.username if principal else str(body.get("actor_id") or "local-operator"),
            tenant_id=body.get("tenant_id"),
            seed=body.get("seed"),
        )
        runtime.evidence.register_run(run.run_id, run.tenant_id)
        return {"run": run.to_dict(), "health": runtime.engine.service_health(run.run_id, tenant_id=run.tenant_id)}
    except SimulatorError as exc:
        raise _error(exc) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, principal: Principal | None = Depends(require_http_auth)) -> dict[str, Any]:
    try:
        state = runtime.engine.get_run(run_id)
        checkpoint = None
        try:
            persisted_checkpoint = runtime.checkpoints.get(run_id)
            if persisted_checkpoint.stage in DurableInvestigationWorkflow.LEGACY_ACTION_STAGES:
                # Migrate pre-analysis-only runs when they are opened. This is
                # a checkpoint-only transition: resume() deliberately does
                # not call any simulator mutation method for legacy stages.
                persisted_checkpoint = runtime.workflow().resume(
                    run_id,
                    checkpoint=persisted_checkpoint,
                ).checkpoint
            checkpoint = persisted_checkpoint.to_dict()
        except Exception:
            pass
        shared_evidence = runtime.evidence.get_shared_evidence(run_id, tenant_id=state.run.tenant_id)
        evaluation = None
        if checkpoint and checkpoint.get("stage") == "completed":
            evaluation = DeterministicEvaluator(runtime.engine, runtime.evidence).evaluate(run_id).to_dict()
        return {
            "run": state.run.to_dict(),
            "health": runtime.engine.service_health(run_id, tenant_id=state.run.tenant_id),
            "checkpoint": checkpoint,
            "evidence": shared_evidence,
            "evaluation": evaluation,
            "agent_catalog": _agent_catalog(),
        }
    except (SimulatorError, KeyError) as exc:
        raise _error(exc) from exc


@app.post("/api/runs/{run_id}/investigate")
def investigate(
    run_id: str,
    body: dict[str, Any],
    principal: Principal | None = Depends(require_http_auth),
) -> dict[str, Any]:
    try:
        workflow = runtime.workflow()
        result = workflow.start(
            run_id,
            objective=str(body.get("objective") or f"Investigate {run_id}"),
            actor_id=principal.username if principal else str(body.get("actor_id") or "local-operator"),
            incident_id=body.get("incident_id"),
        )
        return {"status": result.status, "message": result.error, "checkpoint": result.checkpoint.to_dict()}
    except (SimulatorError, WorkflowError, EvidenceStoreError) as exc:
        raise _error(exc) from exc


@app.post("/api/runs/{run_id}/follow-up")
def follow_up(
    run_id: str,
    body: dict[str, Any],
    principal: Principal | None = Depends(require_http_auth),
) -> dict[str, Any]:
    """Resume an evidence-gap investigation without repeating completed tracks."""

    try:
        workflow = runtime.workflow()
        requests = body.get("requests")
        selected = list(requests) if isinstance(requests, list) else None
        result = workflow.request_follow_up(run_id, selected)
        return {"status": result.status, "message": result.error, "checkpoint": result.checkpoint.to_dict()}
    except (SimulatorError, WorkflowError, EvidenceStoreError) as exc:
        raise _error(exc) from exc
    except Exception as exc:  # pragma: no cover - provider/storage boundary.
        logger.exception("follow-up failed before the durable checkpoint was returned", extra={"run_id": run_id})
        raise HTTPException(
            status_code=500,
            detail={
                "code": "FOLLOW_UP_CHECKPOINT_FAILED",
                "message": "Decision checks could not save their checkpoint. No service change was made; reload the case and try again.",
            },
        ) from exc


WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def _web_console_enabled() -> bool:
    return (
        os.environ.get("OPSFUSION_SERVE_WEB", "false").strip().lower() in {"1", "true", "yes", "on"}
        and (WEB_DIST / "index.html").is_file()
    )


@app.get("/")
def root():
    """Serve the authenticated console when enabled in the deployed API."""

    if _web_console_enabled():
        return FileResponse(
            WEB_DIST / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return {
        "name": "opsfusion",
        "status": "synthetic-incident-investigation-simulator",
        "auth_required": os.environ.get("OPSFUSION_AUTH_REQUIRED", "false").lower() in {"1", "true", "yes", "on"},
    }


if _web_console_enabled():
    # Specific API/auth routes are registered above this fallback mount. The
    # mount only serves the static console bundle and tutorial artifact.
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="opsfusion-web")


try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover
    handler = None
