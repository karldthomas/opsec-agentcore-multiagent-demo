"""Local parallel investigation coordinator.

This is a testable equivalent of the investigation branch that will later be
expressed as a Step Functions Parallel state. Harness still owns each agent's
reasoning loop; this module only coordinates independent sessions.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agents.session import AgentSession, AgentSessionResult
from contracts import Finding, Hypothesis
from evidence import EvidenceStore, EvidenceStoreError
from simulator.engine import ScenarioEngine, SimulatorError


INVESTIGATION_ROLES = ("telemetry-agent", "change-agent", "data-agent")


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    role: str
    status: str
    session_result: AgentSessionResult | None = None
    error: str | None = None
    request: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role": self.role,
            "status": self.status,
            "session": self.session_result.invocation.to_dict() if self.session_result else None,
            "output": self.session_result.output.to_dict() if self.session_result and self.session_result.output else None,
            "output_error": self.session_result.output_error if self.session_result else None,
            "error": self.error,
            "request": dict(self.request) if self.request else None,
        }


@dataclass(frozen=True, slots=True)
class InvestigationRunResult:
    run_id: str
    task_results: tuple[TaskResult, ...]
    findings: tuple[Finding, ...]
    hypotheses: tuple[Hypothesis, ...]
    unresolved_questions: tuple[str, ...]
    workflow_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    follow_up_requests: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return bool(self.task_results) and all(item.status == "completed" for item in self.task_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "succeeded": self.succeeded,
            "task_results": [item.to_dict() for item in self.task_results],
            "findings": [item.to_dict() for item in self.findings],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "unresolved_questions": list(self.unresolved_questions),
            "follow_up_requests": [dict(item) for item in self.follow_up_requests],
            "workflow_events": list(self.workflow_events),
        }


class InvestigationCoordinator:
    """Coordinate parallel specialist Harness sessions and persist findings."""

    def __init__(self, engine: ScenarioEngine, evidence: EvidenceStore, sessions: dict[str, AgentSession]) -> None:
        missing = sorted(set(INVESTIGATION_ROLES) - set(sessions))
        if missing:
            raise ValueError("missing specialist sessions: " + ", ".join(missing))
        self.engine = engine
        self.evidence = evidence
        self.sessions = sessions

    def run(
        self,
        run_id: str,
        *,
        objective: str,
        actor_id: str,
        incident_id: str | None = None,
        max_workers: int = 3,
    ) -> InvestigationRunResult:
        state = self.engine.get_run(run_id)
        incident = incident_id or state.definition.primary_incident_id
        self.evidence.register_run(run_id, state.run.tenant_id)
        events: list[dict[str, Any]] = [
            {"event": "investigation_started", "run_id": run_id, "roles": list(INVESTIGATION_ROLES)},
        ]
        if self.engine.consume_failure(run_id, "parallel_investigation", tenant_id=state.run.tenant_id):
            events.append({"event": "investigation_failed", "stage": "parallel_investigation"})
            raise SimulatorError("injected investigation interruption", "INJECTED_FAILURE")

        task_results: list[TaskResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.sessions[role].invoke,
                    objective,
                    run_id=run_id,
                    tenant_id=state.run.tenant_id,
                    actor_id=actor_id,
                    incident_id=incident,
                    registry_record=f"opsfusion-dev-{role}",
                    context=self.engine.get_incident_context(run_id, tenant_id=state.run.tenant_id),
                ): (role, f"task-{role}-{uuid4().hex[:8]}")
                for role in INVESTIGATION_ROLES
            }
            for future in as_completed(futures):
                role, task_id = futures[future]
                try:
                    session_result = future.result()
                except Exception as exc:  # pragma: no cover - defensive worker boundary.
                    task_results.append(TaskResult(task_id, role, "failed", error=str(exc)))
                    events.append({"event": "task_failed", "task_id": task_id, "role": role, "error": str(exc)})
                    continue
                if session_result.succeeded:
                    try:
                        self._persist_output(run_id, state.run.tenant_id, role, session_result)
                    except Exception as exc:  # pragma: no cover - provider output is outside our process.
                        task_results.append(TaskResult(task_id, role, "failed", session_result=session_result, error=str(exc)))
                        events.append({"event": "task_failed", "task_id": task_id, "role": role, "error": f"evidence persistence failed: {exc}"})
                    else:
                        task_results.append(TaskResult(task_id, role, "completed", session_result=session_result))
                        events.append({"event": "task_completed", "task_id": task_id, "role": role})
                else:
                    task_results.append(TaskResult(task_id, role, "failed", session_result=session_result, error=session_result.output_error))
                    events.append({"event": "task_failed", "task_id": task_id, "role": role, "error": session_result.output_error})

        findings = tuple(self.evidence.runs[run_id].findings.values())
        hypotheses = tuple(self.evidence.runs[run_id].hypotheses.values())
        unresolved = tuple(question["question"] for question in self.evidence.runs[run_id].unresolved_questions)
        follow_ups = tuple(
            request
            for item in task_results
            if item.session_result and item.session_result.output
            for request in item.session_result.output.follow_up_requests
        )
        events.append({"event": "investigation_reconciled", "finding_count": len(findings), "hypothesis_count": len(hypotheses)})
        return InvestigationRunResult(run_id, tuple(task_results), findings, hypotheses, unresolved, tuple(events), follow_ups)

    def run_follow_up(
        self,
        run_id: str,
        requests: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        *,
        actor_id: str,
        incident_id: str | None = None,
    ) -> tuple[TaskResult, ...]:
        """Run explicitly requested evidence-gap investigations.

        Requests are data, not authority: the target role must be one of the
        configured specialists and the same tenant/run context is supplied to
        the session.
        """
        state = self.engine.get_run(run_id)
        incident = incident_id or state.definition.primary_incident_id
        normalized: list[tuple[int, dict[str, Any], str, str, str]] = []
        results: dict[int, TaskResult] = {}
        allowed_roles = set(INVESTIGATION_ROLES).intersection(self.sessions)
        for index, request in enumerate(requests):
            request_data = dict(request)
            objective = str(request_data.get("objective") or request_data.get("question") or "").strip()
            role = str(request_data.get("agent") or request_data.get("role") or "").strip()
            if not role:
                role = self._infer_follow_up_role(objective)
            if role not in allowed_roles:
                # Older model prompts used descriptive role names such as
                # ``customer-impact-agent``. Treat those as routing hints and
                # map them to the configured specialist instead of persisting
                # an impossible task that can never complete.
                inferred_role = self._infer_follow_up_role(objective)
                role = inferred_role if inferred_role in allowed_roles else ""
                if role:
                    request_data["agent"] = role
            task_id = f"follow-up-{role or 'unknown'}-{uuid4().hex[:8]}"
            if role not in allowed_roles or not objective:
                results[index] = TaskResult(
                    task_id,
                    role or "unknown",
                    "failed",
                    error="invalid follow-up request",
                    request=request_data,
                )
                continue
            normalized.append((index, request_data, role, objective, task_id))

        # Follow-up questions are independent read-only checks. Running them
        # concurrently keeps the operator request inside the service timeout
        # and mirrors the parallel investigation stage. EvidenceStore uses a
        # lock around each write, so the durable workspace remains consistent.
        if normalized:
            # Bound the fan-out while allowing duplicate questions from the
            # same specialist role to finish in the same decision-check run.
            max_workers = min(5, len(normalized))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        self._run_follow_up_request,
                        run_id,
                        state.run.tenant_id,
                        actor_id,
                        incident,
                        request_data,
                        role,
                        objective,
                        task_id,
                    ): index
                    for index, request_data, role, objective, task_id in normalized
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:  # pragma: no cover - worker boundary.
                        index_data, request_data, role, _objective, task_id = next(
                            item for item in normalized if item[0] == index
                        )
                        results[index_data] = TaskResult(
                            task_id,
                            role,
                            "failed",
                            error=str(exc),
                            request=dict(request_data),
                        )
        return tuple(results[index] for index in sorted(results))

    def _run_follow_up_request(
        self,
        run_id: str,
        tenant_id: str,
        actor_id: str,
        incident_id: str,
        request: dict[str, Any],
        role: str,
        objective: str,
        task_id: str,
    ) -> TaskResult:
        session_result: AgentSessionResult | None = None
        try:
            session_result = self.sessions[role].invoke(
                objective,
                run_id=run_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                incident_id=incident_id,
                registry_record=f"opsfusion-dev-{role}",
                context=self.engine.get_incident_context(run_id, tenant_id=tenant_id),
            )
            if session_result.succeeded:
                self._persist_output(
                    run_id,
                    tenant_id,
                    role,
                    session_result,
                    id_namespace=task_id,
                )
                return TaskResult(task_id, role, "completed", session_result=session_result, request=dict(request))
            return TaskResult(
                task_id,
                role,
                "failed",
                session_result=session_result,
                error=session_result.output_error or "follow-up check failed",
                request=dict(request),
            )
        except Exception as exc:  # pragma: no cover - worker boundary.
            return TaskResult(
                task_id,
                role,
                "failed",
                session_result=session_result,
                error=str(exc),
                request=dict(request),
            )

    @staticmethod
    def _infer_follow_up_role(objective: str) -> str:
        """Constrain underspecified model requests to a specialist role."""

        text = objective.casefold()
        if any(term in text for term in ("deployment", "rollback", "configuration", "release", "version")):
            return "change-agent"
        if any(term in text for term in ("order", "customer", "queue", "database", "business impact")):
            return "data-agent"
        if any(term in text for term in ("verify", "recovery", "post-remediation")):
            return "telemetry-agent"
        return "telemetry-agent"

    def _persist_output(
        self,
        run_id: str,
        tenant_id: str,
        role: str,
        result: AgentSessionResult,
        *,
        id_namespace: str | None = None,
    ) -> None:
        if not result.output:
            return
        id_prefix = f"{role}-{id_namespace}" if id_namespace else role
        finding_ids: list[str] = []
        prepared_findings: list[Finding] = []
        for index, draft in enumerate(result.output.findings, start=1):
            supplied_finding_id = str(draft.get("finding_id") or "").strip()
            finding_id = (
                supplied_finding_id
                if supplied_finding_id.startswith(f"FIND-{id_prefix}-")
                else f"FIND-{id_prefix}-{index}"
            )
            evidence_ids = tuple(str(item) for item in draft["evidence_ids"])
            self.evidence.ensure_evidence_ids(run_id, tenant_id, evidence_ids)
            prepared_findings.append(Finding(
                finding_id=finding_id,
                agent=role,
                claim_type=str(draft["claim_type"]),
                statement=str(draft["statement"]),
                evidence_ids=evidence_ids,
                confidence=float(draft["confidence"]),
                scenario_run_id=run_id,
                tenant_id=tenant_id,
            ))
            finding_ids.append(finding_id)
        available_finding_ids = set(self.evidence.runs[run_id].findings) | set(finding_ids)
        prepared_hypotheses: list[Hypothesis] = []
        for index, draft in enumerate(result.output.hypotheses, start=1):
            raw_supporting = tuple(str(item) for item in (draft.get("supporting_finding_ids") or ()))
            supporting = tuple(item for item in raw_supporting if item in available_finding_ids)
            if not supporting:
                supporting = tuple(finding_ids) or tuple(available_finding_ids)
            prepared_hypotheses.append(Hypothesis(
                hypothesis_id=(
                    str(draft.get("hypothesis_id") or "").strip()
                    if str(draft.get("hypothesis_id") or "").strip().startswith(f"H-{id_prefix}-")
                    else f"H-{id_prefix}-{index}"
                ),
                statement=str(draft["statement"]),
                supporting_finding_ids=supporting,
                contradicting_finding_ids=tuple(
                    str(item) for item in draft.get("contradicting_finding_ids", []) if str(item) in available_finding_ids
                ),
                confidence=float(draft["confidence"]),
                scenario_run_id=run_id,
                tenant_id=tenant_id,
                unresolved_questions=tuple(result.output.unresolved_questions),
            ))

        # Build the complete branch output before writing any claims. A
        # malformed nested field must fail this branch cleanly, not leave a
        # half-persisted result that can break the durable handoff.
        for finding in prepared_findings:
            self.evidence.add_finding(finding)
        for hypothesis in prepared_hypotheses:
            self.evidence.add_hypothesis(hypothesis)
        for question in result.output.unresolved_questions:
            self.evidence.add_unresolved_question(run_id, tenant_id, question, requested_by=role)

    def persist_output(
        self,
        run_id: str,
        tenant_id: str,
        role: str,
        result: AgentSessionResult,
        *,
        id_namespace: str | None = None,
    ) -> None:
        """Persist a reconciliation session through the same evidence boundary."""

        self._persist_output(run_id, tenant_id, role, result, id_namespace=id_namespace)
