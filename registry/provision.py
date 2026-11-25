"""Provision the OpsFusion AWS Agent Registry without auto-approving records."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .client import RegistryClient
from .manifest import opsfusion_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "NoTurningBack"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT_NAME", "dev"))
    parser.add_argument("--registry-name", default=None)
    parser.add_argument("--records", action="store_true", help="create the declared records as DRAFT")
    parser.add_argument("--submit", action="store_true", help="submit newly created records for approval")
    parser.add_argument("--approve", action="store_true", help="explicitly approve the declared records")
    parser.add_argument("--context-file", type=Path, help="persist Registry IDs and bind the application Lambda")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    name = args.registry_name or f"opsfusion-{args.environment}"
    client = RegistryClient(region=args.region, profile=args.profile)
    registry = client.ensure_registry(
        name=name,
        description="OpsFusion approved catalog of synthetic incident agents, tools, and skills.",
        tags={"Application": "OpsFusion", "Environment": args.environment, "ManagedBy": "opsfusion"},
    )
    registry = client.wait_for_registry(str(registry["registryId"]))
    output: dict[str, object] = {"registry": registry, "records": []}
    if args.records:
        records = []
        for definition in opsfusion_records(args.environment):
            record = client.ensure_record(str(registry["registryId"]), definition)
            record_status = str(record.get("status") or "").upper()
            if (args.submit or args.approve) and record_status == "DRAFT" and record.get("recordId"):
                client.submit_for_approval(str(registry["registryId"]), str(record["recordId"]))
                record = client.wait_for_record_status(
                    str(registry["registryId"]),
                    str(record.get("name") or definition.name),
                    {"PENDING_APPROVAL", "APPROVED"},
                )
            if args.approve and str(record.get("status") or "").upper() != "APPROVED" and record.get("recordId"):
                client.set_status(str(registry["registryId"]), str(record["recordId"]), "APPROVED", "Explicit OpsFusion deployment approval")
            if (args.submit or args.approve) and record.get("name"):
                record = client.find_record(str(registry["registryId"]), str(record["name"])) or record
            records.append(record)
        output["records"] = records
    if args.context_file:
        context = json.loads(args.context_file.read_text(encoding="utf-8")) if args.context_file.exists() else {}
        context.update({
            "CF_OUT_RegistryId": str(registry["registryId"]),
            "CF_OUT_RegistryArn": str(registry.get("registryArn") or ""),
        })
        application_arn = str(context.get("CF_OUT_ApplicationFunctionArn") or "").strip()
        if application_arn:
            import boto3

            session = boto3.Session(profile_name=args.profile or None, region_name=args.region)
            lambda_client = session.client("lambda")
            configuration = lambda_client.get_function_configuration(FunctionName=application_arn)
            variables = dict((configuration.get("Environment") or {}).get("Variables") or {})
            variables["OPSFUSION_REGISTRY_ID"] = str(registry["registryId"])
            lambda_client.update_function_configuration(
                FunctionName=application_arn,
                Environment={"Variables": variables},
            )
        args.context_file.write_text(json.dumps(dict(sorted(context.items())), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
