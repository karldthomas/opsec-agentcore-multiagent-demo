"""Legacy migration helper for the OpsFusion AgentCore Policy engine.

Normal deployments create and attach the read-only policy through the
``04-agentcore`` CloudFormation stack. This module remains available only for
older environments that need an out-of-band migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any


class PolicyProvisioningError(RuntimeError):
    pass


def _wait(fetch, label: str, timeout: int = 300, interval: int = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = dict(fetch())
        status = str(last.get("status") or "").upper()
        if status in {"ACTIVE", "READY", "AVAILABLE"}:
            return last
        if status in {"FAILED", "CREATE_FAILED", "UPDATE_FAILED"}:
            raise PolicyProvisioningError(f"{label} failed: {last.get('statusReasons') or last}")
        time.sleep(interval)
    raise PolicyProvisioningError(f"timed out waiting for {label}: {last.get('status')}")


class PolicyProvisioner:
    def __init__(self, control: Any, *, timeout: int = 300, interval: int = 5) -> None:
        self.control = control
        self.timeout = timeout
        self.interval = interval

    def ensure_engine(self, *, environment: str) -> dict[str, Any]:
        name = f"opsfusion_{environment}_policy"
        response = self.control.list_policy_engines(maxResults=100)
        existing = next((item for item in response.get("policyEngines", []) if item.get("name") == name), None)
        if existing:
            engine_id = str(existing.get("policyEngineId") or "")
        else:
            created = self.control.create_policy_engine(
                name=name,
                description="OpsFusion read-only synthetic tool authorization engine.",
                clientToken=f"opsfusion-policy-{environment}-v1-0000000000000000",
                tags={"Application": "OpsFusion", "Environment": environment},
            )
            engine_id = str(created.get("policyEngineId") or "")
        if not engine_id:
            raise PolicyProvisioningError("policy engine response did not contain policyEngineId")
        return _wait(
            lambda: self.control.get_policy_engine(policyEngineId=engine_id),
            f"Policy engine {name}",
            self.timeout,
            self.interval,
        )

    @staticmethod
    def read_only_statement(gateway_arn: str, tool_names: list[str]) -> str:
        actions = ",\n".join(
            f'        AgentCore::Action::"opsfusion-simulator-tools___{name}"'
            for name in tool_names
        )
        return (
            "permit(\n"
            "    principal,\n"
            "    action in [\n"
            f"{actions}\n"
            "    ],\n"
            f'    resource == AgentCore::Gateway::"{gateway_arn}"\n'
            ");"
        )

    def ensure_policy(self, *, engine_id: str, gateway_arn: str, tool_names: list[str]) -> dict[str, Any]:
        name = "opsfusion_read_only_tools"
        response = self.control.list_policies(policyEngineId=engine_id, maxResults=100)
        existing = next((item for item in response.get("policies", []) if item.get("name") == name), None)
        if existing:
            policy_id = str(existing.get("policyId") or "")
        else:
            created = self.control.create_policy(
                policyEngineId=engine_id,
                name=name,
                description="Permit only bounded read-only OpsFusion simulator tools.",
                definition={"cedar": {"statement": self.read_only_statement(gateway_arn, tool_names)}},
                validationMode="FAIL_ON_ANY_FINDINGS",
                enforcementMode="ACTIVE",
                clientToken="opsfusion-read-only-tools-v1-0000000000000000",
            )
            policy_id = str(created.get("policyId") or "")
        if not policy_id:
            raise PolicyProvisioningError("policy response did not contain policyId")
        return _wait(
            lambda: self.control.get_policy(policyEngineId=engine_id, policyId=policy_id),
            f"Policy {name}",
            self.timeout,
            self.interval,
        )

    def attach(self, *, gateway_id: str, engine_arn: str, mode: str) -> dict[str, Any]:
        current = self.control.get_gateway(gatewayIdentifier=gateway_id)
        response = self.control.update_gateway(
            gatewayIdentifier=gateway_id,
            name=str(current["name"]),
            description=str(current.get("description") or "OpsFusion bounded synthetic operational tools."),
            roleArn=str(current["roleArn"]),
            protocolType=str(current.get("protocolType") or "MCP"),
            authorizerType=str(current["authorizerType"]),
            policyEngineConfiguration={"arn": engine_arn, "mode": mode},
        )
        updated = dict(response)
        return _wait(
            lambda: self.control.get_gateway(gatewayIdentifier=gateway_id),
            f"Gateway policy attachment {gateway_id}",
            self.timeout,
            self.interval,
        ) | updated

    def provision(self, *, environment: str, gateway_id: str, gateway_arn: str, tool_names: list[str]) -> dict[str, Any]:
        engine = self.ensure_engine(environment=environment)
        engine_id = str(engine["policyEngineId"])
        # The Gateway must be associated before AgentCore can validate a Cedar
        # statement against its generated tool schema.  Start in LOG_ONLY so
        # an empty engine cannot deny traffic during this provisioning window.
        self.attach(gateway_id=gateway_id, engine_arn=str(engine["policyEngineArn"]), mode="LOG_ONLY")
        policy = self.ensure_policy(engine_id=engine_id, gateway_arn=gateway_arn, tool_names=tool_names)
        gateway = self.attach(gateway_id=gateway_id, engine_arn=str(engine["policyEngineArn"]), mode="ENFORCE")
        return {
            "CF_OUT_PolicyEngineId": engine_id,
            "CF_OUT_PolicyEngineArn": str(engine["policyEngineArn"]),
            "CF_OUT_PolicyId": str(policy["policyId"]),
            "CF_OUT_GatewayPolicyMode": str(
                ((gateway.get("policyEngineConfiguration") or {}).get("mode") or "ENFORCE")
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--context-file", type=Path, required=True)
    args = parser.parse_args()
    import boto3

    context = json.loads(args.context_file.read_text(encoding="utf-8"))
    session = boto3.Session(profile_name=args.profile or None, region_name=args.region)
    from gateway.tool_catalog import ToolCatalog

    values = PolicyProvisioner(session.client("bedrock-agentcore-control")).provision(
        environment=args.environment,
        gateway_id=str(context["CF_OUT_GatewayId"]),
        gateway_arn=str(context["CF_OUT_GatewayArn"]),
        tool_names=[spec.name for spec in ToolCatalog().specs() if spec.read_only],
    )
    context.update(values)
    args.context_file.write_text(json.dumps(dict(sorted(context.items())), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(values, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
