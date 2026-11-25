"""Application-facing store wrapper around the local simulator engine."""

from __future__ import annotations

from typing import Any

from .engine import ScenarioEngine, SimulatorError


class SimulatorStore:
    def __init__(self, engine: ScenarioEngine | None = None) -> None:
        self.engine = engine or ScenarioEngine()

    def create_run(self, scenario_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.engine.create_run(scenario_id, **kwargs).to_dict()

    def get_run(self, run_id: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        return self.engine.get_run(run_id, tenant_id=tenant_id).run.to_dict()

    def list_scenarios(self) -> list[dict[str, Any]]:
        return self.engine.list_scenarios()
