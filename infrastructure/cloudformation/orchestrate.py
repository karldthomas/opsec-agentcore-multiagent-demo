"""Manifest-driven OpsFusion deployment runner."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from typing import Any

from .manifest import Manifest, load_manifest
from .sam_deploy import deploy


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("deployment.yaml")
BUILD = Path(__file__).with_name("build")


def _run(command: list[str], *, env: dict[str, str], cwd: Path = ROOT) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _aws_client(region: str, profile: str | None):
    import boto3

    return boto3.Session(profile_name=profile, region_name=region).client("cloudformation")


def _outputs(client: Any, stack_name: str) -> dict[str, str]:
    response = client.describe_stacks(StackName=stack_name)
    stack = response["Stacks"][0]
    return {str(item["OutputKey"]): str(item["OutputValue"]) for item in stack.get("Outputs", []) if item.get("OutputValue")}


def _save_context(path: Path, context: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(context.items())), indent=2) + "\n", encoding="utf-8")


def _load_context(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _publish_artifacts(context: dict[str, Any], *, environment: str, release_id: str, region: str, profile: str | None, publish_web: bool) -> None:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=region)
    s3 = session.client("s3")
    runbook_bucket = str(context.get("CF_OUT_RunbookBucketName") or "").strip()
    if runbook_bucket:
        for path in sorted((ROOT / "knowledge" / "runbooks").glob("*.md")):
            s3.upload_file(str(path), runbook_bucket, f"runbooks/v1/{path.name}", ExtraArgs={"ContentType": "text/markdown"})
        context["RunbookReleaseId"] = release_id
    if publish_web:
        web_bucket = str(context.get("CF_OUT_WebArtifactBucketName") or "").strip()
        if not web_bucket:
            raise RuntimeError("07-web did not provide WebArtifactBucketName")
        dist = ROOT / "apps" / "web" / "dist"
        for path in sorted(item for item in dist.rglob("*") if item.is_file()):
            key = path.relative_to(dist).as_posix()
            content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(path.suffix, "application/octet-stream")
            s3.upload_file(str(path), web_bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": "no-cache" if key == "index.html" else "max-age=31536000"})
        s3.put_object(Bucket=web_bucket, Key="release.json", Body=(json.dumps({"release_id": release_id, "environment": environment}, sort_keys=True) + "\n").encode(), ContentType="application/json")
        context["WebArtifactUri"] = f"s3://{web_bucket}/"


def _handlers(args: argparse.Namespace, manifest: Manifest, selected: tuple[str, ...], env: dict[str, str]) -> None:
    before = {stack for step in manifest.steps if step.handler in {"run_tests", "validate_templates", "check_aws_identity", "build_web"} for stack in step.before}
    if not args.skip_tests and before.intersection(selected):
        _run([sys.executable, "-m", "pytest", "-q"], env=env)
    if not args.skip_web and "07-web" in selected:
        _run(["npm", "run", "build"], env=env, cwd=ROOT / "apps" / "web")
    for stack_id in selected:
        _run(["sam", "validate", "--template-file", str(manifest.stack(stack_id).template)], env=env)
    if not args.plan:
        _run(["aws", "sts", "get-caller-identity", "--region", args.region], env=env)


def deploy_all(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_manifest(args.manifest)
    selected = manifest.selected(args.stack, args.start_stack)
    env = os.environ.copy()
    env.update({"AWS_REGION": args.region, "AWS_DEFAULT_REGION": args.region})
    if args.profile:
        env["AWS_PROFILE"] = args.profile
    _handlers(args, manifest, selected, env)
    context_path = Path(args.context_file) if args.context_file else BUILD / f"deployment-context-{args.environment}.json"
    if not context_path.is_absolute():
        context_path = ROOT / context_path
    context = _load_context(context_path)
    context.update({"EnvironmentName": args.environment, "ReleaseId": args.release_id, "DeploymentContractVersion": "opsfusion.v1"})
    if args.plan:
        print(json.dumps({"environment": args.environment, "stacks": list(selected), "context": str(context_path)}, indent=2))
        return context
    cf = _aws_client(args.region, args.profile)
    for stack_id in selected:
        definition = manifest.stack(stack_id)
        stack_name = manifest.stack_name(args.environment, stack_id)
        parameters = {
            "EnvironmentName": args.environment,
            "DeploymentContractVersion": "opsfusion.v1",
            "ReleaseId": args.release_id,
        }
        # These are useful to templates and make dependency hydration explicit
        # even when a particular stack only consumes a subset of them.
        for dependency in manifest.dependencies(stack_id):
            dependency_name = manifest.stack_name(args.environment, dependency)
            outputs = _outputs(cf, dependency_name)
            for key, value in outputs.items():
                context[f"CF_OUT_{key}"] = value
        parameter_sources: dict[str, tuple[str, ...]] = {}
        if stack_id == "04-agentcore":
            parameter_sources = {
                "GatewayExecutionRoleArn": ("CF_OUT_GatewayExecutionRoleArn",),
                "HarnessExecutionRoleArn": ("CF_OUT_HarnessExecutionRoleArn",),
                "GatewayTargetFunctionArn": ("CF_OUT_GatewayTargetFunctionArn",),
                "PolicyEngineArn": ("CF_OUT_PolicyEngineArn",),
            }
        elif stack_id == "06-application":
            parameter_sources = {
                "HarnessArn": ("CF_OUT_HarnessArn",),
                "GatewayArn": ("CF_OUT_GatewayArn",),
                "MemoryArn": ("CF_OUT_MemoryArn", "CF_OUT_HarnessMemoryArn"),
                "RegistryId": ("CF_OUT_RegistryId",),
            }
        for parameter_name, context_keys in parameter_sources.items():
            value = next((str(context.get(key) or "").strip() for key in context_keys if str(context.get(key) or "").strip()), "")
            if value:
                parameters[parameter_name] = value
        deploy(definition.template, stack_name=stack_name, region=args.region, parameters=parameters, resolve_s3=args.resolve_s3, profile=args.profile)
        for key, value in _outputs(cf, stack_name).items():
            context[f"CF_OUT_{key}"] = value
        if stack_id == "04-agentcore" and "PolicyEngineArn" not in parameters:
            policy_engine_arn = str(context.get("CF_OUT_PolicyEngineArn") or "").strip()
            if not policy_engine_arn:
                raise RuntimeError("04-agentcore did not provide PolicyEngineArn for the Gateway policy attachment")
            policy_parameters = dict(parameters)
            policy_parameters["PolicyEngineArn"] = policy_engine_arn
            deploy(
                definition.template,
                stack_name=stack_name,
                region=args.region,
                parameters=policy_parameters,
                resolve_s3=args.resolve_s3,
                profile=args.profile,
            )
            for key, value in _outputs(cf, stack_name).items():
                context[f"CF_OUT_{key}"] = value
        context[f"STACK_{stack_id}_NAME"] = stack_name
        _save_context(context_path, context)
    if not args.skip_artifacts and not args.plan:
        _publish_artifacts(
            context,
            environment=args.environment,
            release_id=args.release_id,
            region=args.region,
            profile=args.profile,
            publish_web=("07-web" in selected),
        )
        _save_context(context_path, context)
    print(f"Deployment context: {context_path}")
    if args.reconcile_registry:
        registry_command = [
            sys.executable,
            "-m",
            "registry.provision",
            "--profile",
            args.profile or "",
            "--region",
            args.region,
            "--environment",
            args.environment,
            "--records",
            "--context-file",
            str(context_path),
        ]
        if args.submit_registry:
            registry_command.append("--submit")
        if args.approve_registry:
            registry_command.append("--approve")
        _run(registry_command, env=env)
    return context


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--release-id", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    parser.add_argument("--context-file")
    parser.add_argument("--stack")
    parser.add_argument("--from", dest="start_stack")
    parser.add_argument("--resolve-s3", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-artifacts", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--reconcile-registry", action="store_true")
    parser.add_argument("--submit-registry", action="store_true")
    parser.add_argument("--approve-registry", action="store_true")
    args = parser.parse_args()
    deploy_all(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
