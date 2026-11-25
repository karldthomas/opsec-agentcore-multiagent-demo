"""AgentCore Memory adapter with a deterministic local implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    entry_id: str
    run_id: str
    tenant_id: str
    text: str
    kind: str
    verified: bool
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "text": self.text,
            "kind": self.kind,
            "verified": self.verified,
            "created_at": self.created_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class LocalMemoryStore:
    def __init__(self) -> None:
        self.entries: list[MemoryEntry] = []

    def save_summary(
        self,
        *,
        run_id: str,
        tenant_id: str,
        text: str,
        kind: str = "investigation_summary",
        verified: bool = False,
    ) -> MemoryEntry:
        entry = MemoryEntry(f"mem-{uuid4().hex}", run_id, tenant_id, str(text), kind, bool(verified), _now())
        self.entries.append(entry)
        return entry

    def search(self, query: str, *, tenant_id: str, limit: int = 5) -> list[MemoryEntry]:
        terms = {item for item in re.findall(r"[a-z0-9_-]+", str(query).casefold()) if len(item) > 2}
        ranked: list[tuple[int, MemoryEntry]] = []
        for entry in self.entries:
            if entry.tenant_id != tenant_id:
                continue
            score = len(terms & set(re.findall(r"[a-z0-9_-]+", entry.text.casefold())))
            if score:
                ranked.append((score, entry))
        return [item for _, item in sorted(ranked, key=lambda value: (-value[0], value[1].entry_id))[:limit]]


class AgentCoreMemoryAdapter:
    """Persist summaries through AgentCore Memory without making them facts.

    Only verified summaries should be written by the application.  Retrieval
    is returned as historical context and must be checked against current
    simulator observations before being used in a report.
    """

    def __init__(self, memory_id: str, *, client: Any | None = None, region: str = "us-east-1") -> None:
        if not str(memory_id or "").strip():
            raise ValueError("memory_id is required")
        self.memory_id = memory_id
        if client is not None:
            self.client = client
        else:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("boto3 is required for AgentCore Memory") from exc
            self.client = boto3.client("bedrock-agentcore", region_name=region)

    def save_summary(
        self,
        *,
        run_id: str,
        tenant_id: str,
        actor_id: str,
        text: str,
        session_id: str | None = None,
        verified: bool = True,
    ) -> dict[str, Any]:
        if not verified:
            raise ValueError("only verified summaries may be persisted as historical memory")
        return dict(
            self.client.create_event(
                memoryId=self.memory_id,
                actorId=actor_id,
                sessionId=session_id or f"opsfusion-memory-{run_id}",
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"conversational": {"content": {"text": str(text)}, "role": "ASSISTANT"}}],
                metadata={"tenant_id": tenant_id, "run_id": run_id, "kind": "verified_investigation_summary"},
                clientToken=f"opsfusion-{run_id}-{uuid4().hex}",
            )
        )

    def search(self, query: str, *, namespace: str, limit: int = 5) -> list[dict[str, Any]]:
        response = self.client.retrieve_memory_records(
            memoryId=self.memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": str(query), "topK": max(1, min(int(limit), 20))},
        )
        return [dict(item) for item in response.get("memoryRecordSummaries", [])]
