"""Provision an S3 Vectors runbook index and ingest synthetic runbooks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .retrieval import LocalRunbookIndex


ROOT = Path(__file__).resolve().parents[1]


def _embedding(text: str, dimension: int = 32) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round((digest[index % len(digest)] / 255.0) * 2 - 1, 6) for index in range(dimension)]


class S3VectorsProvisioner:
    def __init__(self, client: Any, *, region: str) -> None:
        self.client = client
        self.region = region

    def ensure_index(self, *, environment: str, documents_dir: Path) -> dict[str, str]:
        bucket_name = f"opsfusion-{environment}-runbooks"
        buckets = self.client.list_vector_buckets(maxResults=100).get("vectorBuckets", [])
        bucket = next((item for item in buckets if item.get("vectorBucketName") == bucket_name), None)
        if bucket is None:
            response = self.client.create_vector_bucket(
                vectorBucketName=bucket_name,
                tags={"Application": "OpsFusion", "Environment": environment},
            )
            bucket_arn = str(response["vectorBucketArn"])
        else:
            bucket_arn = str(bucket.get("vectorBucketArn") or "")
        index_name = "opsfusion-runbooks-v1"
        indexes = self.client.list_indexes(vectorBucketName=bucket_name, maxResults=100).get("indexes", [])
        index = next((item for item in indexes if item.get("indexName") == index_name), None)
        if index is None:
            response = self.client.create_index(
                vectorBucketName=bucket_name,
                indexName=index_name,
                dataType="float32",
                dimension=32,
                distanceMetric="cosine",
                metadataConfiguration={"textSearchConfigurations": [{"metadataKey": "text", "language": "en"}]},
                tags={"Application": "OpsFusion", "Environment": environment},
            )
            index_arn = str(response["indexArn"])
        else:
            index_arn = str(index.get("indexArn") or "")
        local = LocalRunbookIndex.from_directory(documents_dir)
        vectors = [
            {
                "key": item.document_id,
                "data": {"float32": _embedding(item.text)},
                "metadata": {
                    "document_id": item.document_id,
                    "title": item.title,
                    "text": item.text,
                    "source_uri": item.source_uri,
                },
            }
            for item in local.documents
        ]
        if vectors:
            self.client.put_vectors(indexArn=index_arn, vectors=vectors)
        return {"vectorBucketName": bucket_name, "vectorBucketArn": bucket_arn, "indexName": index_name, "indexArn": index_arn}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--documents", type=Path, default=ROOT / "knowledge" / "runbooks")
    parser.add_argument("--context-file", type=Path)
    args = parser.parse_args()
    import boto3

    client = boto3.Session(profile_name=args.profile or None, region_name=args.region).client("s3vectors")
    values = S3VectorsProvisioner(client, region=args.region).ensure_index(environment=args.environment, documents_dir=args.documents)
    if args.context_file:
        payload = json.loads(args.context_file.read_text(encoding="utf-8")) if args.context_file.exists() else {}
        payload.update({f"CF_OUT_{key}": value for key, value in values.items()})
        args.context_file.write_text(json.dumps(dict(sorted(payload.items())), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(values, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
