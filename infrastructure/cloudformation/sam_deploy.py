"""One-stack CloudFormation/SAM adapter used by the manifest runner."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
import re
import tempfile


def _is_sam(path: Path) -> bool:
    return "Transform:" in path.read_text(encoding="utf-8")


def deploy(
    template: Path,
    *,
    stack_name: str,
    region: str,
    parameters: dict[str, str],
    resolve_s3: bool = False,
    profile: str | None = None,
) -> None:
    env = os.environ.copy()
    env.update({"AWS_REGION": region, "AWS_DEFAULT_REGION": region})
    if profile:
        env["AWS_PROFILE"] = profile
    overrides = [f"{key}={value}" for key, value in sorted(parameters.items())]
    root = template.parents[3]
    deploy_template = template
    if _is_sam(template):
        build_root = Path(tempfile.gettempdir()) / "opsfusion-sam"
        build_dir = build_root / re.sub(r"[^A-Za-z0-9_.-]", "-", stack_name)
        cache_dir = build_root / "cache"
        build_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        build_command = [
            "sam", "build", "--template-file", str(template), "--build-dir", str(build_dir),
            "--cache-dir", str(cache_dir), "--cached",
        ]
        print("+ " + " ".join(build_command))
        subprocess.run(build_command, env=env, cwd=root, check=True)
        deploy_template = build_dir / "template.yaml"
        if not deploy_template.is_file():
            raise RuntimeError(f"SAM build did not produce {deploy_template}")
        command = [
            "sam", "deploy", "--template-file", str(deploy_template), "--stack-name", stack_name,
            "--region", region, "--capabilities", "CAPABILITY_IAM", "CAPABILITY_NAMED_IAM",
            "--no-confirm-changeset", "--no-fail-on-empty-changeset", "--parameter-overrides", *overrides,
        ]
        if resolve_s3:
            command.append("--resolve-s3")
    else:
        command = [
            "aws", "cloudformation", "deploy", "--template-file", str(template), "--stack-name", stack_name,
            "--region", region, "--capabilities", "CAPABILITY_IAM", "CAPABILITY_NAMED_IAM",
            "--no-fail-on-empty-changeset", "--parameter-overrides", *overrides,
        ]
    print("+ " + " ".join(command))
    subprocess.run(command, env=env, cwd=root, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile")
    parser.add_argument("--resolve-s3", action="store_true")
    parser.add_argument("--parameter", action="append", default=[])
    args = parser.parse_args()
    parameters = dict(item.split("=", 1) for item in args.parameter)
    deploy(args.template, stack_name=args.stack_name, region=args.region, parameters=parameters, resolve_s3=args.resolve_s3, profile=args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
