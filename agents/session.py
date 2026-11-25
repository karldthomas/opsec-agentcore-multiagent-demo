"""Role-scoped Harness session invocation and output contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .harness import AgentCoreHarnessClient, HarnessInvocationResult, InvocationContext
from .output import ParsedAgentOutput, parse_agent_output
from .roles import AGENT_ROLES


@dataclass(frozen=True, slots=True)
class AgentSessionResult:
    role: str
    invocation: HarnessInvocationResult
    output: ParsedAgentOutput | None
    output_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.invocation.succeeded and self.output is not None and self.output_error is None


class AgentSession:
    def __init__(
        self,
        client: AgentCoreHarnessClient,
        role: str,
        *,
        registry_resolver: Any | None = None,
        catalog: Any | None = None,
        evidence: Any | None = None,
    ) -> None:
        if role not in AGENT_ROLES:
            raise ValueError(f"unknown agent role: {role}")
        self.client = client
        self.role = role
        self.registry_resolver = registry_resolver
        self.catalog = catalog
        self.evidence = evidence

    def _materialize_harness_evidence(
        self,
        invocation: HarnessInvocationResult,
        *,
        run_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> None:
        """Record bounded read-only Gateway observations for provenance.

        InvokeHarness streams the model/tool interaction, but the application
        evidence workspace needs the typed ``ToolResponse`` records that back
        cited evidence IDs. Replaying the exact read-only request against the
        same authoritative simulator is deterministic and cannot mutate state.
        """

        if self.catalog is None or self.evidence is None:
            return
        from gateway.tool_catalog import ToolContext

        for call in invocation.tool_calls:
            name = str(call.get("name") or "").strip()
            if "___" in name:
                name = name.rsplit("___", 1)[1]
            if name not in self.catalog.names() or not self.catalog.spec(name).read_only:
                continue
            parameters = dict(call.get("input") or {})
            supplied_run = str(parameters.pop("run_id", run_id)).strip()
            supplied_tenant = str(parameters.pop("tenant_id", tenant_id)).strip()
            supplied_role = str(parameters.pop("role", self.role)).strip()
            supplied_actor = str(parameters.pop("actor_id", actor_id)).strip()
            if supplied_run != run_id or supplied_tenant != tenant_id or supplied_role != self.role:
                continue
            response = self.catalog.invoke(
                name,
                ToolContext(run_id, tenant_id, supplied_actor, self.role),
                parameters,
            )
            if response.status == "ok":
                self.evidence.record_tool_response(response)

    def build_prompt(
        self,
        objective: str,
        *,
        run_id: str,
        tenant_id: str,
        incident_id: str,
        actor_id: str = "local-operator",
        registry_record: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        role = AGENT_ROLES[self.role]
        return (
            f"Investigation objective: {objective}\n"
            f"Scenario run ID: {run_id}\n"
            f"Tenant ID: {tenant_id}\n"
            f"Actor ID: {actor_id}\n"
            f"Incident ID: {incident_id}\n"
            f"Approved Registry record: {registry_record or 'static role configuration'}\n"
            f"Runtime context: {context or {}}\n\n"
            f"You are the {role.name}. Use only your role's allowed capabilities.\n"
            f"Allowed tool names for this role: {', '.join(role.allowed_tools)}\n"
            "Use a small sufficient evidence set (normally no more than six tool calls). "
            "Do not call tools assigned to another specialist. Once the evidence is sufficient, stop calling tools and return the JSON object.\n"
            "Follow-up request agent must be one of telemetry-agent, change-agent, or data-agent. "
            "Use data-agent for customer, order, queue, or database impact questions. "
            "Only cite evidence IDs returned by a tool in this invocation or already present in the shared evidence workspace; never invent or infer EVD IDs.\n"
            "Return only one JSON object with this shape:\n"
            '{"findings":[{"claim_type":"fact|relationship|hypothesis","statement":"...",'
            '"evidence_ids":["EVD-..."],"confidence":0.0}],'
            '"hypotheses":[{"statement":"...","supporting_finding_ids":[],"'
            '"contradicting_finding_ids":[],"confidence":0.0}],'
            '"unresolved_questions":[],"follow_up_requests":[{"agent":"role-key","objective":"..."}],"recommendations":[]}\n'
            "Do not include hidden root cause labels, evaluator fields, chain-of-thought, or unsupported citations."
        )

    def invoke(
        self,
        objective: str,
        *,
        run_id: str,
        tenant_id: str,
        actor_id: str,
        incident_id: str,
        registry_record: str | None = None,
        context: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> AgentSessionResult:
        approved_record = registry_record
        if self.registry_resolver is not None:
            # Resolver enforces approved status and the static role allowlist;
            # model-supplied record names are never trusted as authority.
            record = self.registry_resolver.resolve_agent(self.role)
            approved_record = str(record.get("name") or record.get("recordArn") or "")
        invocation_context = InvocationContext.create(
            run_id,
            self.role,
            tenant_id=tenant_id,
            actor_id=actor_id,
            session_id=session_id,
        )
        prompt = self.build_prompt(
            objective,
            run_id=run_id,
            tenant_id=tenant_id,
            incident_id=incident_id,
            actor_id=actor_id,
            registry_record=approved_record,
            context=context,
        )
        invocation = self.client.invoke(invocation_context, prompt)
        if not invocation.succeeded:
            return AgentSessionResult(self.role, invocation, None, "Harness invocation failed")
        try:
            self._materialize_harness_evidence(
                invocation,
                run_id=run_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
        except Exception as exc:
            return AgentSessionResult(self.role, invocation, None, f"evidence materialization failed: {exc}")
        try:
            output = parse_agent_output(invocation.text)
        except Exception as exc:
            # Keep a transient schema error from failing the whole parallel
            # branch. The repair turn uses the same role/run binding and does
            # not add any capability or authorization.
            repair_context = InvocationContext.create(
                run_id,
                self.role,
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
            try:
                repair = self.client.invoke(
                    repair_context,
                    prompt
                    + "\nYour previous response failed schema validation: "
                    + str(exc)
                    + ". Return a corrected JSON object only. Every finding must include claim_type, statement, evidence_ids, and confidence. Every hypothesis must include statement, supporting_finding_ids, contradicting_finding_ids, and confidence.",
                )
            except Exception as repair_exc:
                return AgentSessionResult(self.role, invocation, None, f"schema validation failed: {exc}; repair invocation failed: {repair_exc}")
            if not repair.succeeded:
                return AgentSessionResult(self.role, repair, None, str(exc))
            try:
                self._materialize_harness_evidence(
                    repair,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
            except Exception as repair_exc:
                return AgentSessionResult(self.role, repair, None, f"evidence materialization failed after repair: {repair_exc}")
            try:
                output = parse_agent_output(repair.text)
            except Exception as repair_exc:
                return AgentSessionResult(self.role, repair, None, f"schema validation failed after repair: {repair_exc}")
            return AgentSessionResult(self.role, repair, output)
        return AgentSessionResult(self.role, invocation, output)
