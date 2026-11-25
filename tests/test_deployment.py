from __future__ import annotations

from pathlib import Path

from gateway.openapi import build_openapi
from infrastructure.cloudformation.manifest import load_manifest
from workflow.worker import handler


def test_manifest_has_ordered_independent_stacks_and_targeted_selection() -> None:
    root = Path(__file__).parents[1]
    manifest = load_manifest(root / "infrastructure" / "cloudformation" / "deployment.yaml")
    assert manifest.stack_ids[0] == "00-foundation"
    assert manifest.stack_ids[-1] == "08-observability-evaluation"
    assert manifest.selected(start="04-agentcore")[0] == "04-agentcore"
    assert "08-observability-evaluation" in manifest.selected(start="04-agentcore")
    assert manifest.dependencies("06-application") == ("00-foundation", "01-state", "02-simulator", "03-knowledge", "04-agentcore", "05-workflow")


def test_gateway_contract_contains_bounded_read_only_tools() -> None:
    document = build_openapi()
    assert len(document["paths"]) >= 15
    assert "/tools/query_cloudwatch_metrics" in document["paths"]
    assert "/tools/execute_approved_remediation" not in document["paths"]


def test_step_functions_worker_has_allowlisted_stages() -> None:
    assert handler({"stage": "initialize", "input": {"run_id": "run-1"}}, None)["worker_status"] == "checkpointed"


def test_agentcore_is_cloudformation_managed_and_deploys_no_sdk_provision_step() -> None:
    root = Path(__file__).parents[1]
    template = (root / "infrastructure" / "cloudformation" / "stacks" / "04-agentcore.yaml").read_text(encoding="utf-8")
    orchestrator = (root / "infrastructure" / "cloudformation" / "orchestrate.py").read_text(encoding="utf-8")
    manifest = load_manifest(root / "infrastructure" / "cloudformation" / "deployment.yaml")

    assert "AWS::BedrockAgentCore::Gateway" in template
    assert "AWS::BedrockAgentCore::GatewayTarget" in template
    assert "AWS::BedrockAgentCore::Harness" in template
    assert "AWS::BedrockAgentCore::Memory" in template
    assert "AWS::BedrockAgentCore::PolicyEngine" in template
    assert "AWS::BedrockAgentCore::Policy" in template
    assert 'resource == AgentCore::Gateway::"${GatewayArn}"' in template
    assert "provision-agentcore" not in orchestrator
    assert all(step.handler != "provision_agentcore" for step in manifest.steps)
