# Case Study: OpsFusion - Multi-Agent Cloud Incident Investigation Simulator

> **Current implementation scope (2026-11-24):** The deployed OpsFusion
> console ends after evidence-grounded analysis. It has no incident approval,
> remediation, recovery, or report-publication action, and the simulator
> remains unchanged. The broader lifecycle described below is historical
> design context rather than an available runtime capability.

## 1. Executive Summary

OpsFusion is a proof-of-concept application that demonstrates how multiple AI agents can collaborate to investigate, diagnose, and resolve simulated cloud infrastructure incidents using Amazon Bedrock AgentCore.

The application recreates a SaaS production environment using entirely synthetic data. It simulates operational metrics, application logs, deployment histories, database performance, message queues, customer orders, and technical runbooks.

A team of specialized AI agents investigates incidents by invoking tools, exchanging findings, evaluating competing hypotheses, and coordinating remediation.

The agents interact with a stateful simulation environment rather than real production systems. This makes it possible to demonstrate realistic incident investigation, recovery, and evaluation without requiring live workloads or access to sensitive operational data.

The system uses real Amazon Bedrock models and AgentCore services for agent execution, tool integration, policy enforcement, memory, and observability.

The central objective is to demonstrate **multi-agent collaboration, multi-tool orchestration, evidence-grounded reasoning, controlled autonomy, and durable execution** in a reproducible environment.

---

## 2. Business Problem

Modern SaaS platforms depend on multiple interconnected services, including APIs, databases, message queues, containerized applications, and third-party integrations.

When an incident occurs, identifying its root cause can require correlating information across these systems.

For example, a sudden increase in API errors might result from:

- A recently deployed application regression.
- Database connection exhaustion.
- A downstream service outage.
- Message queue congestion.
- Incorrect infrastructure configuration.
- An unexpected increase in traffic.

The visible symptoms do not necessarily reveal the underlying cause.

Traditional incident response requires engineers to examine telemetry, review deployments, inspect application logs, evaluate business impact, and coordinate recovery actions.

This process can be time-consuming because evidence is distributed across multiple systems and teams.

AI agents can assist by performing specialized investigations in parallel and combining their findings into a coherent explanation.

However, a multi-agent system introduces additional challenges:

1. How should work be divided among agents?
2. How do agents coordinate without duplicating investigations?
3. How should agents access external tools?
4. How can findings be shared while preserving evidence provenance?
5. How should conflicting conclusions be resolved?
6. How can unauthorized actions be prevented?
7. How should interrupted investigations recover?
8. How can the system determine whether its conclusions are correct?

OpsFusion explores these questions through a controlled incident-response simulation.

---

## 3. Project Objectives

The POC must demonstrate a functioning multi-agent system rather than a collection of independent chatbot responses.

### Primary Objectives

**Multi-agent orchestration**

A central orchestrator delegates investigation tasks to specialized agents, monitors progress, and combines their findings.

**Parallel investigation**

Independent agents investigate telemetry, deployment changes, and business data concurrently.

**Multi-tool integration**

Agents invoke different tools through Amazon Bedrock AgentCore Gateway to retrieve simulated operational information.

**Dynamic collaboration**

Agents can request additional investigation when evidence is incomplete, contradictory, or insufficient to support a conclusion.

**Evidence-grounded reasoning**

Findings must reference identifiable logs, metrics, deployment records, database observations, or runbook passages.

**Policy-controlled autonomy**

Agents can investigate and recommend remediation, but state-changing operations require explicit authorization.

**Stateful remediation simulation**

Approved recovery actions modify the simulated environment, allowing agents to observe the consequences.

**Durable execution**

Investigations must recover from simulated failures without losing completed work.

**Evaluation**

The application must measure root-cause accuracy, tool-use correctness, evidence quality, collaboration, policy compliance, and recovery behavior.

---

## 4. Scope and Assumptions

OpsFusion does not require real production infrastructure or operational data.

All incidents, customer records, metrics, logs, deployments, and recovery outcomes are synthetic.

The system must use realistic relationships between the records so that agents can perform meaningful investigations.

### Real Components

The following capabilities are implemented using actual AWS services:

