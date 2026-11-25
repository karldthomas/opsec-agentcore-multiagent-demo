"""Local and Amazon S3 Vectors runbook retrieval.

Retrieved text is reference material only.  Callers must not interpret it as
an authorization decision or as current incident evidence without corroborating
observations from the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    document_id: str
    title: str
    text: str
    source_uri: str
    score: float
    untrusted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "text": self.text,
            "source_uri": self.source_uri,
            "score": self.score,
            "untrusted": self.untrusted,
        }


def _terms(value: str) -> set[str]:
    return {item for item in re.findall(r"[a-z0-9][a-z0-9_-]+", value.casefold()) if len(item) > 2}


class LocalRunbookIndex:
    """Deterministic lexical retrieval used by local tests and stub mode."""

    def __init__(self, documents: Iterable[KnowledgeDocument] = ()) -> None:
        self.documents = tuple(documents)

    @classmethod
    def from_directory(cls, directory: str | Path) -> "LocalRunbookIndex":
        root = Path(directory)
        documents: list[KnowledgeDocument] = []
        for path in sorted(root.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            first_line = next((line.strip("# ") for line in text.splitlines() if line.strip()), path.stem)
            documents.append(KnowledgeDocument(path.stem, first_line, text, str(path), 0.0))
        return cls(documents)

    def search(self, query: str, *, limit: int = 5) -> list[KnowledgeDocument]:
        query_terms = _terms(query)
        ranked: list[KnowledgeDocument] = []
        for document in self.documents:
            haystack = _terms(document.title + " " + document.text)
            score = len(query_terms & haystack) / max(1, len(query_terms))
            if score > 0:
                ranked.append(replace(document, score=round(score, 4)))
        return sorted(ranked, key=lambda item: (-item.score, item.document_id))[:limit]


class S3VectorsRetriever:
    """Thin adapter over the current S3 Vectors text-query API."""

    def __init__(self, index_arn: str, *, client: Any | None = None, region: str = "us-east-1") -> None:
        if not str(index_arn or "").strip():
            raise ValueError("index_arn is required")
        self.index_arn = index_arn
        if client is not None:
            self.client = client
        else:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("boto3 is required for S3 Vectors retrieval") from exc
            self.client = boto3.client("s3vectors", region_name=region)

    def search(self, query: str, *, limit: int = 5) -> list[KnowledgeDocument]:
        if not str(query or "").strip():
            return []
        response = self.client.query_vectors(
            indexArn=self.index_arn,
            queryTexts=[{"text": str(query), "metadataKey": "text"}],
            topK=max(1, min(int(limit), 100)),
            returnMetadata=True,
            returnScores=True,
        )
        documents: list[KnowledgeDocument] = []
        for item in response.get("vectors", []):
            metadata = dict(item.get("metadata") or {})
            document_id = str(metadata.get("document_id") or item.get("key") or "unknown")
            documents.append(
                KnowledgeDocument(
                    document_id=document_id,
                    title=str(metadata.get("title") or document_id),
                    text=str(metadata.get("text") or ""),
                    source_uri=str(metadata.get("source_uri") or "s3://unknown"),
                    score=float(item.get("score", 0.0)),
                    untrusted=True,
                )
            )
        return documents
