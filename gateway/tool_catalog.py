"""Typed tool catalog used directly in local tests and by the Gateway adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from contracts import EvidenceReference, ToolResponse
from contracts.models import utc_now
from simulator.engine import ScenarioEngine, SimulatorError


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    tenant_id: str
    actor_id: str
    role: str
    request_id: str = ""

    def resolved_request_id(self) -> str:
        return self.request_id or f"req-{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    category: str
    description: str
    read_only: bool
    roles: frozenset[str]


def _evidence(
    record: dict[str, Any],
    *,
    context: ToolContext,
    source_type: str,
    query: dict[str, Any],
) -> EvidenceReference:
    record_id = str(record.get("record_id") or record.get("id") or "unknown")
    return EvidenceReference(
        evidence_id=f"EVD-{record_id}",
        source_type=source_type,
        source_record_id=record_id,
        scenario_run_id=context.run_id,
        tenant_id=context.tenant_id,
        observed_at=str(record.get("timestamp") or record.get("observed_at") or utc_now()),
        query=query,
    )


class ToolCatalog:
    """A least-privilege local equivalent of the Gateway tool boundary."""

    _ROLE_ALL = frozenset(
        {
            "orchestrator",
            "telemetry-agent",
            "change-agent",
            "data-agent",
        }
    )

    def __init__(self, engine: ScenarioEngine | None = None, *, evidence: Any | None = None) -> None:
        self.engine = engine or ScenarioEngine()
        self.evidence = evidence
        self._handlers: dict[str, tuple[ToolSpec, Callable[[ToolContext, dict[str, Any]], ToolResponse]]] = {}
        self._register_tools()

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec, _ in self._handlers.values())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._handlers[name][0]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def invoke(self, name: str, context: ToolContext, parameters: dict[str, Any] | None = None) -> ToolResponse:
        request_id = context.resolved_request_id()
        parameters = dict(parameters or {})
        try:
            spec, handler = self._handlers[name]
        except KeyError:
            return self._error(request_id, name, context, "TOOL_NOT_FOUND", "tool is not registered")
        if context.role not in spec.roles:
            return self._denied(request_id, name, context, "ROLE_NOT_AUTHORIZED")
        try:
            return handler(context, parameters)
        except SimulatorError as exc:
            return self._error(request_id, name, context, exc.code, str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._error(request_id, name, context, "INVALID_TOOL_INPUT", str(exc))

    def _register(self, spec: ToolSpec, handler: Callable[[ToolContext, dict[str, Any]], ToolResponse]) -> None:
        self._handlers[spec.name] = (spec, handler)

    def _register_tools(self) -> None:
        read_all = self._ROLE_ALL
        telemetry = frozenset({"orchestrator", "telemetry-agent"})
        change = frozenset({"orchestrator", "change-agent"})
        data = frozenset({"orchestrator", "data-agent"})

        self._register(ToolSpec("get_incident_context", "workflow", "Get the current incident anchor and service context.", True, read_all), self._get_incident_context)
        self._register(ToolSpec("get_shared_evidence", "workflow", "Retrieve the current run-scoped evidence workspace.", True, frozenset({"orchestrator"})), self._get_shared_evidence)
        self._register(ToolSpec("query_cloudwatch_metrics", "observability", "Query bounded synthetic metric observations.", True, telemetry), self._query_metrics)
        self._register(ToolSpec("search_application_logs", "observability", "Search bounded application log observations.", True, telemetry), self._search_logs)
        self._register(ToolSpec("get_service_traces", "observability", "Retrieve request trace observations derived from simulator records.", True, telemetry), self._get_traces)
        self._register(ToolSpec("get_active_alarms", "observability", "Retrieve active synthetic alarms.", True, telemetry), self._get_active_alarms)
        self._register(ToolSpec("compare_metric_windows", "observability", "Compare baseline and incident metric windows.", True, telemetry), self._compare_metric_windows)
        self._register(ToolSpec("get_service_health", "observability", "Get current service health and incident values.", True, read_all), self._get_service_health)
        self._register(ToolSpec("list_recent_deployments", "change-management", "List recent deployment records.", True, change), self._list_deployments)
        self._register(ToolSpec("get_deployment_details", "change-management", "Get one deployment record.", True, change), self._get_deployment_details)
        self._register(ToolSpec("compare_deployment_versions", "change-management", "Compare observable deployment versions.", True, change), self._compare_deployments)
        self._register(ToolSpec("get_configuration_changes", "change-management", "Get observable configuration differences.", True, change), self._get_configuration_changes)
        self._register(ToolSpec("search_runbooks", "knowledge", "Search synthetic troubleshooting guidance.", True, change | read_all), self._search_runbooks)
        self._register(ToolSpec("get_database_metrics", "business-data", "Get database capacity and latency observations.", True, data | telemetry), self._get_database_metrics)
        self._register(ToolSpec("get_queue_metrics", "business-data", "Get queue depth and age observations.", True, data | telemetry), self._get_queue_metrics)
        self._register(ToolSpec("query_order_records", "business-data", "Query bounded synthetic order records.", True, data), self._query_orders)
        self._register(ToolSpec("get_order_processing_history", "business-data", "Get order progress observations.", True, data), self._get_order_history)
        self._register(ToolSpec("calculate_customer_impact", "business-data", "Calculate synthetic customer impact.", True, data), self._calculate_customer_impact)

    def _response(
        self,
        context: ToolContext,
        tool_name: str,
        data: dict[str, Any],
        records: list[tuple[str, dict[str, Any], dict[str, Any]]] | None = None,
    ) -> ToolResponse:
        records = records or []
        evidence = tuple(
            _evidence(record, context=context, source_type=source, query=query)
            for source, record, query in records
        )
        return ToolResponse(
            request_id=context.resolved_request_id(),
            tool_name=tool_name,
            scenario_run_id=context.run_id,
            tenant_id=context.tenant_id,
            status="ok",
            data=data,
            evidence=evidence,
            role=context.role,
        )

    def _error(self, request_id: str, tool_name: str, context: ToolContext, code: str, message: str) -> ToolResponse:
        return ToolResponse(
            request_id=request_id,
            tool_name=tool_name,
            scenario_run_id=context.run_id,
            tenant_id=context.tenant_id,
            status="error",
            data={},
            errors=({"code": code, "message": message},),
            role=context.role,
        )

    def _denied(self, request_id: str, tool_name: str, context: ToolContext, code: str) -> ToolResponse:
        return ToolResponse(
            request_id=request_id,
            tool_name=tool_name,
            scenario_run_id=context.run_id,
            tenant_id=context.tenant_id,
            status="denied",
            data={},
            errors=({"code": code, "message": "role is not authorized for this tool"},),
            role=context.role,
        )

    def _records(self, context: ToolContext, source: str, parameters: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        records = self.engine.observe(context.run_id, source, tenant_id=context.tenant_id, filters=parameters)
        return [(source, record, parameters) for record in records]

    def _get_incident_context(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        data = self.engine.get_incident_context(context.run_id, tenant_id=context.tenant_id)
        records = self._records(context, "incidents", {})
        return self._response(context, "get_incident_context", data, records)

    def _get_shared_evidence(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        if self.evidence is None:
            raise SimulatorError("shared evidence is unavailable at this boundary", "EVIDENCE_UNAVAILABLE")
        data = self.evidence.get_shared_evidence(context.run_id, tenant_id=context.tenant_id)
        return self._response(context, "get_shared_evidence", data)

    def _query_metrics(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        allowed = {key: parameters[key] for key in ("service", "metric") if key in parameters}
        records = self._records(context, "metrics", allowed)
        return self._response(
            context,
            "query_cloudwatch_metrics",
            {"observations": [record for _, record, _ in records]},
            records,
        )

    def _search_logs(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        query = str(parameters.get("query") or "").strip().casefold()
        records = self.engine.observe(context.run_id, "logs", tenant_id=context.tenant_id)
        if query:
            records = [record for record in records if query in str(record).casefold()]
        records = records[:50]
        return self._response(context, "search_application_logs", {"observations": records}, [("logs", record, {"query": query}) for record in records])

    def _get_traces(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        logs = self.engine.observe(context.run_id, "logs", tenant_id=context.tenant_id)
        traces = [
            {
                "record_id": f"TRACE-{record['record_id']}",
                "request_id": record.get("request_id", "unknown"),
                "service": record.get("service"),
                "timestamp": record.get("timestamp"),
                "status": "error" if record.get("level") == "ERROR" else "degraded",
                "span_summary": record.get("message"),
            }
            for record in logs[:50]
        ]
        return self._response(context, "get_service_traces", {"observations": traces}, [("logs", record, {}) for record in logs[:50]])

    def _get_active_alarms(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        health = self.engine.service_health(context.run_id, tenant_id=context.tenant_id)
        alarms = []
        if health["api_error_rate"] > 1:
            alarms.append({"record_id": "ALARM-API-ERROR-RATE", "alarm": "ApiErrorRateHigh", "state": "ALARM", "value": health["api_error_rate"]})
        if health["queue_depth"] > 1000:
            alarms.append({"record_id": "ALARM-QUEUE-BACKLOG", "alarm": "QueueBacklogHigh", "state": "ALARM", "value": health["queue_depth"]})
        if health["database_connections"] >= 500:
            alarms.append({"record_id": "ALARM-DB-CONNECTIONS", "alarm": "DatabaseConnectionsAtLimit", "state": "ALARM", "value": health["database_connections"]})
        return self._response(context, "get_active_alarms", {"alarms": alarms}, [("metrics", record, {}) for record in alarms])

    def _compare_metric_windows(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        metric = str(parameters.get("metric") or "").strip()
        records = self.engine.observe(context.run_id, "metrics", tenant_id=context.tenant_id, filters={"metric": metric} if metric else {})
        baseline = next((record for record in records if "BASELINE" in record["record_id"]), None)
        incident = next((record for record in reversed(records) if record is not baseline), None)
        data = {"metric": metric, "baseline": baseline, "incident": incident}
        selected = [record for record in (baseline, incident) if record]
        return self._response(context, "compare_metric_windows", data, [("metrics", record, {"metric": metric}) for record in selected])

    def _get_service_health(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        health = self.engine.service_health(context.run_id, tenant_id=context.tenant_id)
        record = self.engine.service_health_record(context.run_id, tenant_id=context.tenant_id)
        return self._response(context, "get_service_health", health, [("service_health", record, {})])

    def _list_deployments(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self._records(context, "deployments", {})[:50]
        return self._response(context, "list_recent_deployments", {"deployments": [record for _, record, _ in records]}, records)

    def _get_deployment_details(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        deployment_id = str(parameters.get("deployment_id") or "").strip()
        records = self.engine.observe(context.run_id, "deployments", tenant_id=context.tenant_id)
        selected = [record for record in records if record.get("deployment_id") == deployment_id]
        if not selected:
            raise SimulatorError("deployment was not found", "DEPLOYMENT_NOT_FOUND")
        record = selected[0]
        return self._response(context, "get_deployment_details", {"deployment": record}, [("deployments", record, {"deployment_id": deployment_id})])

    def _compare_deployments(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self.engine.observe(context.run_id, "deployments", tenant_id=context.tenant_id)
        return self._response(context, "compare_deployment_versions", {"versions": records}, [("deployments", record, {}) for record in records])

    def _get_configuration_changes(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self._records(context, "configuration_changes", {})
        return self._response(context, "get_configuration_changes", {"changes": [record for _, record, _ in records]}, records)

    def _search_runbooks(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        query = str(parameters.get("query") or "").strip().casefold()
        records = self.engine.observe(context.run_id, "runbooks", tenant_id=context.tenant_id)
        if query:
            records = [record for record in records if query in str(record).casefold()]
        return self._response(context, "search_runbooks", {"documents": records[:20]}, [("runbooks", record, {"query": query}) for record in records[:20]])

    def _get_database_metrics(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self._records(context, "database_metrics", {})
        return self._response(context, "get_database_metrics", {"observations": [record for _, record, _ in records]}, records)

    def _get_queue_metrics(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self._records(context, "queue_metrics", {})
        return self._response(context, "get_queue_metrics", {"observations": [record for _, record, _ in records]}, records)

    def _query_orders(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self._records(context, "orders", {})[:100]
        return self._response(context, "query_order_records", {"records": [record for _, record, _ in records]}, records)

    def _get_order_history(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self.engine.observe(context.run_id, "orders", tenant_id=context.tenant_id)
        history = [
            {
                "record_id": f"HISTORY-{record['record_id']}",
                "order_id": record.get("order_id") or record.get("sample_order_ids", [None])[0],
                "status": record.get("status", "delayed"),
                "observed_at": record.get("timestamp", "2026-11-23T14:15:00Z"),
            }
            for record in records
        ]
        return self._response(context, "get_order_processing_history", {"history": history}, [("orders", record, {}) for record in records])

    def _calculate_customer_impact(self, context: ToolContext, parameters: dict[str, Any]) -> ToolResponse:
        records = self.engine.observe(context.run_id, "orders", tenant_id=context.tenant_id)
        summary = next((record for record in records if "affected_customer_count" in record), None)
        if not summary:
            raise SimulatorError("customer impact summary is unavailable", "IMPACT_NOT_FOUND")
        impact = {key: summary[key] for key in ("total_orders", "delayed_orders", "failed_orders", "duplicate_orders", "affected_customer_count")}
        return self._response(context, "calculate_customer_impact", impact, [("orders", summary, {})])