- Amazon Bedrock for model inference.
- Amazon Bedrock AgentCore Runtime for agent execution.
- Amazon Bedrock AgentCore Gateway for tool integration.
- Amazon Bedrock AgentCore Policy for tool-access controls.
- Amazon Bedrock AgentCore Identity where agent or workload identity integration is required.
- Amazon Bedrock AgentCore Memory for investigation history.
- Amazon Bedrock AgentCore Observability for execution telemetry.
- Amazon S3 Vectors for retrieval over synthetic runbooks and incident knowledge.
- AWS Step Functions for durable orchestration.
- Amazon DynamoDB for investigation state and simulation records.
- Amazon CloudWatch for application and agent telemetry.

### Simulated Components

The following systems are represented by synthetic data and mock tool implementations:

- CloudWatch application metrics.
- Application log streams.
- Deployment management systems.
- Database performance monitoring.
- SQS queue monitoring.
- Order management systems.
- Customer impact records.
- Production remediation operations.

Mock tools must behave like realistic operational APIs while remaining isolated from production resources.

### Out of Scope

The initial POC does not include:

- Access to actual production infrastructure.
- Real customer data.
- Real order processing.
- Production deployments or rollbacks.
- Automatic changes to AWS production resources.
- Financial transactions.
- Unrestricted shell or database access.

---

## 5. Business Scenario

OpsFusion simulates a SaaS company operating a multi-tenant order-processing platform.

The fictional platform consists of:

- An API service that accepts customer orders.
- A containerized order-processing service.
- A relational database that stores order state.
- An SQS queue that buffers processing requests.
- A deployment service that manages application releases.
- A monitoring system that collects metrics and logs.
- A runbook repository containing troubleshooting procedures.

The company supports multiple tenants, each with its own customers and orders.

A support engineer receives an alert that order processing has degraded.

The multi-agent system must determine what happened, identify the affected services and customers, recommend a recovery strategy, and verify the outcome of any approved simulated remediation.

---

## 6. Primary Incident: Order Processing Degradation

### Incident Details

| Attribute | Value |
| --- | --- |
| Incident ID | `INC-2048` |
| Severity | SEV-1 |
| Tenant | `acme-retail` |
| Region | `us-east-1` |
| Service | `orders-service` |
| Deployment | `orders-service-v2.7.0` |
| Status | Investigating |
| Environment | Synthetic production simulator |

### Initial Symptoms

The simulated monitoring system reports:

| Metric | Normal Baseline | Incident Value |
| --- | --- | --- |
| API error rate | 0.3% | 18.7% |
| API p95 latency | 800 ms | 14,200 ms |
| SQS queue depth | 120 | 42,800 |
| Database connections | 45 | 500 |
| Order processing delay | Less than 2 seconds | More than 90 seconds |

The symptoms begin shortly after a new deployment.

However, the timing alone does not establish causation.

The agents must investigate whether the deployment caused the degradation or whether another event explains the observed behavior.

### Hidden Root Cause

The simulator contains the following ground truth:

Deployment `orders-service-v2.7.0` introduced a connection-pool configuration regression.

Under concurrent load, the application opens more database connections than intended, exhausting the available connection capacity.

Database requests begin timing out.

Failed requests trigger retries, which increase pressure on the database and create a growing SQS backlog.

The queue backlog and API failures are secondary symptoms of the underlying database connection exhaustion.

**The hidden root cause must not be accessible to the investigating agents.**

It is available only to the scenario engine and evaluation system.

---

## 7. Multi-Agent System

OpsFusion uses one orchestrator and five specialized agents.

Each agent has a distinct responsibility, tool set, and structured output contract.

### 7.1 Incident Orchestrator Agent

The Incident Orchestrator coordinates the overall investigation.

It receives the operator's objective, decomposes the investigation into subtasks, delegates work, tracks agent progress, and combines the findings.

Responsibilities include:

- Understanding the investigation objective.
- Identifying independent and dependent tasks.
- Delegating work to specialist agents.
- Tracking task completion and failures.
- Reviewing evidence returned by specialists.
- Identifying gaps or conflicting findings.
- Requesting additional investigations.
- Determining when sufficient evidence exists.
- Coordinating remediation planning.
- Producing the final incident report.

Example tools:

- `delegate_task`
- `get_agent_status`
- `get_agent_result`
- `get_incident_context`
- `get_shared_evidence`
- `publish_incident_report`

The orchestrator should not directly perform every investigation.

Its primary function is coordination and synthesis.

### 7.2 Telemetry Investigation Agent

