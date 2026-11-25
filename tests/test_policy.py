from __future__ import annotations

from agentcore.policy import PolicyProvisioner


def test_policy_statement_explicitly_allows_only_read_only_gateway_actions() -> None:
    statement = PolicyProvisioner.read_only_statement(
        "arn:aws:bedrock-agentcore:us-east-1:1:gateway/gw",
        ["query_cloudwatch_metrics", "get_service_health"],
    )
    assert "action in [" in statement
    assert 'opsfusion-simulator-tools___query_cloudwatch_metrics' in statement
    assert 'opsfusion-simulator-tools___get_service_health' in statement
    assert "execute_approved_remediation" not in statement
    assert "AgentCore::Gateway::\"arn:aws:bedrock-agentcore:us-east-1:1:gateway/gw\"" in statement
