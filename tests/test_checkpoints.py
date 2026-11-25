from __future__ import annotations

import pytest

from simulator.engine import ScenarioEngine
from simulator.persistence import DynamoRunRepository, JsonRunStore
from workflow.checkpoints import (
    CheckpointConflict,
    DynamoCheckpointStore,
    InMemoryCheckpointStore,
    JsonCheckpointStore,
    WorkflowCheckpoint,
)


def test_in_memory_checkpoints_are_versioned() -> None:
    store = InMemoryCheckpointStore()
    initial = store.create(WorkflowCheckpoint(run_id="run-1", stage="initialize"))
    saved = store.save(WorkflowCheckpoint(run_id="run-1", stage="parallel"), expected_version=initial.version)
    assert saved.version == 1
    with pytest.raises(CheckpointConflict):
        store.save(WorkflowCheckpoint(run_id="run-1", stage="stale"), expected_version=0)


def test_json_checkpoints_survive_store_recreation(tmp_path) -> None:
    backing = JsonRunStore(tmp_path)
    first = JsonCheckpointStore(backing)
    initial = first.create(WorkflowCheckpoint(run_id="run-2", stage="initialize"))
    first.save(WorkflowCheckpoint(run_id="run-2", stage="parallel", completed_stages=("initialize",)), expected_version=initial.version)
    second = JsonCheckpointStore(JsonRunStore(tmp_path))
    assert second.get("run-2").stage == "parallel"
    assert second.get("run-2").completed_stages == ("initialize",)


class FakeDynamoTable:
    def __init__(self) -> None:
        self.item = None

    def put_item(self, *, Item, ConditionExpression, **kwargs):
        if "attribute_not_exists" in ConditionExpression and self.item is not None:
            error = RuntimeError("conditional failure")
            error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise error
        if "attribute_exists" in ConditionExpression:
            if self.item is None or self.item["version"] != kwargs["ExpressionAttributeValues"][":expected_version"]:
                error = RuntimeError("conditional failure")
                error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
                raise error
        self.item = Item

    def get_item(self, *, Key):
        return {"Item": self.item} if self.item else {}


def test_dynamo_checkpoints_use_versioned_conditional_writes() -> None:
    table = FakeDynamoTable()
    store = DynamoCheckpointStore("workflow", table=table)
    initial = store.create(WorkflowCheckpoint(run_id="run-3", stage="initialize"))
    saved = store.save(WorkflowCheckpoint(run_id="run-3", stage="parallel"), expected_version=initial.version)
    assert saved.version == 1
    assert store.get("run-3").stage == "parallel"
    with pytest.raises(CheckpointConflict):
        store.save(WorkflowCheckpoint(run_id="run-3", stage="stale"), expected_version=0)


class FakeRunTable:
    def __init__(self) -> None:
        self.items = {}

    def put_item(self, *, Item, ConditionExpression, **kwargs):
        key = (Item["run_id"], Item["record_key"])
        current = self.items.get(key)
        if "attribute_not_exists" in ConditionExpression and current is not None:
            error = RuntimeError("conditional failure")
            error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise error
        if "attribute_exists" in ConditionExpression:
            expected = kwargs["ExpressionAttributeValues"][":expected_version"]
            if current is None or current["version"] != expected:
                error = RuntimeError("conditional failure")
                error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
                raise error
        self.items[key] = Item

    def get_item(self, *, Key):
        item = self.items.get((Key["run_id"], Key["record_key"]))
        return {"Item": item} if item else {}


def test_dynamo_run_repository_shares_mutable_state_between_engine_instances() -> None:
    table = FakeRunTable()
    repository = DynamoRunRepository("workflow", table=table)
    first = ScenarioEngine(persistence=repository)
    first.create_run("INC-2048", run_id="shared-run")
    second = ScenarioEngine(persistence=DynamoRunRepository("workflow", table=table))
    state = second.get_run("shared-run", tenant_id="acme-retail")
    assert state.run.scenario_id == "INC-2048"
    assert second.service_health("shared-run")["database_connections"] == 500
