"""Generate the bounded OpenAPI tool contract used by Gateway provisioning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tool_catalog import ToolCatalog


def build_openapi(catalog: ToolCatalog | None = None) -> dict[str, Any]:
    catalog = catalog or ToolCatalog()
    paths: dict[str, Any] = {}
    for spec in catalog.specs():
        if not spec.read_only:
            continue
        paths[f"/tools/{spec.name}"] = {
            "post": {
                "operationId": spec.name,
                "summary": spec.description,
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": {"200": {"description": "Structured OpsFusion tool response."}},
            }
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "OpsFusion synthetic operational tools", "version": "1.0.0"},
        "paths": paths,
    }


def write_openapi(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
