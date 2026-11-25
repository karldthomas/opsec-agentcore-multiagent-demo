from __future__ import annotations

import pytest

from contracts import Finding, Hypothesis
from evidence import EvidenceStore, EvidenceStoreError
from gateway.tool_catalog import ToolCatalog, ToolContext
from simulator.engine import ScenarioEngine


def test_evidence_store_accepts_grounded_finding_and_hypothesis() -> None:
    engine = ScenarioEngine()
    engine.create_run("INC-2048", run_id="evidence-run")
    catalog = ToolCatalog(engine)
    store = EvidenceStore(engine)
    response = catalog.invoke("get_database_metrics", ToolContext("evidence-run", "acme-retail", "a", "data-agent"))
    store.record_tool_response(response)
    finding = store.add_finding(
        Finding(
            finding_id="FIND-DB-001",
            agent="data-agent",
            claim_type="fact",
            statement="Database connections reached the configured maximum.",
            evidence_ids=("EVD-DB-2048-001",),
            confidence=0.98,
            scenario_run_id="evidence-run",
            tenant_id="acme-retail",
        )
    )
    hypothesis = store.add_hypothesis(
        Hypothesis(
            hypothesis_id="H-001",
            statement="Connection pressure is a primary contributor to the degradation.",
            supporting_finding_ids=(finding.finding_id,),
            contradicting_finding_ids=(),
            confidence=0.8,
            scenario_run_id="evidence-run",
            tenant_id="acme-retail",
        )
    )
    assert hypothesis.hypothesis_id == "H-001"
    shared = store.get_shared_evidence("evidence-run", tenant_id="acme-retail")
    assert shared["findings"][0]["evidence_ids"] == ["EVD-DB-2048-001"]
    assert shared["observations"][0]["record"]["connections"] == 500
    assert shared["tool_events"][0]["data"]["observations"][0]["connections"] == 500


def test_evidence_store_rejects_unsupported_claims() -> None:
    store = EvidenceStore()
    with pytest.raises(EvidenceStoreError, match="unknown evidence"):
        store.add_finding(
            Finding(
                finding_id="FIND-BAD",
                agent="telemetry-agent",
                claim_type="hypothesis",
                statement="Unsupported conclusion.",
                evidence_ids=("EVD-NOT-RECORDED",),
                confidence=0.5,
                scenario_run_id="missing-run",
                tenant_id="acme-retail",
            )
        )