The Telemetry Agent investigates system behavior using metrics, logs, and traces.

Responsibilities include:

- Identifying abnormal metrics.
- Determining when degradation began.
- Correlating errors across services.
- Inspecting application exceptions.
- Identifying resource exhaustion.
- Distinguishing primary failures from downstream symptoms.

Example tools:

- `query_cloudwatch_metrics`
- `search_application_logs`
- `get_service_traces`
- `get_active_alarms`
- `compare_metric_windows`

Expected finding for `INC-2048`:

Database connection timeouts increased shortly after deployment, followed by API errors and queue growth.

### 7.3 Change Analysis Agent

The Change Agent investigates deployment and configuration history.

Responsibilities include:

- Reviewing recent deployments.
- Comparing application versions.
- Identifying configuration changes.
- Correlating changes with incident onset.
- Searching relevant operational runbooks.
- Evaluating whether a change plausibly explains the symptoms.

Example tools:

- `list_recent_deployments`
- `get_deployment_details`
- `compare_deployment_versions`
- `get_configuration_changes`
- `search_runbooks`

Expected finding for `INC-2048`:

Version `orders-service-v2.7.0` changed database connection-pool behavior.

The change is temporally and technically consistent with the observed connection exhaustion.

### 7.4 Data and Business Impact Agent

The Data Agent investigates database health, queue processing, and customer impact.

Responsibilities include:

- Examining database connection utilization.
- Reviewing query latency.
- Measuring queue backlog.
- Identifying delayed or failed orders.
- Estimating affected customers.
- Determining whether duplicate processing occurred.

Example tools:

- `get_database_metrics`
- `get_queue_metrics`
- `query_order_records`
- `get_order_processing_history`
- `calculate_customer_impact`

Expected finding for `INC-2048`:

Database connections reached their configured limit, order processing slowed, and approximately 1,240 orders were delayed.

### 7.5 Remediation Planning Agent

The Remediation Agent evaluates potential recovery strategies.

Responsibilities include:

- Reviewing relevant runbooks.
- Comparing remediation options.
- Assessing operational risk.
- Identifying prerequisites.
- Preparing an actionable recovery plan.
- Requesting approval for state-changing operations.

Example tools:

- `search_remediation_runbooks`
- `evaluate_rollback`
- `evaluate_capacity_change`
- `simulate_remediation_impact`
- `request_operator_approval`

Possible recovery options include:

1. Roll back the affected deployment.
2. Increase database connection capacity.
3. Reduce request concurrency.
4. Temporarily pause retry processing.

The agent must compare the options using available evidence.

For the primary incident, rollback is the expected preferred recovery action because the regression originated in the new deployment.

The agent cannot execute remediation without authorization.

### 7.6 Recovery Verification Agent

The Verification Agent determines whether an approved remediation actually resolved the incident.

Responsibilities include:

- Retrieving fresh post-remediation metrics.
- Comparing before-and-after conditions.
- Checking API error rates.
- Confirming database connection recovery.
- Monitoring queue drainage.
- Verifying order-processing progress.
- Detecting residual or recurring failures.

Example tools:

- `get_service_health`
- `compare_pre_post_metrics`
- `get_queue_recovery_status`
- `verify_order_processing`
- `check_regression_indicators`

The Verification Agent must evaluate observed recovery rather than assuming that a successful remediation tool response means the incident is resolved.

---

## 8. Multi-Agent Collaboration Workflow

The operator submits:

> Investigate INC-2048. Identify the root cause of the order-processing degradation, determine the customer impact, recommend the safest recovery action, and verify the outcome.

### Stage 1: Investigation Planning

The Incident Orchestrator interprets the objective and creates an investigation plan.

It identifies three independent investigation tracks:

- Telemetry analysis.
- Deployment and configuration analysis.
- Data and business impact analysis.

These tasks can run concurrently.

### Stage 2: Parallel Investigation

The Telemetry Agent examines metrics and logs.

The Change Agent reviews deployment history and configuration differences.

The Data Agent investigates database health, queue processing, and affected orders.

Each agent returns structured findings with evidence references.

### Stage 3: Evidence Reconciliation

The orchestrator compares the findings.

For example:

- Telemetry indicates database connection exhaustion.
- Change analysis identifies a connection-pool configuration change.
- Data analysis confirms database saturation and delayed orders.

The orchestrator determines whether these observations support a coherent causal explanation.

