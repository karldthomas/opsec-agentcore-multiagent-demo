"""Programmatic AgentCore Harness adapter for OpsFusion.

Harness owns model reasoning, tool selection, iteration, and stopping. This
module only constructs an invocation boundary and normalizes the event stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

try:
    from botocore.exceptions import EventStreamError
except ImportError:  # pragma: no cover - boto3 is a runtime dependency.
    EventStreamError = RuntimeError  # type: ignore[misc,assignment]

from .roles import AGENT_ROLES, AgentRole


class HarnessInvocationError(RuntimeError):
    """Raised when a Harness invocation cannot be started or validated."""


@dataclass(frozen=True, slots=True)
class InvocationContext:
    scenario_run_id: str
    role: str
    session_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    actor_id: str

    @classmethod
    def create(
        cls,
        scenario_run_id: str,
        role: str,
        *,
        tenant_id: str,
        actor_id: str,
        session_id: str | None = None,
    ) -> "InvocationContext":
        if role not in AGENT_ROLES:
            raise HarnessInvocationError(f"unknown agent role: {role}")
        binding = hashlib.sha256(f"{tenant_id}:{actor_id}:{scenario_run_id}:{role}".encode("utf-8")).hexdigest()[:24]
        supplied = str(session_id or "").strip()
        if supplied and len(supplied) < 33:
            raise HarnessInvocationError("runtimeSessionId must contain at least 33 characters")
        managed = re.fullmatch(r"opsfusion-v1-([0-9a-f]{24})-([0-9a-f]{32})", supplied)
        if managed and managed.group(1) != binding:
            raise HarnessInvocationError("runtimeSessionId is bound to another scenario, role, or actor")
        resolved = supplied or f"opsfusion-v1-{binding}-{uuid4().hex}"
        if supplied and not managed:
            resolved = f"opsfusion-v1-{binding}-{hashlib.sha256(supplied.encode()).hexdigest()[:32]}"
        return cls(
            scenario_run_id=scenario_run_id,
            role=role,
            session_id=resolved,
            request_id=f"hreq-{uuid4().hex}",
            trace_id=uuid4().hex,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    event_type: str
    sequence: int
    context: InvocationContext
    payload: dict[str, Any]
    occurred_at: str

    @property
    def text_delta(self) -> str:
        if self.event_type != "contentBlockDelta":
            return ""
        return str(self.payload.get("delta", {}).get("text") or "")

    @property
    def tool_use(self) -> dict[str, Any] | None:
        if self.event_type == "contentBlockStart":
            value = self.payload.get("start", {}).get("toolUse")
            return dict(value) if isinstance(value, dict) else None
        if self.event_type == "contentBlockDelta":
            value = self.payload.get("delta", {}).get("toolUse")
            return dict(value) if isinstance(value, dict) else None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "sequence": self.sequence,
            "scenario_run_id": self.context.scenario_run_id,
            "role": self.context.role,
            "session_id": self.context.session_id,
            "request_id": self.context.request_id,
            "trace_id": self.context.trace_id,
            "payload": self.payload,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True, slots=True)
class HarnessInvocationResult:
    context: InvocationContext
    text: str
    stop_reason: str | None
    events: tuple[HarnessEvent, ...]
    tool_uses: tuple[dict[str, Any], ...]
    usage: dict[str, Any]
    metrics: dict[str, Any]
    errors: tuple[dict[str, Any], ...]
    skill_trace: dict[str, Any] | None = None
    prompt_trace: dict[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return not self.errors and self.stop_reason not in {
            "runtime_client_error",
            "internal_server_exception",
            "validation_exception",
        }

    def to_dict(self) -> dict[str, Any]:
        role = AGENT_ROLES.get(self.context.role)
        skill_trace = dict(self.skill_trace or {})
        if not skill_trace and role is not None:
            skill_trace = {
                "skills": list(role.skills),
                "status": "trace_unavailable",
                "applied_via": "role system prompt",
                "authority": "reference-only",
            }
        return {
            "scenario_run_id": self.context.scenario_run_id,
            "role": self.context.role,
            "session_id": self.context.session_id,
            "request_id": self.context.request_id,
            "trace_id": self.context.trace_id,
            # Never persist/display the raw model response here.  It may
            # contain private reasoning; the structured parser exposes only
            # bounded findings, hypotheses, and recommendations separately.
            "response_size": len(self.text),
            "stop_reason": self.stop_reason,
            "succeeded": self.succeeded,
            "tool_uses": list(self.tool_uses),
            "event_count": len(self.events),
            "usage": self.usage,
            "metrics": self.metrics,
            "errors": list(self.errors),
            "skill_trace": skill_trace,
            "prompt_trace": dict(self.prompt_trace or {}),
        }

    @property
    def tool_calls(self) -> tuple[dict[str, Any], ...]:
        """Coalesce streamed tool-name and tool-input events.

        The managed stream may emit a tool name and its JSON input in separate
        content-block events. This normalized view is used only to attach
        authoritative evidence references after the Gateway call completes;
        it does not implement or replace Harness orchestration.
        """

        calls: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for use in self.tool_uses:
            if not isinstance(use, dict):
                continue
            name = str(use.get("name") or "").strip()
            if name:
                if current and current.get("name"):
                    calls.append(current)
                current = {"name": name}
            if "input" in use:
                if current is None:
                    current = {}
                value = use.get("input")
                if isinstance(value, str):
                    try:
                        import json

                        value = json.loads(value)
                    except json.JSONDecodeError:
                        value = {}
                current["input"] = value if isinstance(value, dict) else {}
                if current.get("name"):
                    calls.append(current)
                    current = None
        if current and current.get("name"):
            calls.append(current)
        return tuple(calls)


class AgentCoreHarnessClient:
    """Thin wrapper around the managed ``InvokeHarness`` API."""

    def __init__(
        self,
        harness_arn: str,
        *,
        region: str | None = None,
        client: Any | None = None,
        model: dict[str, Any] | None = None,
        gateway_arn: str | None = None,
        skills: list[dict[str, Any]] | None = None,
        max_iterations: int = 16,
        max_tokens: int = 4096,
        timeout_seconds: int = 180,
    ) -> None:
        if not str(harness_arn or "").strip():
            raise HarnessInvocationError("a Harness ARN is required")
        if max_iterations <= 0 or max_tokens <= 0 or timeout_seconds <= 0:
            raise HarnessInvocationError("Harness limits must be positive")
        self.harness_arn = harness_arn.strip()
        self.model = model or {"bedrockModelConfig": {"modelId": "us.amazon.nova-2-lite-v1:0", "apiFormat": "converse_stream"}}
        self.gateway_arn = gateway_arn
        self.skills = skills or []
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        if client is not None:
            self.client = client
        else:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise HarnessInvocationError("boto3 is required for Harness invocation") from exc
            self.client = boto3.client("bedrock-agentcore", region_name=region)

    def _config(self, context: InvocationContext, role: AgentRole) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        if self.gateway_arn:
            tools.append(
                {
                    "type": "agentcore_gateway",
                    "name": "opsfusion-simulator",
                    "config": {"agentCoreGateway": {"gatewayArn": self.gateway_arn, "outboundAuth": {"awsIam": {}}}},
                }
            )
        return {
            "model": self.model,
            "systemPrompt": [{"text": role.system_prompt()}],
            "tools": tools,
            "allowedTools": ["@opsfusion-simulator"] if tools else list(role.allowed_tools),
            "skills": self.skills,
            "maxIterations": self.max_iterations,
            "maxTokens": self.max_tokens,
            "timeoutSeconds": self.timeout_seconds,
            # AgentCore Memory actor IDs have a restricted character set;
            # use the stable role key rather than the human display name.
            "actorId": context.role,
        }

    def _request_kwargs(self, context: InvocationContext, prompt: str, role: AgentRole) -> dict[str, Any]:
        kwargs = {
            "harnessArn": self.harness_arn,
            "runtimeSessionId": context.session_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "traceId": context.trace_id,
            "runtimeUserId": context.actor_id,
            "actorId": context.actor_id,
        }
        kwargs.update(self._config(context, role))
        return kwargs

    def stream(
        self,
        context: InvocationContext,
        prompt: str,
        *,
        on_event: Callable[[HarnessEvent], None] | None = None,
    ) -> Iterator[HarnessEvent]:
        role = AGENT_ROLES[context.role]
        try:
            response = self.client.invoke_harness(**self._request_kwargs(context, prompt, role))
        except Exception as exc:
            raise HarnessInvocationError(f"InvokeHarness could not start: {exc}") from exc
        stream = response.get("stream") if isinstance(response, dict) else None
        if stream is None:
            raise HarnessInvocationError("InvokeHarness response did not contain a stream")
        sequence = 0
        try:
            for raw_event in stream:
                sequence += 1
                if not isinstance(raw_event, dict) or not raw_event:
                    continue
                event_type, payload = next(iter(raw_event.items()))
                if not isinstance(payload, dict):
                    payload = {"value": payload}
                event = HarnessEvent(
                    event_type=str(event_type),
                    sequence=sequence,
                    context=context,
                    payload=payload,
                    occurred_at=datetime.now(timezone.utc).isoformat(),
                )
                if on_event:
                    on_event(event)
                yield event
        except EventStreamError as exc:
            sequence += 1
            event = HarnessEvent(
                event_type="runtimeClientError",
                sequence=sequence,
                context=context,
                payload={"message": str(exc), "stopReason": "runtime_client_error"},
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
            if on_event:
                on_event(event)
            yield event

    def invoke(
        self,
        context: InvocationContext,
        prompt: str,
        *,
        on_event: Callable[[HarnessEvent], None] | None = None,
    ) -> HarnessInvocationResult:
        role = AGENT_ROLES[context.role]
        events = tuple(self.stream(context, prompt, on_event=on_event))
        text = "".join(event.text_delta for event in events)
        stop_reason = next(
            (
                str(event.payload.get("stopReason"))
                for event in reversed(events)
                if event.event_type == "messageStop" and event.payload.get("stopReason")
            ),
            None,
        )
        tool_uses = tuple(use for event in events if (use := event.tool_use))
        usage = next((dict(event.payload.get("usage", {})) for event in reversed(events) if event.event_type == "metadata"), {})
        metrics = next((dict(event.payload.get("metrics", {})) for event in reversed(events) if event.event_type == "metadata"), {})
        errors = tuple(
            {"event_type": event.event_type, **event.payload}
            for event in events
            if event.event_type in {"runtimeClientError", "internalServerException", "validationException"}
        )
        return HarnessInvocationResult(
            context=context,
            text=text,
            stop_reason=stop_reason,
            events=events,
            tool_uses=tool_uses,
            usage=usage,
            metrics=metrics,
            errors=errors,
            skill_trace={
                "skills": list(role.skills),
                "definitions": list(role.skill_definitions()),
                "status": "applied",
                "applied_via": "Harness system prompt",
                "authority": "reference-only",
                "direct_harness_attachments": list(self.skills),
            },
            prompt_trace={
                "status": "applied",
                "system_prompt": role.system_prompt(),
                "user_prompt": prompt,
                "system_prompt_source": "AGENT_ROLES",
            },
        )
