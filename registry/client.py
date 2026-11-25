"""Small idempotent wrapper around the current AWS Agent Registry APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Mapping
from uuid import uuid4


class RegistryError(RuntimeError):
    """Raised for Registry configuration or API failures."""


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    name: str
    record_type: str
    description: str
    record_version: str = "1.0.0"
    display_name: str | None = None
    descriptors: dict[str, Any] = field(default_factory=dict)
    custom_metadata: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    def to_create_kwargs(self, registry_id: str) -> dict[str, Any]:
        if self.record_type not in {"MCP", "AGENT", "CUSTOM", "SKILL", "GATEWAY"}:
            raise RegistryError(f"unsupported Registry record type: {self.record_type}")
        return {
            "registryId": registry_id,
            "name": self.name,
            "displayName": self.display_name or self.name,
            "description": self.description,
            "recordType": self.record_type,
            "descriptors": self.descriptors,
            "recordVersion": self.record_version,
            "clientToken": str(uuid4()),
            "customMetadata": self.custom_metadata,
            "tags": self.tags,
        }


class RegistryClient:
    """Control/data plane wrapper with injectable clients for tests."""

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        profile: str | None = None,
        control_client: Any | None = None,
        data_client: Any | None = None,
    ) -> None:
        if control_client is not None and data_client is not None:
            self.control = control_client
            self.data = data_client
            return
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - dependency boundary.
            raise RegistryError("boto3 is required for AWS Agent Registry operations") from exc
        session = boto3.Session(profile_name=profile, region_name=region)
        self.control = control_client or session.client("agent-registry-control")
        self.data = data_client or session.client("agent-registry")

    def find_registry(self, name: str) -> dict[str, Any] | None:
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            response = self.control.list_registries(**kwargs)
            for registry in response.get("registries", []):
                if registry.get("name") == name:
                    return dict(registry)
            token = response.get("nextToken")
            if not token:
                return None

    def ensure_registry(
        self,
        *,
        name: str,
        description: str,
        tags: Mapping[str, str] | None = None,
        auto_approve: bool = False,
    ) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                key: {"type": "string"}
                for key in ("role", "owner", "version", "safety_boundary", "tool_boundary", "data", "skill")
            },
        }
        existing = self.find_registry(name)
        if existing:
            if not existing.get("customMetadataSchemaConfiguration") and hasattr(self.control, "update_registry"):
                try:
                    self.control.update_registry(
                        registryId=str(existing["registryId"]),
                        customMetadataSchemaConfiguration={
                            "optionalValue": {"defaultSchema": json.dumps(schema, sort_keys=True)}
                        },
                    )
                    existing = {**existing, "customMetadataSchemaConfiguration": {"defaultSchema": json.dumps(schema, sort_keys=True)}}
                except Exception as exc:
                    raise RegistryError(f"could not configure custom metadata schema for {name}: {exc}") from exc
            return existing
        kwargs: dict[str, Any] = {
            "name": name,
            "description": description,
            "discoveryConfiguration": {"authorizerType": "AWS_IAM"},
            "clientToken": str(uuid4()),
            "tags": dict(tags or {}),
            "customMetadataSchemaConfiguration": {
                "defaultSchema": json.dumps(schema, sort_keys=True)
            },
        }
        if auto_approve:
            kwargs["approvalConfiguration"] = {"autoApprovalRules": ["APPROVE_ALL"]}
        response = self.control.create_registry(**kwargs)
        return dict(response.get("registry") or response)

    def wait_for_registry(
        self,
        registry_id: str,
        *,
        timeout_seconds: int = 300,
        poll_seconds: int = 5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                current = self.control.get_registry(registryId=registry_id)
            except AttributeError:
                current = next((item for item in self.list_registries() if item.get("registryId") == registry_id), {})
            current = dict(current.get("registry") or current)
            last = current
            status = str(current.get("status") or "").upper()
            if status in {"ACTIVE", "READY"}:
                return current
            if status in {"FAILED", "CREATE_FAILED", "UPDATE_FAILED"}:
                raise RegistryError(f"Registry {registry_id} failed: {current}")
            time.sleep(poll_seconds)
        raise RegistryError(f"timed out waiting for Registry {registry_id}: {last.get('status')}")

    def list_registries(self) -> list[dict[str, Any]]:
        token: str | None = None
        items: list[dict[str, Any]] = []
        while True:
            kwargs: dict[str, Any] = {"maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            response = self.control.list_registries(**kwargs)
            items.extend(dict(item) for item in response.get("registries", []))
            token = response.get("nextToken")
            if not token:
                return items

    def list_records(self, registry_id: str, *, status: str | None = None, record_type: str | None = None) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if status:
            filters.append({"name": "status", "values": [status]})
        if record_type:
            filters.append({"name": "recordType", "values": [record_type]})
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "registryId": registry_id,
                "maxResults": 100,
                **({"filters": filters} if filters else {}),
            }
            if token:
                kwargs["nextToken"] = token
            response = self.control.list_registry_records(**kwargs)
            items.extend(dict(item) for item in response.get("records", response.get("registryRecords", [])))
            token = response.get("nextToken")
            if not token:
                return items

    def find_record(self, registry_id: str, name: str) -> dict[str, Any] | None:
        for record in self.list_records(registry_id):
            if record.get("name") == name:
                record_id = record.get("recordId")
                if record_id and hasattr(self.control, "get_registry_record"):
                    detail = self.control.get_registry_record(registryId=registry_id, recordId=str(record_id))
                    return dict(detail.get("record") or detail)
                return record
        return None

    def ensure_record(self, registry_id: str, record: RegistryRecord) -> dict[str, Any]:
        existing = self.find_record(registry_id, record.name)
        if existing:
            if self._record_needs_update(existing, record) and hasattr(self.control, "update_registry_record"):
                record_id = str(existing.get("recordId") or "")
                if record_id:
                    self.update_record(registry_id, record_id, record)
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        current = self.find_record(registry_id, record.name)
                        if current and str(current.get("status") or "").upper() not in {"CREATING", "UPDATING"}:
                            return current
                        time.sleep(2)
                    return self.find_record(registry_id, record.name) or existing
            return existing
        response = self.control.create_registry_record(**record.to_create_kwargs(registry_id))
        created = dict(response.get("record") or response)
        record_id = str(created.get("recordId") or created.get("recordArn", "").rstrip("/").split("/")[-1])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = self.find_record(registry_id, record.name)
            if current and str(current.get("status") or "").upper() not in {"CREATING", "UPDATING"}:
                return current
            time.sleep(2)
        if record_id:
            created["recordId"] = record_id
        return created

    @staticmethod
    def _record_needs_update(existing: Mapping[str, Any], desired: RegistryRecord) -> bool:
        if str(existing.get("description") or "") != desired.description:
            return True
        if str(existing.get("displayName") or "") != str(desired.display_name or desired.name):
            return True
        if str(existing.get("recordVersion") or "") != desired.record_version:
            return True
        current_metadata = existing.get("customMetadata") or existing.get("custom_metadata") or {}
        if any(current_metadata.get(key) != value for key, value in desired.custom_metadata.items()):
            return True
        desired_custom = ((desired.descriptors or {}).get("custom") or {}).get("data")
        current_custom = (((existing.get("descriptors") or {}).get("custom") or {}).get("data"))
        return bool(desired_custom is not None and current_custom != desired_custom)

    def update_record(self, registry_id: str, record_id: str, record: RegistryRecord) -> dict[str, Any]:
        """Reconcile a declared capability record without changing its type."""

        descriptors = {}
        for key, value in (record.descriptors or {}).items():
            descriptor = dict(value) if isinstance(value, Mapping) else value
            if isinstance(descriptor, dict) and "data" in descriptor:
                descriptor["data"] = {"optionalValue": descriptor["data"]}
            descriptors[key] = {"optionalValue": descriptor}
        response = self.control.update_registry_record(
            registryId=registry_id,
            recordId=record_id,
            name=record.name,
            displayName={"optionalValue": record.display_name or record.name},
            description={"optionalValue": record.description},
            recordType=record.record_type,
            descriptors={"optionalValue": descriptors},
            recordVersion=record.record_version,
            customMetadata={"optionalValue": record.custom_metadata},
            triggerSynchronization=False,
        )
        updated = dict(response.get("record") or response)
        return updated

    def wait_for_record_status(
        self,
        registry_id: str,
        name: str,
        statuses: set[str],
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            latest = self.find_record(registry_id, name) or {}
            if str(latest.get("status") or "").upper() in {item.upper() for item in statuses}:
                return latest
            time.sleep(2)
        raise RegistryError(f"timed out waiting for Registry record {name}: {latest.get('status')}")

    def submit_for_approval(self, registry_id: str, record_id: str) -> dict[str, Any]:
        return dict(self.control.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id))

    def set_status(self, registry_id: str, record_id: str, status: str, reason: str = "") -> dict[str, Any]:
        if status not in {"DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"}:
            raise RegistryError(f"unsupported Registry status: {status}")
        return dict(
            self.control.update_registry_record_status(
                registryId=registry_id,
                recordId=record_id,
                status=status,
                **({"statusReason": reason} if reason else {}),
            )
        )

    def search_approved(self, registry_id: str, query: str, *, max_results: int = 20) -> list[dict[str, Any]]:
        response = self.data.search_discoverable_registry_records(
            searchQuery=query,
            registryIds=[registry_id],
            maxResults=max_results,
        )
        return [dict(item) for item in response.get("records", response.get("results", []))]

    @staticmethod
    def descriptor_for_custom(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"custom": {"data": json.dumps(dict(payload), sort_keys=True)}}
