"""Legacy migration helper for pre-CloudFormation AgentCore resources.

Normal deployments use ``infrastructure/cloudformation/stacks/04-agentcore.yaml``
and do not call this module. It remains available only for operators migrating
an older environment whose AgentCore resources were created out of band.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from gateway.tool_catalog import ToolCatalog


ROOT = Path(__file__).resolve().parents[1]


class AgentCoreProvisioningError(RuntimeError):
    pass


def _token(*parts: str) -> str:
    return "opsfusion-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _wait(fetch, label: str, timeout: int = 600, interval: int = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = dict(fetch())
        status = str(last.get("status") or "").upper()
        if status in {"READY", "ACTIVE", "AVAILABLE"}:
            return last
        if status in {"FAILED", "CREATE_FAILED", "UPDATE_FAILED"}:
            raise AgentCoreProvisioningError(f"{label} failed: {last.get('statusReasons') or last}")
        time.sleep(interval)
    raise AgentCoreProvisioningError(f"timed out waiting for {label}: {last.get('status')}")


def _context(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise AgentCoreProvisioningError(f"invalid deployment context: {path}") from exc
    if not isinstance(payload, dict):
        raise AgentCoreProvisioningError("deployment context must be an object")
    return payload


def _required(context: dict[str, Any], key: str) -> str:
    value = str(context.get(key) or "").strip()
    if not value:
        raise AgentCoreProvisioningError(f"deployment context is missing {key}")
    return value


_TOOL_ARGUMENTS: dict[str, dict[str, dict[str, Any]]] = {
    "query_cloudwatch_metrics": {
        "service": {"type": "string", "description": "Service name, usually orders-service."},
        "metric": {"type": "string", "description": "Metric name such as api_error_rate or database_connections."},
    },
    "search_application_logs": {
        "query": {"type": "string", "description": "Optional bounded text query such as timeout or retry."},
    },
    "compare_metric_windows": {
        "metric": {"type": "string", "description": "Metric to compare between baseline and incident windows."},
    },
    "get_deployment_details": {
        "deployment_id": {"type": "string", "description": "Exact deployment ID returned by list_recent_deployments."},
    },
    "search_runbooks": {
        "query": {"type": "string", "description": "Search terms for current troubleshooting guidance."},
    },
    "query_order_records": {
        "status": {"type": "string", "description": "Optional order status filter."},
    },
}


def _tool_definitions() -> list[dict[str, Any]]:
    """Build explicit Lambda-target schemas, including run and role scope."""

    common = {
        "run_id": {"type": "string", "description": "Current synthetic scenario run ID."},
        "tenant_id": {"type": "string", "description": "Authorized synthetic tenant ID."},
        "actor_id": {"type": "string", "description": "Operator or agent actor ID for this investigation."},
        "role": {
            "type": "string",
            "description": "Exact read-only role key: telemetry-agent, change-agent, data-agent, or orchestrator.",
        },
    }
    definitions: list[dict[str, Any]] = []
    for spec in ToolCatalog().specs():
        if not spec.read_only:
            continue
        properties = {**common, **_TOOL_ARGUMENTS.get(spec.name, {})}
        required = ["run_id", "tenant_id", "actor_id", "role"]
        if spec.name == "get_deployment_details":
            required.append("deployment_id")
        definitions.append({
            "name": spec.name,
            "description": f"{spec.description} Include the required run, tenant, actor, and role fields.",
            "inputSchema": {
                "type": "object",
                "description": "Run-scoped OpsFusion tool request.",
                "properties": properties,
                "required": required,
            },
        })
    return definitions


class AgentCoreProvisioner:
    def __init__(self, control: Any, *, region: str, lambda_client: Any | None = None, timeout: int = 600, interval: int = 5) -> None:
        self.control = control
        self.region = region
        self.lambda_client = lambda_client
        self.timeout = timeout
        self.interval = interval

    def gateway(self, *, environment: str, role_arn: str) -> dict[str, Any]:
        name = f"opsfusion-{environment}-gateway"
        response = self.control.list_gateways(maxResults=100)
        existing = next((item for item in response.get("items", []) if item.get("name") == name), None)
        if existing:
            gateway_id = str(existing.get("gatewayId") or "")
            return _wait(lambda: self.control.get_gateway(gatewayIdentifier=gateway_id), f"Gateway {name}", self.timeout, self.interval)
        created = self.control.create_gateway(
            name=name,
            description="OpsFusion bounded synthetic operational tools.",
            clientToken=_token("gateway", environment),
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
        )
        gateway_id = str(created.get("gatewayId") or "")
        if not gateway_id:
            raise AgentCoreProvisioningError("Gateway creation returned no gatewayId")
        return _wait(lambda: self.control.get_gateway(gatewayIdentifier=gateway_id), f"Gateway {name}", self.timeout, self.interval)

    def gateway_target(self, gateway: dict[str, Any], *, environment: str, lambda_arn: str) -> dict[str, Any]:
        gateway_id = _required(gateway, "gatewayId")
        name = "opsfusion-simulator-tools"
        current = self.control.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)
        existing = next((item for item in current.get("items", []) if item.get("name") == name), None)
        configuration = {"mcp": {"lambda": {"lambdaArn": lambda_arn, "toolSchema": {"inlinePayload": _tool_definitions()}}}}
        credentials = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
        if existing:
            target_id = _required(existing, "targetId")
            self.control.update_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target_id,
                name=name,
                description="OpsFusion synthetic simulator Gateway target.",
                targetConfiguration=configuration,
                credentialProviderConfigurations=credentials,
            )
        else:
            created = self.control.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=name,
                description="OpsFusion synthetic simulator Gateway target.",
                clientToken=_token("target", environment),
                targetConfiguration=configuration,
                credentialProviderConfigurations=credentials,
            )
            target_id = _required(created, "targetId")
        return _wait(
            lambda: self.control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
            f"Gateway target {name}",
            self.timeout,
            self.interval,
        )

    def memory(self, *, environment: str) -> dict[str, Any] | None:
        name = f"opsfusion_{environment}_memory"
        response = self.control.list_memories(maxResults=100)
        existing = next(
            (
                item
                for item in response.get("memories", [])
                if item.get("name") == name
                or item.get("id") == name
                or str(item.get("id") or "").startswith(f"{name}-")
            ),
            None,
        )
        if existing:
            return dict(existing)
        created = self.control.create_memory(
            name=name,
            description="Verified OpsFusion investigation summaries; historical context only.",
            eventExpiryDuration=30,
            clientToken=_token("memory", environment),
            tags={"Application": "OpsFusion", "Environment": environment},
        )
        return dict(created.get("memory") or created)

    def harness(
        self,
        *,
        environment: str,
        role_arn: str,
        gateway_arn: str,
        memory: dict[str, Any] | None,
        model_id: str,
    ) -> dict[str, Any]:
        name = f"opsfusion_{environment}_harness"
        response = self.control.list_harnesses(maxResults=100)
        existing = next((item for item in response.get("harnesses", []) if item.get("harnessName") == name), None)
        if existing:
            harness_id = _required(existing, "harnessId")
            def fetch_existing() -> dict[str, Any]:
                response = self.control.get_harness(harnessId=harness_id)
                return dict(response.get("harness") or response)

            return _wait(fetch_existing, f"Harness {name}", self.timeout, self.interval)
        request: dict[str, Any] = {
            "harnessName": name,
            "clientToken": _token("harness", environment),
            "executionRoleArn": role_arn,
            "model": {"bedrockModelConfig": {"modelId": model_id, "apiFormat": "converse_stream"}},
            "systemPrompt": [{"text": "You are an OpsFusion specialist operating only on synthetic evidence in a read-only analysis run. Cite evidence IDs, distinguish facts from hypotheses, and never mutate simulator state."}],
            "tools": [{"type": "agentcore_gateway", "name": "opsfusion-simulator", "config": {"agentCoreGateway": {"gatewayArn": gateway_arn, "outboundAuth": {"awsIam": {}}}}}],
            "allowedTools": ["@opsfusion-simulator"],
            "maxIterations": 16,
            "maxTokens": 4096,
            "timeoutSeconds": 180,
            "tags": {"Application": "OpsFusion", "Environment": environment},
        }
        # Harness creates/owns the managed memory backing resource.  The
        # service populates its ARN on GetHarness; CreateHarness accepts only
        # the strategy and retention settings here.
        request["memory"] = {
            "managedMemoryConfiguration": {
                "strategies": ["SUMMARIZATION", "EPISODIC"],
                "eventExpiryDuration": 30,
            }
        }
        created = self.control.create_harness(**request)
        harness = created.get("harness") or created
        harness_id = _required(harness, "harnessId")
        def fetch_created() -> dict[str, Any]:
            response = self.control.get_harness(harnessId=harness_id)
            return dict(response.get("harness") or response)

        return _wait(fetch_created, f"Harness {name}", self.timeout, self.interval)

    def provision(self, *, environment: str, context: dict[str, Any], model_id: str) -> dict[str, Any]:
        gateway = self.gateway(environment=environment, role_arn=_required(context, "CF_OUT_GatewayExecutionRoleArn"))
        target = self.gateway_target(gateway, environment=environment, lambda_arn=_required(context, "CF_OUT_GatewayTargetFunctionArn"))
        memory = self.memory(environment=environment)
        harness = self.harness(
            environment=environment,
            role_arn=_required(context, "CF_OUT_HarnessExecutionRoleArn"),
            gateway_arn=_required(gateway, "gatewayArn"),
            memory=memory,
            model_id=model_id,
        )
        managed_memory = dict(
            ((harness.get("memory") or {}).get("managedMemoryConfiguration") or {})
        )
        runtime = dict(((harness.get("environment") or {}).get("agentCoreRuntimeEnvironment") or {}))
        workload_identities = self.control.list_workload_identities(maxResults=20).get("workloadIdentities", [])
        gateway_identity = next(
            (item for item in workload_identities if str(item.get("name") or "") == str(gateway.get("gatewayId") or "")),
            {},
        )
        runtime_identity = next(
            (
                item
                for item in workload_identities
                if str(item.get("name") or "") == str(runtime.get("agentRuntimeName") or "")
                or str(item.get("name") or "").startswith(str(runtime.get("agentRuntimeName") or "") + "-")
            ),
            {},
        )
        memory_id = str(
            managed_memory.get("arn")
            or (memory or {}).get("id")
            or (memory or {}).get("memoryId")
            or ""
        )
        values = {
            "CF_OUT_GatewayId": _required(gateway, "gatewayId"),
            "CF_OUT_GatewayArn": _required(gateway, "gatewayArn"),
            "CF_OUT_GatewayTargetId": _required(target, "targetId"),
            "CF_OUT_HarnessId": _required(harness, "harnessId"),
            "CF_OUT_HarnessArn": _required(harness, "arn"),
            "CF_OUT_MemoryId": memory_id,
            "CF_OUT_HarnessMemoryArn": str(managed_memory.get("arn") or ""),
            "CF_OUT_HarnessRuntimeArn": str(runtime.get("agentRuntimeArn") or ""),
            "CF_OUT_GatewayWorkloadIdentityArn": str(gateway_identity.get("workloadIdentityArn") or ""),
            "CF_OUT_HarnessWorkloadIdentityArn": str(runtime_identity.get("workloadIdentityArn") or ""),
        }
        application_arn = str(context.get("CF_OUT_ApplicationFunctionArn") or "").strip()
        if application_arn and self.lambda_client is not None:
            self.bind_application(application_arn, values)
        return values

    def bind_application(self, function_arn: str, values: dict[str, Any]) -> None:
        """Expose provisioned control-plane identifiers to the API Lambda.

        The Lambda still owns workflow state and analysis checkpoints. These
        environment variables only select the managed Harness client and its
        bounded read-only Gateway; they do not grant a model permission to mutate the
        simulator.
        """

        current = self.lambda_client.get_function_configuration(FunctionName=function_arn)
        variables = dict((current.get("Environment") or {}).get("Variables") or {})
        variables.update(
            {
                "OPSFUSION_HARNESS_ARN": str(values["CF_OUT_HarnessArn"]),
                "OPSFUSION_GATEWAY_ARN": str(values["CF_OUT_GatewayArn"]),
                "OPSFUSION_MEMORY_ARN": str(values.get("CF_OUT_HarnessMemoryArn") or values.get("CF_OUT_MemoryId") or ""),
            }
        )
        self.lambda_client.update_function_configuration(
            FunctionName=function_arn,
            Environment={"Variables": variables},
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--context-file", type=Path, required=True)
    parser.add_argument("--model-id", default="us.amazon.nova-2-lite-v1:0")
    args = parser.parse_args()
    import boto3

    context = _context(args.context_file)
    session = boto3.Session(profile_name=args.profile or None, region_name=args.region)
    values = AgentCoreProvisioner(
        session.client("bedrock-agentcore-control"),
        region=args.region,
        lambda_client=session.client("lambda"),
    ).provision(
        environment=args.environment,
        context=context,
        model_id=args.model_id,
    )
    context.update(values)
    args.context_file.write_text(json.dumps(dict(sorted(context.items())), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(values, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
