"""Durable investigation lifecycle shared by local tests and cloud workers.

The class is intentionally a small state-machine adapter.  AWS Step
Functions will call the same stage methods from separate workers; it does not
reimplement Harness reasoning or tool selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.session import AgentSession
from evidence import EvidenceStore
from simulator.engine import ScenarioEngine, SimulatorError

from .checkpoints import CheckpointNotFound, CheckpointStore, WorkflowCheckpoint
from .local import InvestigationCoordinator, InvestigationRunResult, TaskResult


class WorkflowInterrupted(RuntimeError):
    """A controlled interruption that should be resumed with the same run ID."""


class WorkflowError(RuntimeError):
    """Raised for invalid stage transitions or terminal workflow failures."""


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    run_id: str
    status: str
    checkpoint: WorkflowCheckpoint
    investigation: InvestigationRunResult | None = None
    error: str | None = None


class DurableInvestigationWorkflow:
    """Coordinate durable stages around specialist Harness sessions."""

    AUTOMATIC_FOLLOW_UP_ATTEMPTS = 3
    LEGACY_ACTION_STAGES = frozenset(
        {
            "awaiting_remediation_plan",
            "awaiting_approval",
            "execute_approved_remediation",
            "awaiting_verification",
            "verify_recovery",
            "awaiting_report",
        }
    )

    def __init__(
        self,
        engine: ScenarioEngine,
        evidence: EvidenceStore,
        coordinator: InvestigationCoordinator,
        checkpoints: CheckpointStore,
        *,
        orchestrator_session: AgentSession | None = None,
    ) -> None:
        self.engine = engine
        self.evidence = evidence
        self.coordinator = coordinator
        self.checkpoints = checkpoints
        self.orchestrator_session = orchestrator_session

    def start(
        self,
        run_id: str,
        *,
        objective: str,
        actor_id: str,
        incident_id: str | None = None,
    ) -> WorkflowResult:
        state = self.engine.get_run(run_id)
        metadata = {
            "objective": objective,
            "actor_id": actor_id,
            "tenant_id": state.run.tenant_id,
            "incident_id": incident_id or state.definition.primary_incident_id,
        }
        try:
            checkpoint = self.checkpoints.create(
                WorkflowCheckpoint(run_id=run_id, stage="initialize", metadata=metadata)
            )
        except Exception:
            checkpoint = self.checkpoints.get(run_id)
        return self.resume(run_id, checkpoint=checkpoint)

    def resume(self, run_id: str, *, checkpoint: WorkflowCheckpoint | None = None) -> WorkflowResult:
        current = checkpoint or self._get_checkpoint(run_id)
        investigation: InvestigationRunResult | None = None

        # Runs created before the analysis-only boundary may still have a
        # remediation or recovery checkpoint. Reopen those runs as complete
        # analysis; never advance a simulator action while migrating them.
        if current.stage in self.LEGACY_ACTION_STAGES:
            legacy_stage = current.stage
            current = self._save(
                current,
                stage="completed",
                completed_stages=tuple(dict.fromkeys((*current.completed_stages, "analysis_complete"))),
                metadata={
                    **current.metadata,
                    "analysis_complete": True,
                    "read_only_mode": True,
                    "legacy_action_stage": legacy_stage,
                },
            )

        if current.stage == "initialize":
            current = self._save(current, stage="parallel_investigation")

        if current.stage == "parallel_investigation":
            try:
                investigation = self.coordinator.run(
                    run_id,
                    objective=str(current.metadata["objective"]),
                    actor_id=str(current.metadata["actor_id"]),
                    incident_id=str(current.metadata["incident_id"]),
                )
            except SimulatorError as exc:
                if exc.code == "INJECTED_FAILURE":
                    current = self._save(current, retry_count=current.retry_count + 1)
                    return WorkflowResult(run_id, "retryable_failure", current, error=str(exc))
                raise WorkflowError(str(exc)) from exc
            except Exception as exc:  # pragma: no cover - provider/runtime boundary.
                current = self._save(
                    current,
                    retry_count=current.retry_count + 1,
                    metadata={
                        **current.metadata,
                        "last_error_stage": "parallel_investigation",
                        "last_error": str(exc),
                    },
                )
                return WorkflowResult(
                    run_id,
                    "retryable_failure",
                    current,
                    error="The evidence checks could not be handed off to the next workflow step.",
                )
            failed_tasks = tuple(item for item in investigation.task_results if item.status != "completed")
            if failed_tasks:
                follow_up_requests = list(investigation.follow_up_requests)
                follow_up_objectives = {
                    "telemetry-agent": "Confirm service health, resource pressure, and timeout evidence before analysis is complete.",
                    "change-agent": "Confirm the deployment and configuration change associated with the customer impact.",
                    "data-agent": "Confirm database pressure, processing backlog, and customer impact before analysis is complete.",
                }
                for task in failed_tasks:
                    request = {
                        "agent": task.role,
                        "objective": follow_up_objectives.get(
                            task.role,
                            "Complete the missing evidence checks before analysis is complete.",
                        ),
                    }
                    if request not in follow_up_requests:
                        follow_up_requests.append(request)
                investigation = InvestigationRunResult(
                    run_id=investigation.run_id,
                    task_results=investigation.task_results,
                    findings=investigation.findings,
                    hypotheses=investigation.hypotheses,
                    unresolved_questions=investigation.unresolved_questions,
                    workflow_events=investigation.workflow_events,
                    follow_up_requests=tuple(follow_up_requests),
                )
            current = self._save(
                current,
                stage="reconcile_evidence",
                completed_stages=(*current.completed_stages, "parallel_investigation"),
                task_results={item.task_id: item.to_dict() for item in investigation.task_results},
                metadata={
                    **current.metadata,
                    "investigation": self._investigation_checkpoint_payload(investigation),
                    "follow_up_requests": list(investigation.follow_up_requests),
                    "task_failures": [
                        {
                            "role": item.role,
                            "error": item.error or (item.session_result.output_error if item.session_result else "unknown task failure"),
                        }
                        for item in failed_tasks
                    ],
                },
            )
            # Persist the specialist checkpoint before the optional model
            # reconciliation. If the orchestrator is interrupted, the next
            # request can continue from verified specialist evidence without
            # repeating the parallel branch.
            if self.orchestrator_session is not None:
                try:
                    orchestration = self.orchestrator_session.invoke(
                        "Reconcile the completed specialist investigation. Identify the strongest causal explanation, distinguish observed facts from hypotheses, and request follow-up only for material evidence gaps.",
                        run_id=run_id,
                        tenant_id=str(current.metadata["tenant_id"]),
                        actor_id=str(current.metadata["actor_id"]),
                        incident_id=str(current.metadata["incident_id"]),
                        registry_record="opsfusion-dev-orchestrator",
                        context={
                            "incident_context": self.engine.get_incident_context(run_id, tenant_id=str(current.metadata["tenant_id"])),
                            "shared_evidence": self.evidence.get_shared_evidence(run_id, tenant_id=str(current.metadata["tenant_id"])),
                        },
                    )
                    if orchestration.succeeded:
                        self.coordinator.persist_output(
                            run_id,
                            str(current.metadata["tenant_id"]),
                            "orchestrator",
                            orchestration,
                        )
                        investigation = InvestigationRunResult(
                            run_id=investigation.run_id,
                            task_results=investigation.task_results
                            + (TaskResult(
                                task_id=f"task-orchestrator-reconcile-{run_id}",
                                role="orchestrator",
                                status="completed",
                                session_result=orchestration,
                            ),),
                            findings=tuple(self.evidence.runs[run_id].findings.values()),
                            hypotheses=tuple(self.evidence.runs[run_id].hypotheses.values()),
                            unresolved_questions=tuple(
                                question["question"] for question in self.evidence.runs[run_id].unresolved_questions
                            ),
                            workflow_events=investigation.workflow_events
                            + ({"event": "orchestrator_reconciled", "role": "orchestrator"},),
                            follow_up_requests=investigation.follow_up_requests + orchestration.output.follow_up_requests,
                        )
                        current = self._save(
                            current,
                            metadata={
                                **current.metadata,
                                "investigation": self._investigation_checkpoint_payload(investigation),
                            },
                        )
                except Exception as exc:  # pragma: no cover - defensive model boundary.
                    current = self._save(
                        current,
                        metadata={**current.metadata, "orchestrator_error": str(exc)},
                    )
            # This flag is consumed only after specialist output has been
            # persisted, so resume does not have to repeat completed work.
            if self.engine.consume_failure(run_id, "after_telemetry") or self.engine.consume_failure(
                run_id, "after_parallel_investigation"
            ):
                current = self._save(current, stage="reconcile_evidence")
                raise WorkflowInterrupted(
                    f"controlled interruption after specialist checkpoint for {run_id}"
                )

        if current.stage == "reconcile_evidence":
            investigation = investigation or self._investigation_from_checkpoint(current)
            # A retry can land on a fresh worker, so make the stage decision
            # from persisted evidence as well as this process's cache.
            state = self.engine.get_run(run_id)
            shared_evidence = self.evidence.get_shared_evidence(run_id, tenant_id=state.run.tenant_id)
            workspace = self.evidence.runs.get(run_id)
            has_findings = (
                bool(investigation and investigation.findings)
                or bool(workspace and workspace.findings)
                or bool(shared_evidence.get("findings"))
            )
            follow_up_requests = (
                list(investigation.follow_up_requests)
                if investigation
                else list(current.metadata.get("follow_up_requests") or [])
            )
            if follow_up_requests:
                current = self._save(
                    current,
                    stage="awaiting_follow_up",
                    metadata={**current.metadata, "follow_up_requests": follow_up_requests},
                )
                # Evidence-gap checks are read-only. They are part of the
                # investigation, not an operator decision, so run the
                # requested checks before exposing a manual pause. A bounded
                # retry loop handles transient Harness/Gateway failures
                # without repeating the parallel investigation branch.
                return self._run_automatic_follow_up(run_id, follow_up_requests)
            if not has_findings:
                current = self._save(current, stage="awaiting_follow_up")
                return WorkflowResult(run_id, "needs_follow_up", current, investigation=investigation)
            current = self._save(
                current,
                stage="completed",
                completed_stages=tuple(
                    dict.fromkeys(
                        (*current.completed_stages, "reconcile_evidence", "analysis_complete")
                    )
                ),
                metadata={
                    **current.metadata,
                    "analysis_complete": True,
                    "read_only_mode": True,
                },
            )

        if current.stage == "awaiting_follow_up":
            return WorkflowResult(run_id, "needs_follow_up", current, investigation=investigation)
        if current.stage == "completed":
            return WorkflowResult(run_id, current.stage, current, investigation=investigation)
        raise WorkflowError(f"unsupported workflow stage: {current.stage}")

    def _run_automatic_follow_up(
        self,
        run_id: str,
        requests: list[dict[str, Any]],
    ) -> WorkflowResult:
        """Complete read-only evidence follow-ups before returning to the API.

        The checkpoint remains durable after every attempt. Follow-up agents
        answer the requests emitted by reconciliation; they do not create an
        open-ended delegation chain of their own. Persistent failures remain
        visible as an errored checkpoint rather than becoming a manual action.
        """

        selected = [dict(item) for item in requests]
        result = self.request_follow_up(run_id, selected)
        for _ in range(self.AUTOMATIC_FOLLOW_UP_ATTEMPTS - 1):
            if result.status != "needs_follow_up":
                return result
            pending = result.checkpoint.metadata.get("follow_up_requests") or []
            if not pending:
                return result
            selected = [dict(item) for item in pending if isinstance(item, dict)]
            if not selected:
                return result
            result = self.request_follow_up(run_id, selected)
        return result

    def request_follow_up(self, run_id: str, requests: list[dict[str, Any]] | None = None) -> WorkflowResult:
        current = self._get_checkpoint(run_id)
        if current.stage != "awaiting_follow_up":
            raise WorkflowError(f"follow-up is not available in stage {current.stage}")
        state = self.engine.get_run(run_id)
        selected = requests or list(current.metadata.get("follow_up_requests", []))
        results = self.coordinator.run_follow_up(
            run_id,
            selected,
            actor_id=str(current.metadata["actor_id"]),
            incident_id=str(current.metadata["incident_id"]),
        )
        failed = tuple(item for item in results if item.status != "completed")
        if failed:
            remaining_requests = [dict(item.request) for item in failed if item.request]
            saved = self._save(
                current,
                stage="awaiting_follow_up",
                task_results={item.task_id: item.to_dict() for item in results},
                metadata={
                    **current.metadata,
                    "follow_up_requests": remaining_requests,
                    "follow_up_results": [self._task_result_summary(item) for item in results],
                    "follow_up_failures": [
                        {
                            "role": item.role,
                            "error": item.error or (item.session_result.output_error if item.session_result else "unknown check failure"),
                        }
                        for item in failed
                    ],
                },
            )
            return WorkflowResult(
                run_id,
                "needs_follow_up",
                saved,
                error="Some decision checks did not complete; the completed checks were saved.",
            )
        saved = self._save(
            current,
            stage="reconcile_evidence",
            completed_stages=(*current.completed_stages, "follow_up_investigation"),
            task_results={item.task_id: item.to_dict() for item in results},
            # The selected requests have now been executed.  Clear the
            # pending request list before resuming reconciliation; otherwise
            # a durable resume would rediscover the same stale requests and
            # repeatedly re-run the follow-up stage.
            metadata={
                **current.metadata,
                "follow_up_requests": [],
                "follow_up_results": [self._task_result_summary(item) for item in results],
                "follow_up_failures": [],
            },
        )
        # Continue from the checkpoint that was just committed. Re-reading a
        # second copy could make a successful follow-up appear stuck if a
        # worker observes the old version between these two operations.
        return self.resume(run_id, checkpoint=saved)

    def _get_checkpoint(self, run_id: str) -> WorkflowCheckpoint:
        try:
            return self.checkpoints.get(run_id)
        except CheckpointNotFound as exc:
            raise WorkflowError(f"workflow has not been initialized: {run_id}") from exc

    @staticmethod
    def _task_result_summary(item: TaskResult) -> dict[str, Any]:
        """Keep repeated follow-up metadata small and operator-useful."""

        return {
            "task_id": item.task_id,
            "role": item.role,
            "status": item.status,
            "error": item.error or (item.session_result.output_error if item.session_result else None),
            "request": dict(item.request) if item.request else None,
        }

    @classmethod
    def _investigation_checkpoint_payload(cls, investigation: InvestigationRunResult) -> dict[str, Any]:
        """Persist reconciliation context without duplicating full sessions."""

        payload = investigation.to_dict()
        payload["task_results"] = [cls._task_result_summary(item) for item in investigation.task_results]
        return payload

    def _save(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        stage: str | None = None,
        completed_stages: tuple[str, ...] | None = None,
        task_results: dict[str, Any] | None = None,
        retry_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowCheckpoint:
        candidate = WorkflowCheckpoint(
            run_id=checkpoint.run_id,
            stage=stage or checkpoint.stage,
            completed_stages=completed_stages or checkpoint.completed_stages,
            task_results=task_results if task_results is not None else checkpoint.task_results,
            retry_count=checkpoint.retry_count if retry_count is None else retry_count,
            metadata=metadata if metadata is not None else checkpoint.metadata,
        )
        return self.checkpoints.save(candidate, expected_version=checkpoint.version)

    @staticmethod
    def _investigation_from_checkpoint(checkpoint: WorkflowCheckpoint) -> InvestigationRunResult | None:
        payload = checkpoint.metadata.get("investigation")
        if not isinstance(payload, dict):
            return None
        # The checkpoint is intentionally a compact resumable representation;
        # the evidence store remains the authoritative source for findings.
        return None
