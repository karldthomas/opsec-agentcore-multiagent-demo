"""Local JSON persistence adapter used before the DynamoDB implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PersistenceError(RuntimeError):
    """Raised when a local checkpoint cannot be stored or read."""


class JsonRunStore:
    """Minimal durable store for run metadata and simulator checkpoints."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        safe = str(run_id).strip()
        if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
            raise PersistenceError("run_id is not a safe local key")
        return self.root / f"{safe}.json"

    def save(self, run_id: str, payload: dict[str, Any]) -> None:
        path = self._path(run_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PersistenceError(f"checkpoint does not exist: {run_id}") from exc
        except json.JSONDecodeError as exc:
            raise PersistenceError(f"checkpoint is not valid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise PersistenceError("checkpoint must be a JSON object")
        return payload


class DynamoRunRepository:
    """DynamoDB repository for the simulator's run-scoped mutable state.

    Scenario definitions remain bundled with the application and are loaded by
    ``ScenarioEngine`` from the scenario ID.  Only the mutable run state is
    persisted here, which keeps the evaluator-only root-cause field out of
    every agent-facing tool response while allowing the API Lambda and the
    Gateway target Lambda to observe the same run.
    """

    RECORD_KEY = "scenario"

    def __init__(self, table_name: str, *, table: Any | None = None, region_name: str | None = None) -> None:
        if table is None:
            import boto3

            table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        self.table = table

    @staticmethod
    def _item(run_id: str, payload: dict[str, Any], version: int) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "record_key": DynamoRunRepository.RECORD_KEY,
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            "version": int(version),
        }

    @staticmethod
    def _is_conditional_failure(exc: Exception) -> bool:
        response = getattr(exc, "response", {}) or {}
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        return str(error.get("Code") or "") == "ConditionalCheckFailedException"

    def create(self, run_id: str, payload: dict[str, Any]) -> None:
        try:
            self.table.put_item(
                Item=self._item(run_id, payload, 0),
                ConditionExpression="attribute_not_exists(run_id)",
            )
        except Exception as exc:
            if self._is_conditional_failure(exc):
                raise PersistenceError(f"run already exists: {run_id}") from exc
            raise

    def load(self, run_id: str) -> dict[str, Any]:
        response = self.table.get_item(Key={"run_id": run_id, "record_key": self.RECORD_KEY})
        item = response.get("Item") if isinstance(response, dict) else None
        if not isinstance(item, dict):
            raise PersistenceError(f"run does not exist: {run_id}")
        try:
            payload = json.loads(str(item["payload"]))
            version = int(item.get("version", 0))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"run state is invalid: {run_id}") from exc
        if not isinstance(payload, dict):
            raise PersistenceError(f"run state must be an object: {run_id}")
        return {"payload": payload, "version": version}

    def save(self, run_id: str, payload: dict[str, Any], *, expected_version: int) -> int:
        next_version = int(expected_version) + 1
        try:
            self.table.put_item(
                Item=self._item(run_id, payload, next_version),
                ConditionExpression="attribute_exists(run_id) AND version = :expected_version",
                ExpressionAttributeValues={":expected_version": int(expected_version)},
            )
        except Exception as exc:
            if self._is_conditional_failure(exc):
                raise PersistenceError(f"stale simulator state for {run_id}") from exc
            raise
        return next_version
