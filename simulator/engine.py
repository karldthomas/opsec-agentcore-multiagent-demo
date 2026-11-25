"""Read-only deterministic incident source with a strict agent-visible boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any
from uuid import uuid4

from contracts import ScenarioRun
from contracts.models import utc_now

from .fixtures import ScenarioDefinition, all_scenario_definitions
from .persistence import PersistenceError


class SimulatorError(RuntimeError):
    """Raised for invalid or unauthorized simulator operations."""

    def __init__(self, message: str, code: str = "SIMULATOR_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class RunState:
    run: ScenarioRun
    definition: ScenarioDefinition
    current_values: dict[str, Any]
    failed_stages: set[str] = field(default_factory=set)
    persistence_version: int = 0


class ScenarioEngine:
    """In-memory source of truth for deterministic synthetic incident runs."""

    def __init__(self, definitions: dict[str, ScenarioDefinition] | None = None, *, persistence: Any | None = None) -> None:
        self.definitions = definitions or all_scenario_definitions()
        self.persistence = persistence
        self.runs: dict[str, RunState] = {}

    def create_run(
        self,
        scenario_id: str,
        *,
        run_id: str | None = None,
        actor_id: str = "local-operator",
        tenant_id: str | None = None,
        seed: int | None = None,
    ) -> ScenarioRun:
        definition = self._definition(scenario_id)
        requested_tenant = str(tenant_id or definition.tenant_id).strip()
        if requested_tenant != definition.tenant_id:
            raise SimulatorError("tenant does not own the selected scenario", "TENANT_ACCESS_DENIED")
        resolved_seed = definition.seed if seed is None else int(seed)
        if resolved_seed < 0:
            raise SimulatorError("seed cannot be negative", "INVALID_SEED")
        resolved_run_id = str(run_id or f"run-{uuid4().hex}").strip()
        if resolved_run_id in self.runs:
            raise SimulatorError(f"run already exists: {resolved_run_id}", "RUN_ALREADY_EXISTS")
        run = ScenarioRun(
            run_id=resolved_run_id,
            scenario_id=scenario_id,
            tenant_id=requested_tenant,
            actor_id=str(actor_id or "local-operator"),
            seed=resolved_seed,
        )
        state = RunState(
            run=run,
            definition=definition,
            current_values=deepcopy(definition.initial_values),
        )
        if self.persistence is not None:
            self.persistence.create(resolved_run_id, self._serialize_state(state))
        self.runs[resolved_run_id] = state
        return run

    def get_run(self, run_id: str, *, tenant_id: str | None = None) -> RunState:
        if self.persistence is not None:
            try:
                record = self.persistence.load(run_id)
                payload = dict(record["payload"])
                state = self._deserialize_state(payload, int(record.get("version", 0)))
                self.runs[run_id] = state
            except (PersistenceError, KeyError, TypeError, ValueError) as exc:
                raise SimulatorError(f"unknown scenario run: {run_id}", "RUN_NOT_FOUND") from exc
        else:
            try:
                state = self.runs[run_id]
            except KeyError as exc:
                raise SimulatorError(f"unknown scenario run: {run_id}", "RUN_NOT_FOUND") from exc
        if tenant_id is not None and tenant_id != state.run.tenant_id:
            raise SimulatorError("scenario run is outside the tenant boundary", "RUN_ACCESS_DENIED")
        return state

    def _definition(self, scenario_id: str) -> ScenarioDefinition:
        try:
            return self.definitions[scenario_id]
        except KeyError as exc:
            raise SimulatorError(f"unknown scenario: {scenario_id}", "SCENARIO_NOT_FOUND") from exc

    def list_scenarios(self) -> list[dict[str, Any]]:
        return [
            {
                "scenario_id": definition.scenario_id,
                "name": definition.name,
                "primary_incident_id": definition.primary_incident_id,
                "tenant_id": definition.tenant_id,
                "region": definition.region,
            }
            for definition in sorted(self.definitions.values(), key=lambda item: item.scenario_id)
        ]

    def get_incident_context(self, run_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        state = self.get_run(run_id, tenant_id=tenant_id)
        return {
            "incident": deepcopy(state.definition.incidents[0]),
            "available_services": ["orders-service", "orders-db", "orders-processing"],
            "current_status": state.current_values["service_status"],
            "simulation_tick": state.run.simulation_tick,
        }

    def observe(
        self,
        run_id: str,
        source: str,
        *,
        tenant_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        state = self.get_run(run_id, tenant_id=tenant_id)
        filters = filters or {}
        records = [deepcopy(record) for record in state.definition.observable_records(source)]
        selected: list[dict[str, Any]] = []
        for record in records:
            if all(record.get(key) == value for key, value in filters.items() if key in record):
                selected.append(record)
        return selected

    def service_health(self, run_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        state = self.get_run(run_id, tenant_id=tenant_id)
        return {
            "service": "orders-service",
            "status": state.current_values["service_status"],
            "api_error_rate": state.current_values["api_error_rate"],
            "api_p95_latency_ms": state.current_values["api_p95_latency_ms"],
            "database_connections": state.current_values["database_connections"],
            "queue_depth": state.current_values["queue_depth"],
            "order_processing_delay_seconds": state.current_values["order_processing_delay_seconds"],
            "simulation_tick": state.run.simulation_tick,
        }

    def service_health_record(self, run_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        """Return the bounded provenance record for the current health snapshot."""

        state = self.get_run(run_id, tenant_id=tenant_id)
        incident_suffix = state.definition.primary_incident_id.removeprefix("INC-")
        return {
            "record_id": f"SERVICE-{incident_suffix}-001",
            **self.service_health(run_id, tenant_id=tenant_id),
        }

    def inject_failure(self, run_id: str, stage: str, *, tenant_id: str | None = None) -> None:
        state = self.get_run(run_id, tenant_id=tenant_id)
        stage_name = str(stage or "").strip()
        if not stage_name:
            raise SimulatorError("failure stage is required", "INVALID_FAILURE_STAGE")
        state.failed_stages.add(stage_name)
        self._persist_state(state)

    def consume_failure(self, run_id: str, stage: str, *, tenant_id: str | None = None) -> bool:
        state = self.get_run(run_id, tenant_id=tenant_id)
        if stage in state.failed_stages:
            state.failed_stages.remove(stage)
            self._persist_state(state)
            return True
        return False

    def hidden_truth(self, run_id: str) -> dict[str, Any]:
        """Evaluator-only accessor; never call from agent-facing adapters."""

        state = self.get_run(run_id)
        return {
            "scenario_id": state.definition.scenario_id,
            "tenant_id": state.definition.tenant_id,
            "hidden_root_cause": state.definition.hidden_root_cause,
            "primary_incident_id": state.definition.primary_incident_id,
        }

    @staticmethod
    def _serialize_state(state: RunState) -> dict[str, Any]:
        """Serialize only mutable run state; never serialize the definition."""

        return {
            "run": state.run.to_dict(),
            "current_values": deepcopy(state.current_values),
            "failed_stages": sorted(state.failed_stages),
        }

    def _deserialize_state(self, payload: dict[str, Any], version: int) -> RunState:
        run_payload = dict(payload["run"])
        run = ScenarioRun(
            run_id=str(run_payload["run_id"]),
            scenario_id=str(run_payload["scenario_id"]),
            tenant_id=str(run_payload["tenant_id"]),
            actor_id=str(run_payload["actor_id"]),
            seed=int(run_payload["seed"]),
            simulation_tick=int(run_payload.get("simulation_tick", 0)),
            status=str(run_payload.get("status", "investigating")),
            created_at=str(run_payload.get("created_at") or utc_now()),
        )
        definition = self._definition(run.scenario_id)
        return RunState(
            run=run,
            definition=definition,
            current_values=deepcopy(dict(payload.get("current_values") or definition.initial_values)),
            failed_stages=set(str(item) for item in payload.get("failed_stages", [])),
            persistence_version=version,
        )

    def _persist_state(self, state: RunState) -> None:
        if self.persistence is None:
            return
        state.persistence_version = self.persistence.save(
            state.run.run_id,
            self._serialize_state(state),
            expected_version=state.persistence_version,
        )
