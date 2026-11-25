from __future__ import annotations

from agents.harness import AgentCoreHarnessClient, InvocationContext


class FakeHarness:
    def __init__(self) -> None:
        self.calls = []

    def invoke_harness(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "stream": iter(
                [
                    {"messageStart": {"role": "assistant"}},
                    {"contentBlockDelta": {"delta": {"text": "Finding: observed evidence."}}},
                    {"messageStop": {"stopReason": "end_turn"}},
                ]
            )
        }


def test_harness_adapter_delegates_loop_and_normalizes_events() -> None:
    fake = FakeHarness()
    client = AgentCoreHarnessClient("arn:aws:bedrock-agentcore:us-east-1:123:harness/demo", client=fake)
    context = InvocationContext.create(
        "run-harness",
        "telemetry-agent",
        tenant_id="acme-retail",
        actor_id="actor-1",
    )
    result = client.invoke(context, "Investigate the incident.")
    assert result.succeeded
    assert result.text == "Finding: observed evidence."
    assert result.stop_reason == "end_turn"
    assert len(result.events) == 3
    assert fake.calls[0]["harnessArn"].endswith("harness/demo")
    assert "model" in fake.calls[0]
    assert "systemPrompt" in fake.calls[0]
    assert result.to_dict()["skill_trace"] == {
        "skills": ["telemetry-triage"],
        "definitions": [{
            "name": "telemetry-triage",
            "instructions": "Correlate time-aligned service health, metrics, logs, traces, and alarms; distinguish symptoms from resource pressure; and cite the observations that establish timing and severity.",
        }],
        "status": "applied",
        "applied_via": "Harness system prompt",
        "authority": "reference-only",
        "direct_harness_attachments": [],
    }
    assert result.to_dict()["prompt_trace"]["system_prompt_source"] == "AGENT_ROLES"
    assert "Reference skill instructions" in result.to_dict()["prompt_trace"]["system_prompt"]
    assert result.to_dict()["prompt_trace"]["user_prompt"] == "Investigate the incident."


def test_context_is_bound_to_role_and_tenant() -> None:
    context = InvocationContext.create(
        "run-harness",
        "change-agent",
        tenant_id="acme-retail",
        actor_id="actor-1",
        session_id="caller-provided-session-id-that-is-long-enough-123456789",
    )
    assert context.session_id.startswith("opsfusion-v1-")
    assert context.role == "change-agent"
