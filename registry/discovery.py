"""Approved-record discovery with a static role/capability safety check."""

from __future__ import annotations

from typing import Any

from agents.roles import AGENT_ROLES

from .client import RegistryClient, RegistryError


class RegistryDiscoveryError(RegistryError):
    pass


class RegistryResolver:
    def __init__(self, client: RegistryClient, *, registry_id: str, environment: str) -> None:
        self.client = client
        self.registry_id = registry_id
        self.environment = environment

    def resolve_agent(self, role: str) -> dict[str, Any]:
        if role not in AGENT_ROLES:
            raise RegistryDiscoveryError(f"role is outside the static allowlist: {role}")
        name = f"opsfusion-{self.environment}-{role}"
        record = self.client.find_record(self.registry_id, name)
        if record is None:
            raise RegistryDiscoveryError(f"Registry record is not discoverable: {name}")
        if str(record.get("status") or "").upper() != "APPROVED":
            raise RegistryDiscoveryError(f"Registry record is not approved: {name}")
        metadata = record.get("customMetadata") or record.get("custom_metadata") or {}
        if metadata.get("role") != role or metadata.get("safety_boundary") != "synthetic-only":
            raise RegistryDiscoveryError(f"Registry record metadata is incompatible: {name}")
        return dict(record)

    def resolve_gateway(self) -> dict[str, Any]:
        name = f"opsfusion-{self.environment}-gateway"
        record = self.client.find_record(self.registry_id, name)
        if record is None or str(record.get("status") or "").upper() != "APPROVED":
            raise RegistryDiscoveryError(f"approved Gateway record is unavailable: {name}")
        metadata = record.get("customMetadata") or record.get("custom_metadata") or {}
        if metadata.get("tool_boundary") != "role-scoped":
            raise RegistryDiscoveryError("Gateway record is missing the role-scoped boundary")
        return dict(record)