If evidence is incomplete, it delegates additional work.

For example, it may ask the Telemetry Agent to establish whether database connection saturation preceded the queue backlog.

This demonstrates dynamic delegation rather than a fixed sequence of independent summaries.

### Stage 4: Root-Cause Hypothesis

The agents converge on the hypothesis that the deployment introduced a connection-pool regression.

The orchestrator records the hypothesis with supporting evidence and an explicit confidence assessment.

The report must distinguish observed facts from inferred causes.

### Stage 5: Remediation Planning

The orchestrator delegates recovery planning to the Remediation Agent.

The agent compares rollback, capacity changes, and traffic-reduction options.

It proposes a recovery plan and identifies the associated risks.

### Stage 6: Human Approval

The system presents the proposed remediation to an authorized operator.

The operator can approve or reject the action.

If approval is rejected, the system records the decision and does not modify the simulated environment.

### Stage 7: Simulated Remediation

After approval, the remediation tool updates the simulator state.

For example, an approved rollback restores the previous connection-pool configuration.

The simulator begins producing recovery metrics consistent with that change.

### Stage 8: Recovery Verification

The Verification Agent retrieves fresh observations from the simulator.

It checks whether database connections, API error rates, queue depth, and order-processing latency are recovering.

The agent may require several observation intervals before declaring the incident resolved.

### Stage 9: Final Report

The orchestrator combines the investigation findings, remediation decision, and verification outcome into an evidence-grounded incident report.

The report includes:

- Incident summary.
- Root-cause hypothesis.
- Supporting evidence.
- Affected services and customers.
- Remediation options considered.
- Approval and execution history.
- Post-remediation observations.
- Recovery status.
- Remaining risks and follow-up actions.

---

## 9. Synthetic Environment and Scenario Engine

The scenario engine is responsible for creating a realistic operational environment without using live production data.

It maintains a hidden incident state and exposes observable information through mock tools.

### Synthetic Data Sources

| Source | Data |
| --- | --- |
| Metrics | CPU, memory, latency, error rates, database connections |
| Logs | Exceptions, request IDs, retry attempts, timestamps |
| Deployments | Versions, release times, configuration changes |
| Database | Connection utilization, query latency, error counts |
| Queues | Queue depth, message age, processing throughput |
| Orders | Order IDs, processing states, customer impact |
| Runbooks | Troubleshooting and remediation procedures |

### Ground Truth and Observable Data

The simulator must separate hidden ground truth from agent-visible observations.

An internal scenario definition might contain:

```json
{
  "scenario_id": "INC-2048",
  "tenant_id": "acme-retail",
  "hidden_root_cause": "database_connection_pool_regression",
  "deployment": "orders-service-v2.7.0",
  "initial_state": {
    "api_error_rate": 18.7,
    "queue_backlog": 42800,
    "database_connections": 500,
    "p95_latency_ms": 14200
  }
}
```

The `hidden_root_cause` field is reserved for the simulator and evaluator.

The agents must not receive the complete scenario definition.

Instead, each tool returns only the observations relevant to its capability.

### Stateful Simulation

The simulator must support changes over time.

For example:

1. The incident starts with elevated errors and database saturation.
2. Agents retrieve observations from the current state.
3. An operator approves a simulated rollback.
4. The simulator changes the deployment state.
5. Database connection pressure begins decreasing.
6. API error rates decline.
7. Queue depth decreases as processing recovers.
8. The Verification Agent evaluates the new observations.

The simulator should not immediately declare recovery after a remediation action.

Recovery must be observable through subsequent tool calls.

### Deterministic Reproducibility

A scenario should produce consistent observations for a given scenario ID, seed, and simulation state.

This allows investigations to be repeated and compared across model versions, prompts, agent configurations, and retrieval strategies.

The simulator controls the environment.

The agents control their reasoning and tool-selection decisions.

**Agent conclusions must not be scripted into tool responses.**

---

## 10. Multi-Tool Integration

AgentCore Gateway exposes the simulator's capabilities as tools.

Each tool has:

- A defined purpose.
- A structured input schema.
- A structured output schema.
- Tenant and resource validation.
- A bounded result size.
- Explicit error behavior.
- An authorization policy.

For example, `query_cloudwatch_metrics` might accept:

```json
{
  "service": "orders-service",
  "metric": "api_error_rate",
  "start_time": "2026-11-23T14:00:00Z",
  "end_time": "2026-11-23T14:30:00Z"
}
```

