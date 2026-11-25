"""Deployment and configuration-change specialist."""

from .session import AgentSession


class ChangeAnalysisAgent(AgentSession):
    def __init__(self, client) -> None:
        super().__init__(client, "change-agent")
