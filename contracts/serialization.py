"""Helpers for safely serializing contract payloads."""

from __future__ import annotations

import json
from typing import Any

from .models import ContractError, _validate_no_hidden_keys


def safe_json(payload: Any) -> str:
    """Serialize an agent-facing payload after enforcing the hidden-state boundary."""

    _validate_no_hidden_keys(payload)
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise ContractError(f"payload is not JSON serializable: {exc}") from exc
