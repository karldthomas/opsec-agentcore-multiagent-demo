from __future__ import annotations

import pytest

from registry.client import RegistryClient, RegistryRecord
from registry.discovery import RegistryDiscoveryError, RegistryResolver
from registry.manifest import opsfusion_records


class FakeControl:
    def __init__(self) -> None:
        self.registries: list[dict[str, object]] = []
        self.records: list[dict[str, object]] = []

    def list_registries(self, **kwargs):
        return {"registries": self.registries}

    def create_registry(self, **kwargs):
        value = {"registryId": "reg-1", "name": kwargs["name"], "status": "ACTIVE"}
        self.registries.append(value)
        return {"registry": value}

    def list_registry_records(self, **kwargs):
        return {"records": self.records}

    def create_registry_record(self, **kwargs):
        value = {"recordId": f"record-{len(self.records) + 1}", "name": kwargs["name"], "status": "DRAFT", "recordType": kwargs["recordType"]}
        self.records.append(value)
        return {"record": value}

    def submit_registry_record_for_approval(self, **kwargs):
        return {"recordId": kwargs["recordId"], "status": "PENDING_APPROVAL"}

    def update_registry_record_status(self, **kwargs):
        return kwargs


class FakeData:
    def search_discoverable_registry_records(self, **kwargs):
        return {"records": []}


def test_registry_is_idempotent_and_does_not_auto_approve() -> None:
    control = FakeControl()
    client = RegistryClient(control_client=control, data_client=FakeData())
    first = client.ensure_registry(name="opsfusion-dev", description="demo")
    second = client.ensure_registry(name="opsfusion-dev", description="demo")
    assert first["registryId"] == second["registryId"]
    record = client.ensure_record(
        "reg-1",
        RegistryRecord(name="opsfusion-dev-agent", record_type="AGENT", description="agent"),
    )
    assert record["status"] == "DRAFT"
    assert client.find_record("reg-1", "opsfusion-dev-agent")["recordId"] == record["recordId"]


def test_opsfusion_manifest_contains_agents_gateway_and_skills() -> None:
    records = opsfusion_records("dev")
    types = {record.record_type for record in records}
    assert types == {"AGENT", "GATEWAY", "SKILL"}
    assert len([record for record in records if record.record_type == "AGENT"]) == 4


def test_registry_discovery_requires_approved_static_role_record() -> None:
    control = FakeControl()
    client = RegistryClient(control_client=control, data_client=FakeData())
    registry = client.ensure_registry(name="opsfusion-dev", description="demo")
    record = client.ensure_record("reg-1", opsfusion_records("dev")[0])
    resolver = RegistryResolver(client, registry_id="reg-1", environment="dev")
    with pytest.raises(RegistryDiscoveryError, match="not approved"):
        resolver.resolve_agent("orchestrator")
    control.records[0]["status"] = "APPROVED"
    control.records[0]["customMetadata"] = {"role": "orchestrator", "safety_boundary": "synthetic-only"}
    assert resolver.resolve_agent("orchestrator")["recordId"] == record["recordId"]
