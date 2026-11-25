"""Evidence workspace with provenance, idempotency, and optional DynamoDB storage."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from threading import RLock
from typing import Any

from contracts import EvidenceReference, Finding, Hypothesis, ToolResponse
from simulator.engine import ScenarioEngine


class EvidenceStoreError(ValueError):
    """Raised when a finding or hypothesis cannot be grounded."""


@dataclass(slots=True)
class EvidenceRun:
    tenant_id: str
    evidence: dict[str, EvidenceReference] = field(default_factory=dict)
    findings: dict[str, Finding] = field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = field(default_factory=dict)
    unresolved_questions: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class EvidenceStore:
    """A run-scoped workspace that refuses unsupported claims.

    ``table`` is optional so unit tests remain entirely local. When supplied,
    the same workspace is hydrated from and written to DynamoDB. Nested model
    output is stored as JSON because DynamoDB rejects Python floats.
    """

    def __init__(self, engine: ScenarioEngine | None = None, *, table: Any | None = None) -> None:
        self.engine = engine
        self.table = table
        self.runs: dict[str, EvidenceRun] = {}
        self._hydrated: set[str] = set()
        self._lock = RLock()

    @classmethod
    def from_dynamodb(
        cls,
        engine: ScenarioEngine | None,
        table_name: str,
        *,
        region_name: str | None = None,
    ) -> "EvidenceStore":
        import boto3

        table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)
        return cls(engine, table=table)

    def register_run(self, run_id: str, tenant_id: str) -> None:
        with self._lock:
            existing = self.runs.get(run_id)
            if existing and existing.tenant_id != tenant_id:
                raise EvidenceStoreError("run is already bound to another tenant")
            if existing:
                return
            if self.table is None:
                self.runs[run_id] = EvidenceRun(tenant_id=tenant_id)
                return
            self._hydrate_from_dynamodb(run_id, tenant_id)

    def _hydrate_from_dynamodb(self, run_id: str, tenant_id: str) -> None:
        if run_id in self._hydrated:
            return
        response = self.table.query(KeyConditionExpression=self._partition_key(run_id))
        items = list(response.get("Items") or [])
        stored_tenant = next(
            (str(item.get("tenant_id") or "") for item in items if item.get("kind") == "run"),
            "",
        )
        if stored_tenant and stored_tenant != tenant_id:
            raise EvidenceStoreError("evidence workspace is outside the tenant boundary")
        workspace = EvidenceRun(tenant_id=stored_tenant or tenant_id)
        for item in items:
            kind = str(item.get("kind") or "")
            payload = self._decode_item(item)
            try:
                if kind == "evidence":
                    reference = EvidenceReference(
                        evidence_id=str(payload["evidence_id"]),
                        source_type=str(payload["source_type"]),
                        source_record_id=str(payload["source_record_id"]),
                        scenario_run_id=str(payload["scenario_run_id"]),
                        tenant_id=str(payload["tenant_id"]),
                        observed_at=str(payload["observed_at"]),
                        query=dict(payload.get("query") or {}),
                    )
                    workspace.evidence[reference.evidence_id] = reference
                elif kind == "finding":
                    finding = Finding(
                        finding_id=str(payload["finding_id"]),
                        agent=str(payload["agent"]),
                        claim_type=str(payload["claim_type"]),
                        statement=str(payload["statement"]),
                        evidence_ids=tuple(str(item) for item in payload.get("evidence_ids", [])),
                        confidence=float(payload["confidence"]),
                        scenario_run_id=str(payload["scenario_run_id"]),
                        tenant_id=str(payload["tenant_id"]),
                        created_at=str(payload.get("created_at") or "synthetic"),
                    )
                    workspace.findings[finding.finding_id] = finding
                elif kind == "hypothesis":
                    hypothesis = Hypothesis(
                        hypothesis_id=str(payload["hypothesis_id"]),
                        statement=str(payload["statement"]),
                        supporting_finding_ids=tuple(str(item) for item in payload.get("supporting_finding_ids", [])),
                        contradicting_finding_ids=tuple(str(item) for item in payload.get("contradicting_finding_ids", [])),
                        confidence=float(payload["confidence"]),
                        scenario_run_id=str(payload["scenario_run_id"]),
                        tenant_id=str(payload["tenant_id"]),
                        unresolved_questions=tuple(str(item) for item in payload.get("unresolved_questions", [])),
                        created_at=str(payload.get("created_at") or "synthetic"),
                    )
                    workspace.hypotheses[hypothesis.hypothesis_id] = hypothesis
                elif kind == "question":
                    workspace.unresolved_questions.append(dict(payload))
                elif kind == "tool_event":
                    workspace.tool_events.append(dict(payload))
            except (KeyError, TypeError, ValueError) as exc:
                raise EvidenceStoreError(f"invalid persisted evidence record: {item.get('evidence_key')}") from exc
        self.runs[run_id] = workspace
        self._hydrated.add(run_id)
        self._put_item(run_id, "run", {"tenant_id": workspace.tenant_id}, record_key="run")

    @staticmethod
    def _partition_key(run_id: str) -> Any:
        from boto3.dynamodb.conditions import Key

        return Key("run_id").eq(run_id)

    @staticmethod
    def _decode_item(item: dict[str, Any]) -> dict[str, Any]:
        encoded = item.get("payload_json")
        if encoded is not None:
            value = json.loads(str(encoded))
            return dict(value) if isinstance(value, dict) else {}
        return dict(item.get("payload") or {})

    def _put_item(self, run_id: str, kind: str, payload: dict[str, Any], *, record_key: str) -> None:
        if self.table is None:
            return
        self.table.put_item(
            Item={
                "run_id": run_id,
                "evidence_key": record_key,
                "kind": kind,
                "tenant_id": self.runs[run_id].tenant_id,
                "payload_json": json.dumps(payload, sort_keys=True),
            }
        )

    def record_tool_response(self, response: ToolResponse) -> None:
        with self._lock:
            workspace = self._workspace(response.scenario_run_id, response.tenant_id)
            for reference in response.evidence:
                if reference.scenario_run_id != response.scenario_run_id or reference.tenant_id != response.tenant_id:
                    raise EvidenceStoreError("evidence reference does not match the tool response scope")
                workspace.evidence[reference.evidence_id] = reference
            tool_event = {
                "request_id": response.request_id,
                "tool_name": response.tool_name,
                "status": response.status,
                "data": response.data,
                "errors": list(response.errors),
                "evidence_ids": [reference.evidence_id for reference in response.evidence],
                "observed_at": response.observed_at,
                "role": response.role,
            }
            workspace.tool_events.append(tool_event)
            for reference in response.evidence:
                self._put_item(
                    response.scenario_run_id,
                    "evidence",
                    reference.to_dict(),
                    record_key=f"evidence#{reference.evidence_id}",
                )
            self._put_item(
                response.scenario_run_id,
                "tool_event",
                tool_event,
                record_key=f"tool#{response.request_id}",
            )

    def add_finding(self, finding: Finding) -> Finding:
        with self._lock:
            workspace = self._workspace(finding.scenario_run_id, finding.tenant_id)
            for evidence_id in finding.evidence_ids:
                if evidence_id not in workspace.evidence:
                    raise EvidenceStoreError(f"finding cites unknown evidence: {evidence_id}")
            prior = workspace.findings.get(finding.finding_id)
            if prior and prior != finding:
                raise EvidenceStoreError(f"finding ID is already bound to different content: {finding.finding_id}")
            workspace.findings[finding.finding_id] = finding
            self._put_item(
                finding.scenario_run_id,
                "finding",
                finding.to_dict(),
                record_key=f"finding#{finding.finding_id}",
            )
            return finding

    def ensure_evidence_ids(self, run_id: str, tenant_id: str, evidence_ids: list[str] | tuple[str, ...]) -> None:
        """Hydrate valid source references when a managed stream omits tool payloads.

        InvokeHarness can expose the tool call and the final model response
        without retaining every intermediate Gateway payload in the caller's
        process. Before accepting a citation, resolve it against the same
        scenario engine and create the bounded provenance reference locally.
        Unknown IDs remain invalid and are rejected by ``add_finding``.
        """

        with self._lock:
            workspace = self._workspace(run_id, tenant_id)
            missing = [str(item) for item in evidence_ids if str(item) not in workspace.evidence]
            if not missing or self.engine is None:
                return
            sources = (
                "incidents",
                "metrics",
                "logs",
                "deployments",
                "configuration_changes",
                "database_metrics",
                "queue_metrics",
                "orders",
                "runbooks",
                "service_health",
            )
            records_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
            for source in sources:
                records = (
                    [self.engine.service_health_record(run_id, tenant_id=tenant_id)]
                    if source == "service_health"
                    else self.engine.observe(run_id, source, tenant_id=tenant_id)
                )
                for record in records:
                    record_id = str(record.get("record_id") or record.get("id") or "").strip()
                    if record_id:
                        records_by_id[f"EVD-{record_id}"] = (source, record)
            for evidence_id in missing:
                match = records_by_id.get(evidence_id)
                if not match:
                    continue
                source, record = match
                workspace.evidence[evidence_id] = EvidenceReference(
                    evidence_id=evidence_id,
                    source_type=source,
                    source_record_id=evidence_id.removeprefix("EVD-"),
                    scenario_run_id=run_id,
                    tenant_id=tenant_id,
                    observed_at=str(record.get("timestamp") or record.get("observed_at") or "synthetic"),
                    query={"source": source, "hydrated": True},
                )
                self._put_item(
                    run_id,
                    "evidence",
                    workspace.evidence[evidence_id].to_dict(),
                    record_key=f"evidence#{evidence_id}",
                )

    def add_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        with self._lock:
            workspace = self._workspace(hypothesis.scenario_run_id, hypothesis.tenant_id)
            missing = [item for item in hypothesis.supporting_finding_ids if item not in workspace.findings]
            missing += [item for item in hypothesis.contradicting_finding_ids if item not in workspace.findings]
            if missing:
                raise EvidenceStoreError("hypothesis references unknown findings: " + ", ".join(sorted(set(missing))))
            prior = workspace.hypotheses.get(hypothesis.hypothesis_id)
            if prior and prior != hypothesis:
                raise EvidenceStoreError(f"hypothesis ID is already bound to different content: {hypothesis.hypothesis_id}")
            workspace.hypotheses[hypothesis.hypothesis_id] = hypothesis
            self._put_item(
                hypothesis.scenario_run_id,
                "hypothesis",
                hypothesis.to_dict(),
                record_key=f"hypothesis#{hypothesis.hypothesis_id}",
            )
            return hypothesis

    def add_unresolved_question(self, run_id: str, tenant_id: str, question: str, *, requested_by: str) -> dict[str, Any]:
        with self._lock:
            workspace = self._workspace(run_id, tenant_id)
            value = {"question": str(question).strip(), "requested_by": str(requested_by).strip()}
            if not value["question"] or not value["requested_by"]:
                raise EvidenceStoreError("question and requested_by are required")
            if value not in workspace.unresolved_questions:
                workspace.unresolved_questions.append(value)
                question_key = hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()[:24]
                self._put_item(run_id, "question", value, record_key=f"question#{question_key}")
            return value

    def _resolve_observations(
        self,
        run_id: str,
        tenant_id: str,
        workspace: EvidenceRun,
    ) -> list[dict[str, Any]]:
        """Return the observable records behind persisted evidence references.

        Evidence references intentionally remain small provenance records. The
        operator console also needs the actual observation values, so resolve
        those references back through the simulator's agent-visible surface.
        This never reads hidden evaluator truth.
        """

        if self.engine is None or not workspace.evidence:
            return []
        references = list(workspace.evidence.values())
        wanted = {(item.source_type, item.source_record_id): item for item in references}
        resolved: dict[str, dict[str, Any]] = {}
        for source in {item.source_type for item in references}:
            if source == "service_health":
                records = [self.engine.service_health_record(run_id, tenant_id=tenant_id)]
            else:
                try:
                    records = self.engine.observe(run_id, source, tenant_id=tenant_id)
                except (KeyError, ValueError):
                    continue
            for record in records:
                record_id = str(record.get("record_id") or record.get("id") or "").strip()
                reference = wanted.get((source, record_id))
                if not reference or reference.evidence_id in resolved:
                    continue
                resolved[reference.evidence_id] = {
                    "evidence_id": reference.evidence_id,
                    "source_type": reference.source_type,
                    "source_record_id": reference.source_record_id,
                    "observed_at": reference.observed_at,
                    "record": record,
                }
        return [resolved[item.evidence_id] for item in references if item.evidence_id in resolved]

    def get_shared_evidence(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        with self._lock:
            workspace = self._workspace(run_id, tenant_id)
            return {
                "evidence": [item.to_dict() for item in workspace.evidence.values()],
                "findings": [item.to_dict() for item in workspace.findings.values()],
                "hypotheses": [item.to_dict() for item in workspace.hypotheses.values()],
                "unresolved_questions": list(workspace.unresolved_questions),
                "tool_events": list(workspace.tool_events),
                "observations": self._resolve_observations(run_id, tenant_id, workspace),
            }

    def validate_report_inputs(self, run_id: str, *, tenant_id: str) -> None:
        with self._lock:
            workspace = self._workspace(run_id, tenant_id)
            if not workspace.findings:
                raise EvidenceStoreError("at least one finding is required")
            if not workspace.hypotheses:
                raise EvidenceStoreError("at least one hypothesis is required")
            for finding in workspace.findings.values():
                for evidence_id in finding.evidence_ids:
                    if evidence_id not in workspace.evidence:
                        raise EvidenceStoreError(f"finding lost evidence reference: {evidence_id}")

    def _workspace(self, run_id: str, tenant_id: str) -> EvidenceRun:
        self.register_run(run_id, tenant_id)
        workspace = self.runs[run_id]
        if workspace.tenant_id != tenant_id:
            raise EvidenceStoreError("evidence workspace is outside the tenant boundary")
        return workspace
