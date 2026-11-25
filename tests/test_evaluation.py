from __future__ import annotations

from contracts import Finding, Hypothesis
from evaluation import DeterministicEvaluator
from evidence import EvidenceStore
from gateway.tool_catalog import ToolCatalog, ToolContext
from simulator.engine import ScenarioEngine


def test_deterministic_evaluator_uses_hidden_truth_only_on_evaluator_side() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="eval-run")
    catalog = ToolCatalog(engine)
    evidence = EvidenceStore(engine)
    response = catalog.invoke("get_database_metrics", ToolContext("eval-run", "acme-retail", "operator", "data-agent"), {})
    evidence.record_tool_response(response)
    reference = response.evidence[0].evidence_id
    finding = Finding("eval-finding", "data-agent", "fact", "Connections reached the configured maximum.", (reference,), 0.98, "eval-run", "acme-retail")
    evidence.add_finding(finding)
    evidence.add_hypothesis(Hypothesis("eval-hypothesis", "The connection pool regression is consistent with the observed saturation.", (finding.finding_id,), (), 0.9, "eval-run", "acme-retail"))
    result = DeterministicEvaluator(engine, evidence).evaluate("eval-run")
    assert result.checks["hidden_state_boundary"]["passed"] is True
    assert result.checks["root_cause_match"]["passed"] is True
    assert result.checks["read_only_boundary"]["passed"] is True
    assert result.score > 0
