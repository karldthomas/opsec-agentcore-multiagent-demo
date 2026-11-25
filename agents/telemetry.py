"""Telemetry specialist backed by the role-scoped Harness session."""

from .session import AgentSession


class TelemetryAgent(AgentSession):
    def __init__(self, client) -> None:
        super().__init__(client, "telemetry-agent")
