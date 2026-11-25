from __future__ import annotations

import json

import pytest

from agents.output import AgentOutputError, parse_agent_output


def _payload(**overrides):
    payload = {
        "findings": [],
        "hypotheses": [],
        "unresolved_questions": [],
        "follow_up_requests": [],
        "recommendations": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_agent_output_validates_nested_hypothesis_fields() -> None:
    with pytest.raises(AgentOutputError, match=r"hypotheses\[0\] is missing statement"):
        parse_agent_output(_payload(hypotheses=[{"confidence": 0.8}]))


def test_agent_output_rejects_non_finite_confidence_before_persistence() -> None:
    with pytest.raises(AgentOutputError, match=r"findings\[0\]\.confidence"):
        parse_agent_output(
            _payload(
                findings=[
                    {
                        "claim_type": "fact",
                        "statement": "A service signal was observed.",
                        "evidence_ids": ["EVD-INC-2048"],
                        "confidence": "not-a-number",
                    }
                ]
            )
        )


def test_follow_up_strings_are_normalized_to_business_requests() -> None:
    output = parse_agent_output(_payload(follow_up_requests=["Confirm the customer impact."]))
    assert output.follow_up_requests == ({"objective": "Confirm the customer impact."},)
