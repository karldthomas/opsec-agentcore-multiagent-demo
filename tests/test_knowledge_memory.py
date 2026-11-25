from __future__ import annotations

from pathlib import Path

from knowledge import LocalRunbookIndex, S3VectorsRetriever
from memory import AgentCoreMemoryAdapter, LocalMemoryStore


def test_local_runbook_retrieval_marks_content_untrusted() -> None:
    index = LocalRunbookIndex.from_directory(Path(__file__).parents[1] / "knowledge" / "runbooks")
    results = index.search("rollback operator approval", limit=3)
    assert results
    assert all(item.untrusted for item in results)
    assert any(item.document_id == "adversarial-instructions" for item in LocalRunbookIndex.from_directory(Path(__file__).parents[1] / "knowledge" / "runbooks").search("policy approval", limit=10))


class FakeVectors:
    def query_vectors(self, **kwargs):
        assert kwargs["queryTexts"] == [{"text": "database saturation", "metadataKey": "text"}]
        return {"vectors": [{"key": "runbook-1", "score": 0.92, "metadata": {"title": "DB", "text": "check connections", "source_uri": "s3://runbooks/db"}}]}


def test_s3_vectors_adapter_uses_text_queries() -> None:
    result = S3VectorsRetriever("arn:aws:s3vectors:us-east-1:1:index/x", client=FakeVectors()).search("database saturation")
    assert result[0].document_id == "runbook-1"
    assert result[0].untrusted is True


class FakeMemory:
    def create_event(self, **kwargs):
        return {"event": {"memoryId": kwargs["memoryId"], "metadata": kwargs["metadata"]}}

    def retrieve_memory_records(self, **kwargs):
        return {"memoryRecordSummaries": [{"text": kwargs["searchCriteria"]["searchQuery"]}]}


def test_memory_adapters_separate_verified_history_from_current_evidence() -> None:
    local = LocalMemoryStore()
    entry = local.save_summary(run_id="run-1", tenant_id="tenant", text="verified rollback recovered the queue", verified=True)
    assert local.search("queue", tenant_id="tenant")[0] == entry
    remote = AgentCoreMemoryAdapter("memory-1", client=FakeMemory())
    event = remote.save_summary(run_id="run-1", tenant_id="tenant", actor_id="operator", text="verified summary")
    assert event["event"]["metadata"]["tenant_id"] == "tenant"
    assert remote.search("queue", namespace="tenant")[0]["text"] == "queue"
