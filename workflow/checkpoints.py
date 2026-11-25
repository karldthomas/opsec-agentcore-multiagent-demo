"""Versioned workflow checkpoints with optimistic concurrency."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Protocol


class CheckpointConflict(RuntimeError):
    """Raised when a stale worker attempts to overwrite newer state."""


class CheckpointNotFound(KeyError):
    """Raised when a workflow has not been initialized."""


@dataclass(frozen=True, slots=True)
class WorkflowCheckpoint:
    run_id: str
    stage: str
    completed_stages: tuple[str, ...] = ()
    task_results: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "completed_stages": list(self.completed_stages),
            "task_results": self.task_results,
            "retry_count": self.retry_count,
            "metadata": self.metadata,
            "version": self.version,
        }


class CheckpointStore(Protocol):
    def create(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint: ...

    def get(self, run_id: str) -> WorkflowCheckpoint: ...

    def save(self, checkpoint: WorkflowCheckpoint, *, expected_version: int) -> WorkflowCheckpoint: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._items: dict[str, WorkflowCheckpoint] = {}

    def create(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        if checkpoint.run_id in self._items:
            raise CheckpointConflict(f"checkpoint already exists: {checkpoint.run_id}")
        self._items[checkpoint.run_id] = checkpoint
        return checkpoint

    def get(self, run_id: str) -> WorkflowCheckpoint:
        try:
            return self._items[run_id]
        except KeyError as exc:
            raise CheckpointNotFound(run_id) from exc

    def save(self, checkpoint: WorkflowCheckpoint, *, expected_version: int) -> WorkflowCheckpoint:
        current = self.get(checkpoint.run_id)
        if current.version != expected_version:
            raise CheckpointConflict(
                f"stale checkpoint for {checkpoint.run_id}: expected {expected_version}, current {current.version}"
            )
        updated = WorkflowCheckpoint(
            run_id=checkpoint.run_id,
            stage=checkpoint.stage,
            completed_stages=checkpoint.completed_stages,
            task_results=checkpoint.task_results,
            retry_count=checkpoint.retry_count,
            metadata=checkpoint.metadata,
            version=current.version + 1,
        )
        self._items[checkpoint.run_id] = updated
        return updated


class JsonCheckpointStore:
    """Adapter over a small JSON run store for local interruption tests."""

    def __init__(self, run_store: Any) -> None:
        self.run_store = run_store

    def create(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        try:
            self.run_store.load(checkpoint.run_id)
        except Exception:
            self.run_store.save(checkpoint.run_id, checkpoint.to_dict())
            return checkpoint
        raise CheckpointConflict(f"checkpoint already exists: {checkpoint.run_id}")

    def get(self, run_id: str) -> WorkflowCheckpoint:
        payload = self.run_store.load(run_id)
        return WorkflowCheckpoint(
            run_id=str(payload["run_id"]),
            stage=str(payload["stage"]),
            completed_stages=tuple(payload.get("completed_stages", [])),
            task_results=dict(payload.get("task_results", {})),
            retry_count=int(payload.get("retry_count", 0)),
            metadata=dict(payload.get("metadata", {})),
            version=int(payload.get("version", 0)),
        )

    def save(self, checkpoint: WorkflowCheckpoint, *, expected_version: int) -> WorkflowCheckpoint:
        current = self.get(checkpoint.run_id)
        if current.version != expected_version:
            raise CheckpointConflict(
                f"stale checkpoint for {checkpoint.run_id}: expected {expected_version}, current {current.version}"
            )
        updated = WorkflowCheckpoint(
            run_id=checkpoint.run_id,
            stage=checkpoint.stage,
            completed_stages=checkpoint.completed_stages,
            task_results=checkpoint.task_results,
            retry_count=checkpoint.retry_count,
            metadata=checkpoint.metadata,
            version=current.version + 1,
        )
        self.run_store.save(checkpoint.run_id, updated.to_dict())
        return updated


class DynamoCheckpointStore:
    """DynamoDB-backed checkpoint store used by the deployed worker.

    The workflow table is keyed by ``run_id`` and ``record_key``.  Checkpoints
    use the reserved record key ``checkpoint`` and are written with an
    optimistic version condition so concurrent retries cannot silently
    overwrite a newer stage.
    """

    RECORD_KEY = "checkpoint"

    def __init__(self, table_name: str, *, table: Any | None = None, region_name: str | None = None) -> None:
        if table is None:
            import boto3

            table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self.table = table

    @staticmethod
    def _item(checkpoint: WorkflowCheckpoint) -> dict[str, Any]:
        return {
            "run_id": checkpoint.run_id,
            "record_key": DynamoCheckpointStore.RECORD_KEY,
            "stage": checkpoint.stage,
            "completed_stages": list(checkpoint.completed_stages),
            # DynamoDB's high-level serializer rejects Python floats. JSON
            # keeps nested model/tool output lossless and bounded while the
            # public checkpoint contract remains a normal JSON object.
            "task_results_json": json.dumps(checkpoint.task_results, sort_keys=True),
            "retry_count": checkpoint.retry_count,
            "metadata_json": json.dumps(checkpoint.metadata, sort_keys=True),
            "version": checkpoint.version,
        }

    @staticmethod
    def _json_map(item: dict[str, Any], json_key: str, legacy_key: str) -> dict[str, Any]:
        encoded = item.get(json_key)
        if encoded is not None:
            try:
                value = json.loads(str(encoded))
            except (TypeError, ValueError, json.JSONDecodeError):
                value = {}
            return dict(value) if isinstance(value, dict) else {}
        legacy = item.get(legacy_key, {})
        return dict(legacy) if isinstance(legacy, dict) else {}

    @staticmethod
    def _checkpoint(item: dict[str, Any]) -> WorkflowCheckpoint:
        return WorkflowCheckpoint(
            run_id=str(item["run_id"]),
            stage=str(item["stage"]),
            completed_stages=tuple(item.get("completed_stages", [])),
            task_results=DynamoCheckpointStore._json_map(item, "task_results_json", "task_results"),
            retry_count=int(item.get("retry_count", 0)),
            metadata=DynamoCheckpointStore._json_map(item, "metadata_json", "metadata"),
            version=int(item.get("version", 0)),
        )

    @staticmethod
    def _is_conditional_failure(exc: Exception) -> bool:
        return str(getattr(exc, "response", {}).get("Error", {}).get("Code", "")) == "ConditionalCheckFailedException"

    def create(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        try:
            self.table.put_item(
                Item=self._item(checkpoint),
                ConditionExpression="attribute_not_exists(run_id)",
            )
        except Exception as exc:
            if self._is_conditional_failure(exc):
                raise CheckpointConflict(f"checkpoint already exists: {checkpoint.run_id}") from exc
            raise
        return checkpoint

    def get(self, run_id: str) -> WorkflowCheckpoint:
        response = self.table.get_item(Key={"run_id": run_id, "record_key": self.RECORD_KEY})
        item = response.get("Item")
        if not item:
            raise CheckpointNotFound(run_id)
        return self._checkpoint(item)

    def save(self, checkpoint: WorkflowCheckpoint, *, expected_version: int) -> WorkflowCheckpoint:
        updated = WorkflowCheckpoint(
            run_id=checkpoint.run_id,
            stage=checkpoint.stage,
            completed_stages=checkpoint.completed_stages,
            task_results=checkpoint.task_results,
            retry_count=checkpoint.retry_count,
            metadata=checkpoint.metadata,
            version=expected_version + 1,
        )
        try:
            self.table.put_item(
                Item=self._item(updated),
                ConditionExpression="attribute_exists(run_id) AND version = :expected_version",
                ExpressionAttributeValues={":expected_version": expected_version},
            )
        except Exception as exc:
            if self._is_conditional_failure(exc):
                raise CheckpointConflict(
                    f"stale checkpoint for {checkpoint.run_id}: expected {expected_version}"
                ) from exc
            raise
        return updated
