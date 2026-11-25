"""OpsFusion Registry record definitions.

These records describe capabilities; they do not contain incident-specific
answers or grant permission to perform simulator state changes.
"""

from __future__ import annotations

from .client import RegistryClient, RegistryRecord


def opsfusion_records(environment: str = "dev") -> tuple[RegistryRecord, ...]:
    tags = {"Application": "OpsFusion", "Environment": environment, "ManagedBy": "opsfusion"}
    records: list[RegistryRecord] = []
    agent_roles = (
        ("orchestrator", "Coordinates investigation tasks and evidence reconciliation."),
        ("telemetry-agent", "Investigates metrics, logs, traces, and alarms."),
        ("change-agent", "Investigates deployments and configuration changes."),
        ("data-agent", "Investigates database, queue, order, and customer impact."),
    )
    for role, description in agent_roles:
        records.append(
            RegistryRecord(
                name=f"opsfusion-{environment}-{role}",
                display_name=f"OpsFusion {role}",
                record_type="AGENT",
                description=description,
                descriptors=RegistryClient.descriptor_for_custom(
                    {"role": role, "execution": "agentcore-harness", "capabilities": "synthetic-incident-investigation"}
                ),
                custom_metadata={"role": role, "owner": "opsfusion-platform", "version": "1.0.0", "safety_boundary": "synthetic-only"},
                tags=tags,
            )
        )
    records.append(
        RegistryRecord(
            name=f"opsfusion-{environment}-gateway",
            display_name="OpsFusion operational Gateway",
            record_type="GATEWAY",
            description="AgentCore Gateway exposing bounded synthetic operational tools.",
                descriptors=RegistryClient.descriptor_for_custom(
                    {"tool_count": 18, "read_only_default": True, "state_changes": "never"}
            ),
            custom_metadata={"owner": "opsfusion-platform", "version": "1.0.0", "tool_boundary": "role-scoped", "data": "synthetic"},
            tags=tags,
        )
    )
    for skill in (
        "telemetry-triage",
        "change-analysis",
        "business-impact-analysis",
        "investigation-planning",
        "evidence-reconciliation",
    ):
        records.append(
            RegistryRecord(
                name=f"opsfusion-{environment}-skill-{skill}",
                display_name=f"OpsFusion {skill} skill",
                record_type="SKILL",
                description=f"Reusable {skill} guidance for OpsFusion agents.",
                descriptors=RegistryClient.descriptor_for_custom(
                    {"skill": skill, "content_policy": "untrusted-guidance", "state_changes": "never-authorized"}
                ),
                custom_metadata={"skill": skill, "owner": "opsfusion-platform", "version": "1.0.0", "safety_boundary": "reference-only"},
                tags=tags,
            )
        )
    return tuple(records)