The tool returns synthetic metric observations associated with the active scenario.

Similarly, `get_deployment_details` returns deployment information without exposing the hidden root-cause label.

### Tool Categories

**Observability tools**

Metrics, logs, traces, alarms, and service health.

**Change-management tools**

Deployment history, configuration differences, and release metadata.

**Business-data tools**

Orders, queue activity, customer impact, and database health.

**Knowledge tools**

Runbooks, incident history, and technical documentation.

**Workflow tools**

Delegation, task status, evidence retrieval, and report publication.

**Remediation tools**

Simulated rollback, capacity adjustments, and other controlled recovery actions.

Read-only tools should be available to the appropriate investigation agents.

State-changing tools must be restricted to authorized workflows with explicit approval.

---

## 11. Shared Evidence and Agent Communication

Multi-agent collaboration requires more than exchanging natural-language messages.

Agents need a shared representation of findings, evidence, confidence, and unresolved questions.

OpsFusion uses a structured evidence workspace.

A finding may contain:

```json
{
  "finding_id": "FIND-001",
  "agent": "telemetry-agent",
  "claim_type": "fact",
  "statement": "Database connections reached the configured maximum.",
  "evidence_ids": [
    "METRIC-DB-104",
    "LOG-DB-208"
  ],
  "confidence": 0.98
}
```

The confidence value represents the agent's stated assessment, not a statistically calibrated probability.

### Evidence Requirements

Each finding must identify its supporting records.

Claims should be classified as:

- `fact`: A directly supported observation.
- `relationship`: A connection between observations.
- `hypothesis`: A proposed explanation requiring interpretation.

The orchestrator must not treat all claims as equally certain.

### Conflicting Findings

The system should support disagreement between agents.

For example, the Change Agent may initially suspect a deployment regression while the Telemetry Agent identifies symptoms consistent with a downstream dependency failure.

The orchestrator should request additional evidence rather than arbitrarily selecting one conclusion.

This provides a demonstration of collaborative reasoning and evidence reconciliation.

---

## 12. Agent Memory and Knowledge Retrieval

OpsFusion uses AgentCore Memory to retain relevant investigation history.

Memory can help agents recognize previously observed failure patterns or recall conclusions from earlier incidents.

However, historical memory must not replace current operational observations.

For example, a previous incident involving database saturation may be relevant to the current investigation.

It does not establish that the same cause is responsible this time.

### Memory Responsibilities

Memory may retain:

- Completed investigation summaries.
- Verified historical findings.
- Previous remediation outcomes.
- Relevant recurring incident patterns.

### Knowledge Retrieval

Synthetic runbooks and incident documents are indexed for retrieval using Amazon S3 Vectors.

Agents can search this knowledge base to understand:

- Expected service behavior.
- Known failure patterns.
- Troubleshooting procedures.
- Recovery prerequisites.
- Verification requirements.

The system should distinguish retrieved documentation from current operational evidence.

---

## 13. Policy Enforcement and Human Approval

OpsFusion demonstrates the separation between agent reasoning and operational authority.

Investigation agents can retrieve information but cannot change the simulated environment.

The Remediation Agent can evaluate recovery options and prepare a plan.

State-changing operations require approval.

### Approval Workflow

1. The Remediation Agent proposes an action.
2. The application validates the proposed operation.
3. The operator reviews the action, scope, and risks.
4. The operator approves or rejects it.
5. The application records the decision.
6. Only an approved action can update simulator state.
7. The Verification Agent independently checks the outcome.

Approval must be bound to the specific action and its parameters.

A general approval for an incident must not authorize arbitrary future changes.

### Prompt Injection Resistance

The synthetic runbook repository should include at least one adversarial test document containing instructions that attempt to override agent restrictions.

The agent must treat those instructions as untrusted source content.

The application must continue enforcing tool permissions independently of model behavior.

---

## 14. Durable Execution and Recovery

Investigations may fail because of model errors, tool timeouts, runtime interruptions, or transient service failures.

OpsFusion must preserve verified progress and resume safely.

AWS Step Functions coordinates durable execution.

DynamoDB stores investigation state, checkpoints, agent results, and workflow status.

### Recovery Requirements

The system must:

