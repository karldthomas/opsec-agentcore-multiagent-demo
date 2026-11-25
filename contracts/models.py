"""Small, dependency-free contracts for the OpsFusion first vertical slice.

The contracts deliberately use standard-library dataclasses so they can be
validated in local tests before any AWS SDK or model integration is required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, ClassVar, Mapping


CONTRACT_VERSION = "opsfusion.v1"
CLAIM_TYPES = frozenset({"fact", "relationship", "hypothesis"})
TOOL_STATUSES = frozenset({"ok", "error", "denied"})
HIDDEN_KEYS = frozenset(
    {
        "hidden_root_cause",
        "ground_truth",
        "expected_outcome",
        "evaluator_only",
        "evaluator_data",
    }
)


class ContractError(ValueError):
    """Raised when a versioned contract is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContractError(f"{name} is required")
    return normalized


def _bounded_text(value: Any, name: str, maximum: int = 16_000) -> str:
    text = _required(value, name)
    if len(text) > maximum:
        raise ContractError(f"{name} exceeds the maximum length of {maximum}")
    return text


def _validate_no_hidden_keys(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).casefold()
            if key_text in HIDDEN_KEYS or any(hidden in key_text for hidden in HIDDEN_KEYS):
                raise ContractError(f"hidden evaluator field is not allowed in {path}.{key}")
            _validate_no_hidden_keys(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_no_hidden_keys(nested, f"{path}[{index}]")


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: str
    source_type: str
    source_record_id: str
    scenario_run_id: str
    tenant_id: str
    observed_at: str
    query: dict[str, Any] = field(default_factory=dict)

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source_type", "source_record_id", "scenario_run_id", "tenant_id", "observed_at"):
            _required(getattr(self, name), name)
        _validate_no_hidden_keys(self.query, "query")

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version, **_json_ready(asdict(self))}


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    agent: str
    claim_type: str
    statement: str
    evidence_ids: tuple[str, ...]
    confidence: float
    scenario_run_id: str
    tenant_id: str
    created_at: str = field(default_factory=utc_now)

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("finding_id", "agent", "statement", "scenario_run_id", "tenant_id"):
            _required(getattr(self, name), name)
        if self.claim_type not in CLAIM_TYPES:
            raise ContractError(f"claim_type must be one of {sorted(CLAIM_TYPES)}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ContractError("confidence must be between 0 and 1")
        if not self.evidence_ids:
            raise ContractError("a finding must cite at least one evidence ID")
        if any(not str(item).strip() for item in self.evidence_ids):
            raise ContractError("evidence IDs cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version, **_json_ready(asdict(self))}


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    statement: str
    supporting_finding_ids: tuple[str, ...]
    contradicting_finding_ids: tuple[str, ...]
    confidence: float
    scenario_run_id: str
    tenant_id: str
    unresolved_questions: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("hypothesis_id", "statement", "scenario_run_id", "tenant_id"):
            _required(getattr(self, name), name)
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ContractError("confidence must be between 0 and 1")
        if not self.supporting_finding_ids:
            raise ContractError("a hypothesis must cite at least one supporting finding")

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version, **_json_ready(asdict(self))}


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    run_id: str
    scenario_id: str
    tenant_id: str
    actor_id: str
    seed: int
    simulation_tick: int = 0
    status: str = "investigating"
    created_at: str = field(default_factory=utc_now)

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("run_id", "scenario_id", "tenant_id", "actor_id"):
            _required(getattr(self, name), name)
        if int(self.seed) < 0:
            raise ContractError("seed cannot be negative")
        if int(self.simulation_tick) < 0:
            raise ContractError("simulation_tick cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version, **_json_ready(asdict(self))}


@dataclass(frozen=True, slots=True)
class ToolResponse:
    request_id: str
    tool_name: str
    scenario_run_id: str
    tenant_id: str
    status: str
    data: dict[str, Any]
    evidence: tuple[EvidenceReference, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    observed_at: str = field(default_factory=utc_now)
    role: str = ""

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("request_id", "tool_name", "scenario_run_id", "tenant_id"):
            _required(getattr(self, name), name)
        if self.status not in TOOL_STATUSES:
            raise ContractError(f"status must be one of {sorted(TOOL_STATUSES)}")
        _validate_no_hidden_keys(self.data, "data")
        _validate_no_hidden_keys(self.errors, "errors")
        if self.status == "ok" and self.errors:
            raise ContractError("successful tool responses cannot contain errors")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "scenario_run_id": self.scenario_run_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "data": _json_ready(self.data),
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": _json_ready(self.errors),
            "observed_at": self.observed_at,
            "role": self.role,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)
