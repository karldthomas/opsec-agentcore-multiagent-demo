"""Seeded synthetic scenarios and their observable records.

The hidden root cause is kept on the scenario definition for evaluation only.
Agent-facing code must consume ``observable_records`` and never serialize the
definition object directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    scenario_id: str
    name: str
    tenant_id: str
    region: str
    primary_incident_id: str
    hidden_root_cause: str
    seed: int
    incidents: tuple[dict[str, Any], ...]
    metrics: tuple[dict[str, Any], ...]
    logs: tuple[dict[str, Any], ...]
    deployments: tuple[dict[str, Any], ...]
    configuration_changes: tuple[dict[str, Any], ...]
    database_metrics: tuple[dict[str, Any], ...]
    queue_metrics: tuple[dict[str, Any], ...]
    orders: tuple[dict[str, Any], ...]
    runbooks: tuple[dict[str, Any], ...]
    initial_values: dict[str, Any]

    def observable_records(self, source: str) -> tuple[dict[str, Any], ...]:
        """Return only the selected source records, never scenario metadata."""

        values = {
            "incidents": self.incidents,
            "metrics": self.metrics,
            "logs": self.logs,
            "deployments": self.deployments,
            "configuration_changes": self.configuration_changes,
            "database_metrics": self.database_metrics,
            "queue_metrics": self.queue_metrics,
            "orders": self.orders,
            "runbooks": self.runbooks,
        }
        return values.get(source, ())


def _primary() -> ScenarioDefinition:
    tenant = "acme-retail"
    return ScenarioDefinition(
        scenario_id="INC-2048",
        name="Order processing degradation",
        tenant_id=tenant,
        region="us-east-1",
        primary_incident_id="INC-2048",
        hidden_root_cause="database_connection_pool_regression",
        seed=2048,
        incidents=(
            {
                "record_id": "INC-2048",
                "incident_id": "INC-2048",
                "severity": "SEV-1",
                "tenant_id": tenant,
                "region": "us-east-1",
                "service": "orders-service",
                "deployment": "orders-service-v2.7.0",
                "status": "investigating",
                "environment": "synthetic-production",
                "started_at": "2026-11-23T14:05:00Z",
            },
        ),
        metrics=(
            {
                "record_id": "METRIC-API-BASELINE",
                "timestamp": "2026-11-23T14:00:00Z",
                "service": "orders-service",
                "metric": "api_error_rate",
                "value": 0.3,
                "unit": "percent",
            },
            {
                "record_id": "METRIC-API-2048",
                "timestamp": "2026-11-23T14:15:00Z",
                "service": "orders-service",
                "metric": "api_error_rate",
                "value": 18.7,
                "unit": "percent",
            },
            {
                "record_id": "METRIC-LATENCY-2048",
                "timestamp": "2026-11-23T14:15:00Z",
                "service": "orders-service",
                "metric": "api_p95_latency_ms",
                "value": 14200,
                "unit": "milliseconds",
            },
            {
                "record_id": "METRIC-QUEUE-2048",
                "timestamp": "2026-11-23T14:15:00Z",
                "service": "orders-service",
                "metric": "sqs_queue_depth",
                "value": 42800,
                "unit": "messages",
            },
            {
                "record_id": "METRIC-DB-104",
                "timestamp": "2026-11-23T14:12:00Z",
                "service": "orders-service",
                "metric": "database_connections",
                "value": 500,
                "unit": "connections",
            },
            {
                "record_id": "METRIC-ORDER-DELAY-2048",
                "timestamp": "2026-11-23T14:15:00Z",
                "service": "orders-service",
                "metric": "order_processing_delay_seconds",
                "value": 90,
                "unit": "seconds",
            },
        ),
        logs=(
            {
                "record_id": "LOG-DB-208",
                "timestamp": "2026-11-23T14:11:32Z",
                "service": "orders-service",
                "level": "ERROR",
                "request_id": "req-2048-001",
                "error_type": "DatabaseConnectionTimeout",
                "message": "timed out acquiring a database connection",
            },
            {
                "record_id": "LOG-DB-209",
                "timestamp": "2026-11-23T14:12:02Z",
                "service": "orders-service",
                "level": "WARN",
                "request_id": "req-2048-002",
                "error_type": "RetryScheduled",
                "message": "database request retry scheduled after connection timeout",
            },
            {
                "record_id": "LOG-API-210",
                "timestamp": "2026-11-23T14:13:18Z",
                "service": "orders-service",
                "level": "ERROR",
                "request_id": "req-2048-003",
                "error_type": "UpstreamTimeout",
                "message": "order request exceeded downstream timeout budget",
            },
        ),
        deployments=(
            {
                "record_id": "DEP-2048-OLD",
                "deployment_id": "DEP-2048-OLD",
                "service": "orders-service",
                "version": "orders-service-v2.6.4",
                "released_at": "2026-11-20T09:00:00Z",
                "status": "superseded",
            },
            {
                "record_id": "DEP-2048-NEW",
                "deployment_id": "DEP-2048-NEW",
                "service": "orders-service",
                "version": "orders-service-v2.7.0",
                "released_at": "2026-11-23T14:05:00Z",
                "status": "active",
                "change_summary": "connection-pool settings changed under concurrent load",
            },
        ),
        configuration_changes=(
            {
                "record_id": "CONFIG-2048-001",
                "service": "orders-service",
                "deployment_id": "DEP-2048-NEW",
                "setting": "database.connection_pool",
                "previous_value": {"max_size": 100, "acquire_timeout_ms": 2000},
                "new_value": {"max_size": 500, "acquire_timeout_ms": 2000},
                "changed_at": "2026-11-23T14:05:00Z",
            },
        ),
        database_metrics=(
            {
                "record_id": "DB-2048-001",
                "timestamp": "2026-11-23T14:12:00Z",
                "database": "orders-db",
                "connections": 500,
                "max_connections": 500,
                "connection_waiters": 164,
                "query_latency_ms": 4200,
                "timeout_count": 318,
            },
            {
                "record_id": "DB-2048-002",
                "timestamp": "2026-11-23T14:14:00Z",
                "database": "orders-db",
                "connections": 500,
                "max_connections": 500,
                "connection_waiters": 221,
                "query_latency_ms": 6100,
                "timeout_count": 507,
            },
        ),
        queue_metrics=(
            {
                "record_id": "QUEUE-2048-001",
                "timestamp": "2026-11-23T14:15:00Z",
                "queue": "orders-processing",
                "depth": 42800,
                "baseline_depth": 120,
                "oldest_message_age_seconds": 94,
                "throughput_per_second": 38,
            },
        ),
        orders=(
            {
                "record_id": "ORDER-2048-SUMMARY",
                "tenant_id": tenant,
                "total_orders": 185000,
                "delayed_orders": 1240,
                "failed_orders": 0,
                "duplicate_orders": 0,
                "affected_customer_count": 816,
                "sample_order_ids": ["ORD-2048-0001", "ORD-2048-0002", "ORD-2048-0003"],
            },
            {
                "record_id": "ORDER-2048-0001",
                "order_id": "ORD-2048-0001",
                "tenant_id": tenant,
                "status": "delayed",
                "customer_id": "CUST-00816",
                "delay_seconds": 94,
            },
        ),
        runbooks=(
            {
                "record_id": "RUNBOOK-DB-SATURATION",
                "title": "Database saturation and connection waiters",
                "text": "Confirm connection utilization, inspect timeout ordering, and compare recent configuration changes with service symptoms.",
                "tags": ["database", "connections", "investigation"],
            },
            {
                "record_id": "RUNBOOK-ORDER-IMPACT",
                "title": "Orders service customer impact",
                "text": "Quantify delayed orders and affected customers, then connect the impact to the observed service signals.",
                "tags": ["orders-service", "customer-impact", "investigation"],
            },
        ),
        initial_values={
            "api_error_rate": 18.7,
            "api_p95_latency_ms": 14200,
            "queue_depth": 42800,
            "database_connections": 500,
            "order_processing_delay_seconds": 90,
            "service_status": "degraded",
        },
    )


def _variant(
    *,
    scenario_id: str,
    name: str,
    hidden_root_cause: str,
    service: str,
    symptom: str,
    deployment_version: str,
    tenant_id: str = "acme-retail",
    seed: int,
) -> ScenarioDefinition:
    incident_id = scenario_id
    return ScenarioDefinition(
        scenario_id=scenario_id,
        name=name,
        tenant_id=tenant_id,
        region="us-east-1",
        primary_incident_id=incident_id,
        hidden_root_cause=hidden_root_cause,
        seed=seed,
        incidents=(
            {
                "record_id": incident_id,
                "incident_id": incident_id,
                "severity": "SEV-1",
                "tenant_id": tenant_id,
                "region": "us-east-1",
                "service": service,
                "deployment": deployment_version,
                "status": "investigating",
                "environment": "synthetic-production",
                "started_at": "2026-11-23T15:05:00Z",
            },
        ),
        metrics=(
            {
                "record_id": f"METRIC-{scenario_id}-001",
                "timestamp": "2026-11-23T15:15:00Z",
                "service": service,
                "metric": "api_error_rate",
                "value": 12.5,
                "unit": "percent",
            },
            {
                "record_id": f"METRIC-{scenario_id}-002",
                "timestamp": "2026-11-23T15:15:00Z",
                "service": service,
                "metric": "api_p95_latency_ms",
                "value": 9500,
                "unit": "milliseconds",
            },
        ),
        logs=(
            {
                "record_id": f"LOG-{scenario_id}-001",
                "timestamp": "2026-11-23T15:12:00Z",
                "service": service,
                "level": "ERROR",
                "error_type": symptom,
                "message": f"{symptom} observed during incident investigation",
            },
        ),
        deployments=(
            {
                "record_id": f"DEP-{scenario_id}-001",
                "deployment_id": f"DEP-{scenario_id}-001",
                "service": service,
                "version": deployment_version,
                "released_at": "2026-11-23T15:05:00Z",
                "status": "active",
                "change_summary": "recent application release",
            },
        ),
        configuration_changes=(
            {
                "record_id": f"CONFIG-{scenario_id}-001",
                "service": service,
                "deployment_id": f"DEP-{scenario_id}-001",
                "setting": "application.retry_or_query_behavior",
                "previous_value": "baseline",
                "new_value": "changed",
                "changed_at": "2026-11-23T15:05:00Z",
            },
        ),
        database_metrics=(
            {
                "record_id": f"DB-{scenario_id}-001",
                "timestamp": "2026-11-23T15:15:00Z",
                "database": "orders-db",
                "connections": 230,
                "max_connections": 500,
                "connection_waiters": 4,
                "query_latency_ms": 1800,
                "timeout_count": 12,
            },
        ),
        queue_metrics=(
            {
                "record_id": f"QUEUE-{scenario_id}-001",
                "timestamp": "2026-11-23T15:15:00Z",
                "queue": "orders-processing",
                "depth": 9100,
                "baseline_depth": 120,
                "oldest_message_age_seconds": 42,
                "throughput_per_second": 61,
            },
        ),
        orders=(
            {
                "record_id": f"ORDER-{scenario_id}-SUMMARY",
                "tenant_id": tenant_id,
                "total_orders": 100000,
                "delayed_orders": 420,
                "failed_orders": 12,
                "duplicate_orders": 0,
                "affected_customer_count": 270,
                "sample_order_ids": [f"ORD-{scenario_id}-0001"],
            },
        ),
        runbooks=(
            {
                "record_id": f"RUNBOOK-{scenario_id}-001",
                "title": "Incident investigation guidance",
                "text": "Correlate telemetry, deployments, dependencies, and business impact before recommending a change.",
                "tags": ["investigation", "evidence"],
            },
        ),
        initial_values={
            "api_error_rate": 12.5,
            "api_p95_latency_ms": 9500,
            "queue_depth": 9100,
            "database_connections": 230,
            "order_processing_delay_seconds": 42,
            "service_status": "degraded",
        },
    )


def all_scenario_definitions() -> dict[str, ScenarioDefinition]:
    return {
        "INC-2048": _primary(),
        "INC-2051": _variant(
            scenario_id="INC-2051",
            name="Retry storm",
            hidden_root_cause="retry_amplification_without_backoff",
            service="orders-service",
            symptom="RepeatedTimeoutException",
            deployment_version="orders-service-v2.7.1",
            seed=2051,
        ),
        "INC-2054": _variant(
            scenario_id="INC-2054",
            name="Database query regression",
            hidden_root_cause="high_frequency_query_without_index",
            service="orders-service",
            symptom="SlowQuery",
            deployment_version="orders-service-v2.7.2",
            seed=2054,
        ),
        "INC-2057": _variant(
            scenario_id="INC-2057",
            name="Misleading deployment correlation",
            hidden_root_cause="third_party_dependency_outage",
            service="orders-service",
            symptom="DependencyTimeout",
            deployment_version="orders-service-v2.7.3",
            seed=2057,
        ),
    }


def scenario_definition(scenario_id: str) -> ScenarioDefinition:
    try:
        return all_scenario_definitions()[scenario_id]
    except KeyError as exc:
        choices = ", ".join(sorted(all_scenario_definitions()))
        raise ValueError(f"unknown scenario {scenario_id}; choose from {choices}") from exc