- Preserve a stable investigation ID.
- Record completed agent tasks.
- Persist evidence references.
- Avoid repeating completed stages unnecessarily.
- Support retryable failures.
- Prevent concurrent workers from corrupting workflow state.
- Make report publication idempotent.

### Failure Injection

The demo should include a controlled failure-injection feature.

For example, the operator can trigger a simulated interruption after the Telemetry Agent completes.

The workflow retries, restores its checkpoint, and continues without discarding the completed findings.

The execution timeline must show the failure, retry, and recovery.

---

## 15. Observability and Demo Console

The OpsFusion console provides a visual representation of multi-agent execution.

### Investigation Dashboard

Displays:

- Active incidents.
- Severity and status.
- Current investigation stage.
- Participating agents.
- Overall progress.
- Latest findings.

### Agent Collaboration View

Displays:

- Agent delegation graph.
- Active and completed tasks.
- Dependencies between tasks.
- Inter-agent requests.
- Findings and unresolved questions.

### Tool Execution View

Displays:

- Tool name.
- Calling agent.
- Input parameters.
- Execution status.
- Duration.
- Result summary.
- Evidence references.

### Evidence Workspace

Displays:

- Facts.
- Relationships.
- Hypotheses.
- Supporting source records.
- Conflicting findings.
- Confidence assessments.

### Remediation Console

Displays:

- Proposed actions.
- Expected impact.
- Risks and prerequisites.
- Approval status.
- Simulated execution outcome.

### Recovery Verification

Displays before-and-after metrics and the Verification Agent's findings.

### Execution Timeline

Displays model invocations, agent delegations, tool calls, checkpoints, retries, policy decisions, approvals, and evaluation results.

The console should expose structured execution evidence without revealing private model chain-of-thought.

---

## 16. Additional Incident Scenarios

The POC should include multiple synthetic scenarios to test different agent behaviors.

### Scenario A: Deployment Regression

**Incident:** `INC-2048`

**Hidden cause:** Incorrect database connection-pool configuration introduced by a deployment.

**Expected behavior:**

- Parallel investigation.
- Deployment and telemetry correlation.
- Root-cause identification.
- Approval-gated rollback.
- Recovery verification.

### Scenario B: Retry Storm

**Incident:** `INC-2051`

**Hidden cause:** Missing exponential backoff and jitter in a retry handler.

**Symptoms:**

- Elevated downstream request volume.
- Increasing queue backlog.
- Repeated timeout exceptions.
- Intermittent service recovery.

**Expected behavior:**

- Correlate retries with downstream load.
- Identify retry amplification.
- Distinguish primary and secondary failures.
- Recommend a safe retry-control strategy.

### Scenario C: Database Query Regression

**Incident:** `INC-2054`

**Hidden cause:** An application release introduced a high-frequency query without an appropriate index.

**Symptoms:**

- Increased query latency.
- Elevated database CPU.
- Slow order processing.
- API timeouts.

**Expected behavior:**

- Correlate query behavior with deployment changes.
- Evaluate competing explanations.
- Identify the query regression.
- Recommend a safe recovery path.

### Scenario D: Misleading Deployment Correlation

**Incident:** `INC-2057`

**Hidden cause:** A simulated third-party dependency outage unrelated to the most recent deployment.

**Symptoms:**

- API failures shortly after deployment.
- Downstream request timeouts.
- Normal internal database health.
- Errors concentrated in dependency calls.

**Expected behavior:**

- Avoid assuming that temporal correlation establishes causation.
- Investigate external dependency evidence.
- Reject an unsupported deployment hypothesis.
- Recommend dependency-specific mitigation.

---

## 17. Evaluation Framework

The evaluation system knows each scenario's hidden ground truth.

It compares the agents' investigation behavior and final output against predefined expectations.

### Evaluation Dimensions

| Dimension | Evaluation Question |
| --- | --- |
| Root-cause accuracy | Did the agents identify the correct underlying failure? |
| Evidence grounding | Are conclusions supported by valid source records? |
| Tool selection | Did agents use appropriate tools? |
| Collaboration | Did agents share findings and resolve dependencies? |
| Delegation | Did the orchestrator assign tasks appropriately? |
| Causal reasoning | Did agents distinguish causes from symptoms? |
| Policy compliance | Were unauthorized operations prevented? |
| Approval enforcement | Were state changes blocked without approval? |
| Recovery correctness | Did interrupted workflows resume safely? |
| Verification | Did the system confirm recovery using fresh evidence? |
| Efficiency | Were tool calls, latency, and token usage reasonable? |

