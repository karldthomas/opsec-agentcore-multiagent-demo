"""Local/Gateway target adapter for the typed OpsFusion tool catalog."""

from __future__ import annotations

from typing import Any

from .tool_catalog import ToolCatalog, ToolContext


def invoke_tool(
    catalog: ToolCatalog,
    *,
    name: str,
    run_id: str,
    tenant_id: str,
    actor_id: str,
    role: str,
    parameters: dict[str, Any] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    response = catalog.invoke(
        name,
        ToolContext(run_id=run_id, tenant_id=tenant_id, actor_id=actor_id, role=role, request_id=request_id),
        parameters,
    )
    return response.to_dict()
