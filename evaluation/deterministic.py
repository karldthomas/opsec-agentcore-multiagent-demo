"""Evaluator-side checks that may access simulator hidden truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.models import HIDDEN_KEYS
from evidence import EvidenceStore
from simulator.engine import ScenarioEngine


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    run_id: str
    scenario_id: str
    checks: dict[str, dict[str, Any]]
    semantic: dict[str, Any]

    @property
    def passed(self) -> bool:
        return all(bool(item.get("passed")) for item in self.checks.values())

    @property
    def score(self) -> float:
        values = [1.0 if item.get("passed") else 0.0 for item in self.checks.values()]
        return round(sum(values) / len(values), 4) if values else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "score": self.score,
            "checks": self.checks,
            "semantic": self.semantic,
        }


def _contains_hidden(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key).casefold()
            if key_text in HIDDEN_KEYS or any(hidden in key_text for hidden in HIDDEN_KEYS):
                return True
            if _contains_hidden(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_hidden(item) for item in value)
    return False


class DeterministicEvaluator:
    """Run compliance and outcome checks without exposing truth to agents."""

    REQUIRED_TERMS = {
        "database_connection_pool_regression": ("connection", "pool"),
        "retry_amplification_without_backoff": ("retry",),
        "high_frequency_query_without_index": ("query", "index"),
        "third_party_dependency_outage": ("dependency",),
    }

    def __init__(
        self,
        engine: ScenarioEngine,
        evidence: EvidenceStore,
    ) -> None:
        self.engine = engine
        self.evidence = evidence

    def evaluate(self, run_id: str) -> EvaluationResult:
        truth = self.engine.hidden_truth(run_id)
        state = self.engine.get_run(run_id)
        workspace = self.evidence.runs.get(run_id)
        statements = " ".join(
            [item.statement for item in workspace.hypotheses.values()] if workspace else []
        ).casefold()
        terms = self.REQUIRED_TERMS.get(str(truth["hidden_root_cause"]), ())
        checks: dict[str, dict[str, Any]] = {}

        checks["evidence_citations"] = {
            "passed": bool(workspace and workspace.findings and all(
                evidence_id in workspace.evidence
                for finding in workspace.findings.values()
                for evidence_id in finding.evidence_ids
            )),
            "details": "all finding citations resolve within the run workspace",
        }
        checks["hidden_state_boundary"] = {
            "passed": not _contains_hidden(self.evidence.get_shared_evidence(run_id, tenant_id=state.run.tenant_id)),
            "details": "shared evidence contains no evaluator-only keys",
        }
        checks["root_cause_match"] = {
            "passed": bool(terms) and all(term in statements for term in terms),
            "details": {"required_terms": list(terms)},
        }
        checks["read_only_boundary"] = {
            "passed": state.run.simulation_tick == 0 and state.current_values == state.definition.initial_values,
            "details": "analysis completed without changing the simulator snapshot",
        }
        return EvaluationResult(
            run_id,
            str(truth["scenario_id"]),
            checks,
            semantic={"mode": "read-only analysis"},
        )