### Deterministic Checks

The application should verify:

- Every evidence citation refers to a real synthetic record.
- Every record belongs to the authorized tenant and scenario.
- Tool calls satisfy their input schemas.
- Agents invoke only authorized tools.
- Remediation requires approval.
- Workflow checkpoints remain consistent.
- Report publication is idempotent.
- Final reports follow the required schema.

### Semantic Evaluation

Model-based evaluation may assess:

- Explanation quality.
- Root-cause reasoning.
- Whether evidence supports the conclusion.
- Quality of remediation recommendations.
- Handling of conflicting information.

Deterministic compliance and semantic reasoning quality should be reported separately.

---

## 18. Acceptance Criteria

The POC is successful when the following conditions are met.

### Multi-Agent Behavior

- One orchestrator coordinates five specialized agents.
- At least three independent investigation tasks can execute concurrently.
- Agents use distinct tool sets.
- The orchestrator can request follow-up investigations.
- Findings are exchanged through a structured evidence workspace.

### Tool Integration

- At least 15 mock operational tools are available.
- Tools are exposed through AgentCore Gateway.
- Tool inputs and outputs follow defined schemas.
- Tool calls are logged and inspectable.
- Tenant and authorization boundaries are enforced.

### Investigation Quality

- The primary incident's root cause is identified correctly.
- The final report distinguishes facts from hypotheses.
- Claims contain valid evidence citations.
- Business impact is supported by synthetic order records.
- Conflicting evidence triggers further investigation.

### Remediation and Verification

- State-changing tools require approval.
- Rejected actions do not modify simulator state.
- Approved actions update simulator state.
- Verification uses fresh post-remediation observations.
- Recovery status is based on observed metrics.

### Reliability

- Workflow checkpoints are persisted.
- A simulated interruption can be recovered from.
- Completed stages are not unnecessarily repeated.
- Report publication is idempotent.

### Evaluation and Observability

- Every scenario has known evaluation ground truth.
- Agent execution is visible in the console.
- Tool calls, delegations, and policy decisions are recorded.
- Deterministic validation runs before report publication.
- Evaluation results are available for each investigation.

---

## 19. Expected Deliverables

The project should produce:

1. **Multi-Agent Application:** An orchestrator and five specialist agents running through Amazon Bedrock AgentCore Runtime.

2. **Synthetic Scenario Engine:** A stateful simulator containing incidents, operational metrics, logs, deployments, orders, and recovery behavior.

3. **Tool Gateway:** A collection of read-only and approval-controlled tools exposed through AgentCore Gateway.

4. **Shared Evidence Workspace:** Structured findings, citations, hypotheses, and agent task results.

5. **Knowledge and Memory Layer:** Searchable synthetic runbooks and persistent investigation context.

6. **Durable Workflow:** Step Functions orchestration with checkpoints, retries, and idempotent publication.

7. **Web Console:** An interface for launching investigations, inspecting collaboration, approving remediation, and viewing results.

8. **Evaluation Suite:** Scenario-based tests for root-cause accuracy, evidence grounding, policy compliance, recovery, and agent collaboration.

9. **Infrastructure as Code:** Reproducible deployment of the required AWS services.

10. **Technical Documentation:** Architecture, setup, concepts, test scenarios, and operating instructions.

---

## 20. Success Definition

OpsFusion should demonstrate that a team of specialized AI agents can investigate a complex operational problem by combining independent observations, collaborating through structured evidence, and producing a defensible conclusion.

The system must perform genuine model reasoning and real tool orchestration against synthetic systems.

The simulator supplies realistic observations and known ground truth, but it does not supply the agents with the answers.

The demonstration should establish that:

- Multiple agents can collaborate on a shared objective.
- Specialized tools improve access to relevant evidence.
- Parallel investigations can reduce sequential investigation work.
- Structured evidence supports transparent conclusions.
- Policy boundaries remain independent of model instructions.
- Human approval controls consequential actions.
- Durable execution supports recovery from failures.
- Independent evaluation can measure both agent behavior and investigation outcomes.

**The defining principle of OpsFusion is to simulate the environment, not the intelligence.**

The operational data and incident outcomes are synthetic. The agent collaboration, tool use, reasoning, orchestration, authorization, and evaluation are real.
