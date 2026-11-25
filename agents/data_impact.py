"""Database, queue, order, and customer-impact specialist."""

from .session import AgentSession


class DataImpactAgent(AgentSession):
    def __init__(self, client) -> None:
        super().__init__(client, "data-agent")
