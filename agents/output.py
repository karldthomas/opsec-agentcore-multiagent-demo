"""Parse bounded structured agent output without exposing private reasoning."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from numbers import Real
import re
from typing import Any

from contracts.models import ContractError, _validate_no_hidden_keys


class AgentOutputError(ValueError):
    """Raised when an agent result does not follow its output contract."""


@dataclass(frozen=True, slots=True)
class ParsedAgentOutput:
    findings: tuple[dict[str, Any], ...]
    hypotheses: tuple[dict[str, Any], ...]
    unresolved_questions: tuple[str, ...]
    follow_up_requests: tuple[dict[str, Any], ...]
    recommendations: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [dict(item) for item in self.findings],
            "hypotheses": [dict(item) for item in self.hypotheses],
            "unresolved_questions": list(self.unresolved_questions),
            "follow_up_requests": [dict(item) for item in self.follow_up_requests],
            "recommendations": [dict(item) for item in self.recommendations],
        }


def _json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise AgentOutputError("agent returned no structured output")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else cleaned
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # Bedrock models sometimes add one short lead-in sentence or trailing
        # text despite the JSON-only instruction. Decode the first complete
        # object without retaining the surrounding prose.
        decoder = json.JSONDecoder()
        payload = None
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                possible, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(possible, dict):
                payload = possible
                break
        if payload is None:
            raise AgentOutputError("agent output must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise AgentOutputError("agent output must be a JSON object")
    try:
        _validate_no_hidden_keys(payload)
    except ContractError as exc:
        raise AgentOutputError(str(exc)) from exc
    return payload


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentOutputError(f"{path} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, path: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise AgentOutputError(f"{path} must be a list of strings")
    normalized = [_text(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if not allow_empty and not normalized:
        raise AgentOutputError(f"{path} must contain at least one item")
    return normalized


def _confidence(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise AgentOutputError(f"{path} must be a number between 0 and 1")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise AgentOutputError(f"{path} must be a number between 0 and 1")
    return normalized


def parse_agent_output(text: str) -> ParsedAgentOutput:
    payload = _json_object(text)
    findings = payload.get("findings", [])
    hypotheses = payload.get("hypotheses", [])
    questions = payload.get("unresolved_questions", [])
    follow_ups = payload.get("follow_up_requests", [])
    recommendations = payload.get("recommendations", [])
    if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
        raise AgentOutputError("findings must be a list of objects")
    if not isinstance(hypotheses, list) or not all(isinstance(item, dict) for item in hypotheses):
        raise AgentOutputError("hypotheses must be a list of objects")
    if not isinstance(questions, list) or not all(isinstance(item, str) and item.strip() for item in questions):
        raise AgentOutputError("unresolved_questions must be a list of non-empty strings")
    if not isinstance(follow_ups, list):
        raise AgentOutputError("follow_up_requests must be a list")
    follow_ups = [
        {"objective": item} if isinstance(item, str) and item.strip() else item
        for item in follow_ups
    ]
    if not all(isinstance(item, dict) for item in follow_ups):
        raise AgentOutputError("follow_up_requests must be a list of objects or strings")
    if not isinstance(recommendations, list):
        raise AgentOutputError("recommendations must be a list")
    # Models commonly emit concise recommendation strings even when the
    # contract asks for objects. Normalize that harmless shape at the boundary
    # while keeping the persisted representation structured.
    recommendations = [
        {"statement": item} if isinstance(item, str) and item.strip() else item
        for item in recommendations
    ]
    if not all(isinstance(item, dict) for item in recommendations):
        raise AgentOutputError("recommendations must be a list of objects or strings")
    normalized_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        for field in ("claim_type", "statement", "evidence_ids", "confidence"):
            if field not in finding:
                raise AgentOutputError(f"findings[{index}] is missing {field}")
        if finding["claim_type"] not in {"fact", "relationship", "hypothesis"}:
            raise AgentOutputError(f"findings[{index}].claim_type is invalid")
        normalized = dict(finding)
        normalized["statement"] = _text(finding["statement"], f"findings[{index}].statement")
        normalized["evidence_ids"] = _string_list(
            finding["evidence_ids"],
            f"findings[{index}].evidence_ids",
            allow_empty=False,
        )
        normalized["confidence"] = _confidence(finding["confidence"], f"findings[{index}].confidence")
        if "finding_id" in normalized and normalized["finding_id"] is not None:
            normalized["finding_id"] = _text(normalized["finding_id"], f"findings[{index}].finding_id")
        normalized_findings.append(normalized)

    normalized_hypotheses: list[dict[str, Any]] = []
    for index, hypothesis in enumerate(hypotheses):
        for field in ("statement", "confidence"):
            if field not in hypothesis:
                raise AgentOutputError(f"hypotheses[{index}] is missing {field}")
        normalized = dict(hypothesis)
        normalized["statement"] = _text(hypothesis["statement"], f"hypotheses[{index}].statement")
        normalized["supporting_finding_ids"] = _string_list(
            hypothesis.get("supporting_finding_ids", []),
            f"hypotheses[{index}].supporting_finding_ids",
        )
        normalized["contradicting_finding_ids"] = _string_list(
            hypothesis.get("contradicting_finding_ids", []),
            f"hypotheses[{index}].contradicting_finding_ids",
        )
        normalized["confidence"] = _confidence(hypothesis["confidence"], f"hypotheses[{index}].confidence")
        if "hypothesis_id" in normalized and normalized["hypothesis_id"] is not None:
            normalized["hypothesis_id"] = _text(normalized["hypothesis_id"], f"hypotheses[{index}].hypothesis_id")
        normalized_hypotheses.append(normalized)

    normalized_follow_ups: list[dict[str, Any]] = []
    for index, request in enumerate(follow_ups):
        if not isinstance(request, dict):
            raise AgentOutputError(f"follow_up_requests[{index}] must be an object or non-empty string")
        normalized = dict(request)
        if not (normalized.get("objective") or normalized.get("question")):
            raise AgentOutputError(f"follow_up_requests[{index}] needs an objective or question")
        if normalized.get("objective") is not None:
            normalized["objective"] = _text(normalized["objective"], f"follow_up_requests[{index}].objective")
        if normalized.get("question") is not None:
            normalized["question"] = _text(normalized["question"], f"follow_up_requests[{index}].question")
        for field in ("agent", "role"):
            if normalized.get(field) is not None:
                normalized[field] = _text(normalized[field], f"follow_up_requests[{index}].{field}")
        normalized_follow_ups.append(normalized)

    normalized_recommendations: list[dict[str, Any]] = []
    for index, recommendation in enumerate(recommendations):
        if not isinstance(recommendation, dict):
            raise AgentOutputError(f"recommendations[{index}] must be an object or non-empty string")
        normalized_recommendations.append(dict(recommendation))

    return ParsedAgentOutput(
        tuple(normalized_findings),
        tuple(normalized_hypotheses),
        tuple(questions),
        tuple(normalized_follow_ups),
        tuple(normalized_recommendations),
    )
