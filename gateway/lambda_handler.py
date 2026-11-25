"""Lambda entry point for an Amazon Bedrock AgentCore Gateway MCP target.

AgentCore Lambda targets receive the tool properties directly as the event
object. The fully qualified tool name is supplied in Lambda client context,
for example ``opsfusion-simulator___get_incident_context``. This adapter
normalizes that managed shape while retaining a small local-test envelope
fallback.
"""

from __future__ import annotations

import os
from typing import Any

from .tool_catalog import ToolCatalog, ToolContext
from simulator.runtime import build_engine
from evidence import EvidenceStore


engine = build_engine()
_evidence_table = str(os.getenv("OPSFUSION_EVIDENCE_TABLE") or "").strip()
_use_dynamo = str(os.getenv("OPSFUSION_USE_DYNAMODB") or "").strip().lower() in {"1", "true", "yes", "on"}
if _evidence_table and _use_dynamo:
    evidence = EvidenceStore.from_dynamodb(engine, _evidence_table, region_name=os.getenv("AWS_REGION", "us-east-1"))
else:
    evidence = EvidenceStore(engine)
catalog = ToolCatalog(engine, evidence=evidence)


def _agentcore_context(context: Any) -> dict[str, Any]:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None)
    return dict(custom) if isinstance(custom, dict) else {}


def _tool_name(event: dict[str, Any], context: Any) -> str:
    custom = _agentcore_context(context)
    value = (
        event.get("tool_name")
        or event.get("toolName")
        or event.get("name")
        or event.get("operation")
        or custom.get("bedrockAgentCoreToolName")
    )
    name = str(value or "").strip()
    if "___" in name:
        name = name.rsplit("___", 1)[1]
    return name


def _role(value: Any) -> str:
    aliases = {
        "incident orchestrator": "orchestrator",
        "telemetry investigation agent": "telemetry-agent",
        "change analysis agent": "change-agent",
        "data and business impact agent": "data-agent",
    }
    normalized = str(value or "").strip()
    return aliases.get(normalized.casefold(), normalized)


def _arguments(event: dict[str, Any]) -> dict[str, Any]:
    nested = event.get("arguments") or event.get("input") or event.get("parameters")
    if nested is None:
        nested = event
    if isinstance(nested, str):
        import json

        nested = json.loads(nested)
    return dict(nested) if isinstance(nested, dict) else {}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request = event if isinstance(event, dict) else {}
    custom = _agentcore_context(context)
    tool_name = _tool_name(request, context)
    arguments = _arguments(request)
    run_id = str(arguments.pop("run_id", custom.get("run_id", ""))).strip()
    tenant_id = str(arguments.pop("tenant_id", custom.get("tenant_id", "acme-retail"))).strip()
    actor_id = str(arguments.pop("actor_id", custom.get("actor_id", "agentcore"))).strip()
    role = _role(arguments.pop("role", custom.get("role", "")))
    request_id = str(
        request.get("requestId")
        or request.get("request_id")
        or custom.get("bedrockAgentCoreAwsRequestId")
        or getattr(context, "aws_request_id", "")
        or ""
    ).strip()
    if not tool_name:
        return {"status": "error", "error": {"code": "TOOL_NAME_REQUIRED", "message": "Gateway tool name is required"}}
    if not run_id:
        return {"status": "error", "error": {"code": "RUN_ID_REQUIRED", "message": "run_id is required"}}
    if not role:
        return {"status": "error", "error": {"code": "ROLE_REQUIRED", "message": "role is required"}}
    return catalog.invoke(
        tool_name,
        ToolContext(run_id, tenant_id, actor_id, role, request_id),
        arguments,
    ).to_dict()
