# OpsFusion Implementation Plan

> **Current implementation scope (2026-11-24):** The deployed console is
> analysis-only. It gathers evidence, closes evidence gaps, and evaluates the
> unchanged simulator snapshot, then stops. The remediation, approval,
> recovery, and report-publication sections below are retained as historical
> design context and are not executable product behavior.

This plan turns [`CASE_STUDY.md`](CASE_STUDY.md) into an implementable,
testable, and deployable proof of concept.

The plan follows the deployment approach in the existing
[`agent-harness-demo`](../agent-harness-demo/DEPLOYMENT.md): a manifest-driven
deployment, independent CloudFormation/SAM stacks, persisted stack outputs,
targeted retries, and a separate post-deployment AgentCore provisioning step.

## 1. Goal and Definition of Done

Build a working OpsFusion demonstration in which specialized agents investigate
synthetic incidents using real model inference and real tool calls, exchange
structured evidence, propose approval-gated remediation, recover from an
interruption, and produce an independently evaluated incident report.

The project is complete when:

- The primary `INC-2048` investigation runs end to end.
- At least three investigation tracks execute concurrently.
- One orchestrator coordinates five specialist agents.
- At least 15 bounded operational tools are exposed through AgentCore Gateway.
- AWS Agent Registry contains approved records for the OpsFusion agents,
  Gateway/MCP tools, and reusable skills.
- The hidden root cause is available only to the simulator and evaluator.
- Every material claim in the final report has validated evidence references.
- Remediation cannot change state without action-specific approval.
- Approved remediation changes simulator state and produces observable recovery.
- Verification uses fresh post-remediation observations.
- A controlled interruption resumes from a persisted checkpoint.
- The console exposes structured execution evidence without exposing private
  model chain-of-thought.
- All four scenarios have deterministic checks and expected outcomes.
- The application can be deployed and retried using the documented manifest
  workflow.

## 2. Guiding Principles

1. **Simulate the environment, not the intelligence.** Tool responses provide
   realistic observations but never the answer, a conclusion, or the hidden
   root-cause label.
2. **Separate facts from interpretation.** Findings are explicitly classified
   as facts, relationships, or hypotheses.
3. **Preserve evidence provenance.** Every finding points to immutable,
   tenant-scoped source records.
4. **Keep authority outside the model.** Tool permissions, approval checks,
   tenant boundaries, and state transitions are enforced by application code
   and AWS policies.
5. **Make time and state explicit.** Recovery is observed over subsequent
   simulation intervals rather than reported immediately after a write.
6. **Prefer durable, idempotent operations.** Retries must not duplicate
   remediation, corrupt workflow state, or publish multiple reports.
7. **Expose execution, not private reasoning.** The console shows prompts by
   version, tool calls, outputs, evidence, decisions, and status—not hidden
   chain-of-thought.
8. **Keep every run reproducible.** A scenario ID, seed, and simulation state
   must be sufficient to recreate observable results.
9. **Use Registry for discovery and governance.** Registry approval controls
   which capabilities are discoverable; it does not grant runtime permission
   or replace remediation approval.

## 3. Proposed Repository Layout

Use a Python backend and infrastructure layer, with a React/TypeScript web
console, matching the shape that has already worked in `agent-harness-demo`.

```text
multi-agent-demo/
├── CASE_STUDY.md
├── PLAN.md
├── README.md
├── pyproject.toml
├── requirements.txt
├── deploy-all.sh
├── apps/
│   ├── api/                    # authenticated application API
│   └── web/                    # React console
├── agents/
│   ├── orchestrator.py
│   ├── telemetry.py
│   ├── change_analysis.py
│   ├── data_impact.py
│   ├── remediation.py
│   └── verification.py
├── contracts/                  # JSON Schema/Pydantic contracts
├── simulator/
│   ├── engine.py
│   ├── fixtures.py
│   ├── models.py
│   ├── state.py
│   ├── tools.py
│   └── api.py
├── evidence/
│   ├── models.py
│   ├── store.py
│   └── validation.py
├── workflow/
│   ├── state_machine.asl.json
│   ├── handlers.py
│   └── checkpoints.py
├── gateway/
│   ├── handler.py
│   └── tool_catalog.py
├── knowledge/
│   ├── runbooks/
│   ├── index.py
│   └── retrieval.py
├── memory/
│   └── adapter.py
├── evaluation/
│   ├── deterministic.py
│   ├── semantic.py
│   ├── runner.py
│   └── scorecard.py
├── scenarios/
│   ├── definitions/
│   └── expected_outcomes/
├── infrastructure/
│   └── cloudformation/
│       ├── deployment.yaml
│       ├── orchestrate.py
│       ├── sam_deploy.py
│       └── stacks/
├── docs/
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    ├── evaluation/
    └── load/
```

The exact directory names can change during implementation, but the
responsibilities and boundaries should remain stable.

## 4. Architecture and Contract Work

Complete these contracts before implementing model-driven behavior.

### 4.1 Core domain models

Define versioned schemas for:

- `ScenarioDefinition`: hidden ground truth, seed, initial state, transitions,
  and evaluator-only metadata.
- `ScenarioRun`: scenario ID, run ID, tenant, actor, seed, current state,
  simulation clock, and failure-injection settings.
- `ObservableRecord`: source, record ID, timestamp, tenant, scenario run,
  payload, and provenance metadata.
- `AgentTask`: task ID, assigned agent, objective, dependencies, status,
  attempts, timestamps, and result reference.
- `Finding`: finding ID, agent, claim type, statement, evidence IDs,
  confidence, and status.
- `EvidenceReference`: source type, source record ID, query parameters,
  observed timestamp, and integrity metadata.
- `Hypothesis`: statement, supporting and contradicting finding IDs,
  confidence, and unresolved questions.
- `ApprovalRequest`: exact action, exact parameters, scope, requester,
  expiration, approval status, and decision audit record.
- `RemediationExecution`: approval ID, idempotency key, state transition,
  execution status, and observed result.
- `InvestigationState`: durable stage, checkpoints, agent results, evidence
  version, retry state, and report publication state.
- `IncidentReport`: facts, hypotheses, customer impact, remediation history,
  verification observations, risks, and evaluation references.
- `EvaluationResult`: deterministic checks, semantic scores, failures,
  latency/cost metrics, and scenario ground-truth comparison.

### 4.2 Common envelopes

All tool and agent responses should use versioned envelopes containing:

- `request_id`
- `run_id`
- `scenario_id`
- `tenant_id`
- `tool_or_agent`
- `observed_at`
- `status`
- `data`
- `evidence_ids`
- `errors`

Reject requests with a mismatched tenant, scenario, run, or unsupported schema
version. Bound response sizes and redact secrets before persistence.

### 4.3 State and authority boundaries

Keep these data classes separate:

- **Hidden state:** ground truth and evaluator-only expected outcomes.
- **Observable state:** metrics, logs, deployments, database records, queues,
  orders, runbooks, and service health.
- **Workflow state:** task progress, checkpoints, approvals, and report status.
- **Evaluation state:** expected outcome comparisons and scores.

The runtime agent role must not be able to read hidden scenario definitions or
evaluator-only tables.

### 4.4 Agent Registry boundary

Use AWS Agent Registry as the catalog and governance layer for the agent team.
For a new implementation, use the current `agent-registry` namespace and
follow the official [Registry documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html).

Register records for:

- The Incident Orchestrator agent.
- The five specialist agents.
- The AgentCore Gateway/MCP server and its tool capabilities.
- Reusable skills such as telemetry triage, change analysis, safe
  remediation, and recovery verification.

The Registry lifecycle should be:

1. Create or synchronize a record.
2. Validate its protocol, metadata, owner, version, and permissions.
3. Submit it for approval.
4. Make only approved records discoverable to runtime consumers.
5. Deprecate records that are no longer supported.

The initial OpsFusion workflow should retain a static role-to-capability
allowlist for reproducibility. Registry discovery can resolve approved
metadata and versions, but the application must still validate the selected
record against the current investigation, tenant, and policy boundary before
invocation. A Registry approval is not approval to change simulator state;
that remains a separate, action-specific operator approval.

## 5. Delivery Phases

### Phase 0 — Repository foundation and contracts

Tasks:

- Create the Python project, dependency lock strategy, formatting, linting,
  and test configuration.
- Add environment configuration with safe local defaults.
- Implement the domain schemas and common envelopes.
- Add a local in-memory persistence adapter for fast tests.
- Add a DynamoDB/S3 persistence interface without requiring AWS for unit tests.
- Add structured logging with run, task, request, agent, and tool identifiers.
- Record architecture decisions for state ownership, agent execution, and
  deployment boundaries.
- Add a README explaining local setup and the synthetic-only safety boundary.

Exit criteria:

- Schemas validate valid and invalid fixtures.
- Unit tests run without AWS credentials.
- Hidden fields cannot appear in observable response serializers.
- A local run can be created and queried by ID.

### Phase 1 — Deterministic synthetic scenario engine

Tasks:

- Implement the scenario model and seeded fixture generator.
- Implement `INC-2048` with the documented baseline and incident values.
- Generate coherent metrics, logs, traces, deployments, database records,
  queue messages, orders, and customer-impact records.
- Keep the connection-pool regression only in hidden ground truth and
  observable configuration/history, never as a label returned by a tool.
- Implement simulated time and observation intervals.
- Implement state transitions for approved rollback, including gradual metric
  recovery, queue drainage, and order progress.
- Implement deterministic record IDs and stable timestamps.
- Implement a controlled interruption flag at named workflow checkpoints.
- Add scenarios `INC-2051`, `INC-2054`, and `INC-2057` with distinct causal
  relationships.

Tool-response rule:

- Tools return observations and source metadata, not conclusions. For example,
  a database tool may return `connections=500` and `max_connections=500`, a
  deployment tool may return a connection-pool configuration diff, and a log
  tool may return timeout records. None of those responses should contain
  `root_cause=database_connection_pool_regression`, “the deployment caused the
  incident,” or “rollback is the answer.”
- The agents must connect those observations, weigh alternatives, and create
  the relationship or hypothesis. This is what makes the reasoning and
  collaboration real rather than scripted.
- Tools may return direct facts, timestamps, identifiers, and relevant
  documentation. “Never provide the answer” means not embedding the
  evaluator's conclusion or an agent-generated causal interpretation in the
  tool response; it does not mean hiding useful evidence.

Exit criteria:

- The same scenario ID, seed, and state produce the same observations.
- `INC-2048` has internally consistent secondary symptoms.
- Recovery does not become healthy immediately after a remediation write.
- No agent-visible response contains `hidden_root_cause` or evaluator data.
- Scenario fixtures have tests for tenant isolation and record ownership.

### Phase 2 — Operational tool catalog and simulator API

Implement the tools as ordinary typed functions first, then expose the same
contracts through the API and AgentCore Gateway adapter.

Initial tool catalog:

**Workflow and collaboration**

- `delegate_task`
- `get_agent_status`
- `get_agent_result`
- `get_incident_context`
- `get_shared_evidence`
- `publish_incident_report`

**Observability**

- `query_cloudwatch_metrics`
- `search_application_logs`
- `get_service_traces`
- `get_active_alarms`
- `compare_metric_windows`
- `get_service_health`

**Change analysis**

- `list_recent_deployments`
- `get_deployment_details`
- `compare_deployment_versions`
- `get_configuration_changes`
- `search_runbooks`

**Data and business impact**

- `get_database_metrics`
- `get_queue_metrics`
- `query_order_records`
- `get_order_processing_history`
- `calculate_customer_impact`

**Remediation and verification**

- `search_remediation_runbooks`
- `evaluate_rollback`
- `evaluate_capacity_change`
- `simulate_remediation_impact`
- `request_operator_approval`
- `execute_approved_remediation`
- `compare_pre_post_metrics`
- `get_queue_recovery_status`
- `verify_order_processing`
- `check_regression_indicators`

For every tool:

- Define a JSON input and output schema.
- Validate tenant, scenario, resource, and time-window scope.
- Return bounded results with stable evidence IDs.
- Log the caller, request, status, duration, and evidence references.
- Make read-only versus state-changing behavior explicit.
- Return structured errors for invalid scope, missing records, conflicts,
  rate limits, and simulated failures.
- Require an idempotency key for state-changing calls.

Exit criteria:

- At least 15 tools pass schema and authorization tests.
- Read-only tools cannot mutate simulator state.
- Direct calls and Gateway calls produce equivalent contracts.
- Unauthorized remediation is rejected before any state transition.

### Phase 3 — Shared evidence workspace

Tasks:

- Implement finding creation, update, and retrieval.
- Validate every evidence ID against the current scenario run and tenant.
- Store facts, relationships, and hypotheses separately.
- Track supporting and contradicting evidence.
- Add finding confidence without treating it as calibrated probability.
- Add evidence versioning so a later run cannot overwrite prior findings.
- Implement unresolved questions and follow-up request records.
- Make evidence writes idempotent by `(run_id, finding_id)`.
- Add report pre-publication validation.

Exit criteria:

- Invalid, cross-tenant, hidden, or nonexistent evidence citations are rejected.
- A finding can be reconstructed from its source records.
- Conflicting findings remain visible rather than being silently replaced.
- A report cannot publish with an unsupported root-cause claim.

### Phase 4 — AgentCore agent team

Implement one orchestrator and five specialized agents with distinct prompts,
tool permissions, and output contracts.

#### Orchestrator

- Accept the operator objective and incident context.
- Create the investigation plan.
- Delegate telemetry, change, and data tracks concurrently.
- Monitor task state and retry safe failures.
- Reconcile findings and identify evidence gaps.
- Request follow-up investigations when needed.
- Delegate remediation planning and recovery verification.
- Publish only a validated final report.

#### Telemetry Agent

- Use metrics, logs, traces, alarms, and metric-window comparisons.
- Establish incident onset and ordering of connection saturation, API errors,
  retries, and queue growth.
- Return evidence-backed facts and relationships.

#### Change Agent

- Review deployments, version differences, configuration changes, and
  runbooks.
- Evaluate temporal and technical consistency without asserting causation
  from timing alone.

#### Data and Business Impact Agent

- Inspect database and queue health.
- Query delayed, failed, and duplicate orders.
- Calculate affected customers and order counts with source references.

#### Remediation Agent

- Compare rollback, capacity, concurrency, and retry-control options.
- State prerequisites, risks, expected impact, and approval requirements.
- Never execute a state-changing tool directly.

#### Recovery Verification Agent

- Retrieve fresh observations after remediation.
- Compare pre- and post-remediation metrics.
- Confirm database recovery, API improvement, queue drainage, and order
  progress over multiple observation intervals.

Implementation requirements:

- Use structured output parsing with schema validation and bounded retries.
- Keep agent permissions narrow and role-specific.
- Record model/prompt version, tool calls, and result summaries.
- Do not persist or display private chain-of-thought.
- Support a local deterministic stub mode for contract tests.
- Use real AgentCore Harness invocations, backed by AgentCore Runtime and
  Gateway, for integration and demo runs. Do not implement a second custom
  model/tool loop in the application.
- Resolve approved agent, skill, and tool metadata from Agent Registry while
  enforcing the static role allowlist and application policy.

Exit criteria:

- The primary incident produces a defensible connection-pool regression
  hypothesis from current observations.
- At least three tracks run concurrently.
- The orchestrator can request a follow-up task when evidence is incomplete.
- Specialist agents cannot call tools outside their assigned catalog.

### Phase 5 — Durable workflow and recovery

Use AWS Step Functions for the durable investigation lifecycle and DynamoDB
for checkpoints, task state, evidence metadata, approvals, and publication
locks.

Recommended state-machine stages:

1. `InitializeRun`
2. `LoadIncidentContext`
3. `PlanInvestigation`
4. `ParallelInvestigation` with telemetry, change, and data branches
5. `ReconcileEvidence`
6. `RequestFollowUp` or `ApproveRootCauseHypothesis`
7. `PlanRemediation`
8. `WaitForOperatorApproval`
9. `ExecuteApprovedRemediation`
10. `ObserveRecovery`
11. `VerifyRecovery`
12. `ValidateReport`
13. `PublishReport`

Tasks:

- Define retryable versus terminal errors for each stage.
- Persist a checkpoint before and after every material transition.
- Use task tokens or an equivalent durable wait for human approval.
- Make every worker idempotent on `(run_id, stage, attempt)`.
- Prevent concurrent writers from losing findings or approval decisions.
- Add failure injection after Telemetry Agent completion.
- Resume the same investigation ID without re-running completed work.
- Lock report publication so retries return the original report.

Exit criteria:

- A forced interruption after telemetry resumes successfully.
- Completed telemetry findings are retained and not unnecessarily duplicated.
- Failed tool calls retry within policy and surface terminal errors clearly.
- Replaying a completed publication returns the same report ID and content.

### Phase 6 — Approval-gated remediation and recovery

Tasks:

- Define the supported simulated actions and exact parameter schemas.
- Generate a remediation comparison from evidence, not hardcoded conclusions.
- Bind approval to one action, one parameter set, one incident, and one
  expiration window.
- Validate the operator identity and authorization before accepting approval.
- Reject altered parameters, expired approvals, duplicate approvals, and
  approvals for another run.
- Apply only approved actions to simulator state.
- Record both rejected and approved decisions in the execution timeline.
- Advance simulated time after execution and expose recovery observations.
- Require the Verification Agent to make the final recovery determination.

Primary `INC-2048` path:

1. Recommend rollback as the preferred option.
2. Present impact and risk to the operator.
3. Confirm that rejection leaves simulator state unchanged.
4. On approval, restore the previous connection-pool behavior.
5. Observe falling connection pressure, errors, and queue depth.
6. Verify order-processing progress before resolving the incident.

Exit criteria:

- No approval means no state change.
- Approved rollback changes state exactly once.
- Verification can report recovering, resolved, or residual failure.
- A successful remediation API response alone cannot resolve the incident.

### Phase 7 — Knowledge, Registry, memory, identity, and policy

Tasks:

- Create synthetic runbooks for normal service behavior, database saturation,
  deployment rollback, queue recovery, and verification.
- Add at least one adversarial runbook containing an instruction that attempts
  to override agent restrictions.
- Store runbooks in S3 and index them with S3 Vectors.
- Return retrieval provenance and label retrieved text as untrusted content.
- Create an AWS Agent Registry for the OpsFusion environment.
- Register the orchestrator, five specialists, Gateway/MCP tool server, and
  reusable skills with owner, version, endpoint, and capability metadata.
- Add a manual or controlled CI approval step before records become
  discoverable.
- Add Registry search/discovery through the supported API or MCP endpoint.
- Test that draft, pending, rejected, and deprecated records are not selected
  by the runtime discovery path.
- Keep Registry discovery separate from operational remediation approval.
- Add AgentCore Memory for completed summaries, verified patterns, and prior
  remediation outcomes.
- Ensure historical memory never substitutes for current observations.
- Configure AgentCore Gateway targets and role-specific policies.
- Configure AgentCore Identity for the runtime/application boundary where
  required.
- Enforce read-only versus state-changing policy outside model instructions.
- Add policy decision and denied-call telemetry.

Exit criteria:

- Agents can retrieve relevant runbook passages with source references.
- The adversarial document cannot grant new permissions or bypass approval.
- Memory can inform an investigation but cannot establish current causation.
- Policy tests pass independently of model behavior.

### Phase 8 — Web console and application API

Build the console as a focused operator experience.

Pages and views:

- **Investigation dashboard:** active incidents, severity, stage, agents,
  progress, and latest findings.
- **Agent collaboration:** delegation graph, task dependencies, follow-ups,
  and agent statuses.
- **Tool execution:** caller, tool, sanitized inputs, status, duration,
  result summary, and evidence IDs.
- **Evidence workspace:** facts, relationships, hypotheses, sources,
  contradictions, confidence, and unresolved questions.
- **Remediation console:** options, impact, risks, prerequisites, approval,
  execution, and denial history.
- **Recovery verification:** before/after metrics, queue progress, order
  recovery, and verification findings.
- **Execution timeline:** model calls, delegations, tools, checkpoints,
  retries, policy decisions, approvals, failures, and evaluations.
- **Evaluation view:** deterministic checks, semantic scores, and failures.

Application API tasks:

- [x] Add Cognito ID-token authentication and operator authorization.
- [x] Configure invite-only Cognito users with the `opsfusion-admin` group.
- [x] Add a public login and first-time password-challenge exchange.
- [x] Restrict user invitations and approval decisions to administrators.
- [x] Serve the console from the API Function URL while keeping protected
  routes behind the application auth boundary.
- Add endpoints for scenario catalog, run creation, run status, findings,
  evidence, approvals, reports, and evaluation results.
- Support polling first; add streaming updates only after the core contract is
  stable.
- Keep all browser calls same-origin or use an explicit authenticated API URL.
- Never return hidden ground truth or private model reasoning.
- Sanitize tool parameters and outputs before rendering.

Exit criteria:

- An operator can launch `INC-2048`, inspect active work, approve/reject a
  remediation, and read the final report from the console.
- Timeline and evidence views are backed by persisted records rather than
  frontend-only state.
- Unauthorized UI actions are rejected by the API as well as hidden in the UI.

### Phase 9 — Evaluation suite and scenario coverage

Implement deterministic checks first and semantic evaluation second.

Deterministic checks:

- Evidence IDs exist and belong to the correct run, tenant, and scenario.
- Hidden root cause is absent from agent-visible tool responses.
- Tool inputs and outputs validate against schemas.
- Agent-to-tool permissions are respected.
- At least three investigation tracks execute concurrently.
- Remediation is approval-gated and idempotent.
- Rejected actions do not mutate simulator state.
- Checkpoints remain internally consistent after retry.
- Report publication is idempotent.
- Required report sections and claim classifications are present.

Semantic checks:

- Root-cause explanation matches the scenario ground truth.
- Evidence supports the causal chain.
- Causes are distinguished from secondary symptoms.
- Customer impact is accurately summarized.
- Remediation recommendation is safe and proportionate.
- Conflicting or insufficient evidence leads to further investigation.
- Verification is based on fresh observations.

Scenario expectations:

| Scenario | Primary evaluation |
| --- | --- |
| `INC-2048` | Connection-pool regression, rollback, recovery verification |
| `INC-2051` | Retry amplification and safe retry-control recommendation |
| `INC-2054` | Query/index regression and database evidence correlation |
| `INC-2057` | Reject deployment causation and identify dependency outage |

Add regression fixtures for incomplete evidence, contradictory findings,
tool timeout, model schema failure, denied approval, partial remediation, and
verification that remains unhealthy.

Exit criteria:

- Each scenario has hidden ground truth and expected outcomes.
- Scorecards separate compliance from reasoning quality.
- Evaluation results are stored and visible per run.
- A prompt or tool change can be compared against a prior evaluation set.

### Phase 10 — Infrastructure and deployment

Mirror the proven `agent-harness-demo` deployment pattern with an OpsFusion
manifest and independent stacks.

Recommended stack graph:

```text
00-foundation
└── 01-state
    ├── 02-simulator
    ├── 03-knowledge
    └── 04-agentcore
        └── 05-workflow
            └── 06-application
                └── 07-web

08-observability-evaluation depends on 01-state, 04-agentcore,
05-workflow, and 06-application.
```

Stack responsibilities:

- `00-foundation`: least-privilege IAM, KMS/logging prerequisites, artifact
  naming, and deployment roles.
- `01-state`: DynamoDB tables, scenario/run state, evidence, approvals,
  idempotency, S3 fixture/runbook buckets, and S3 Vectors resources.
- `02-simulator`: synthetic operational API, Lambda packaging, simulator
  state access, and Gateway target handler.
- `03-knowledge`: runbook objects, retrieval metadata, and vector index setup.
- `04-agentcore`: AgentCore Harness/Runtime configuration, Gateway, Registry
  records, policies, identity bindings, memory resources, and prompt versions.
- `05-workflow`: Step Functions state machine and durable worker functions.
- `06-application`: authenticated API, scenario/run endpoints, approval
  endpoints, report/evaluation access, audit events, Cognito User Pool, and
  admin-only user management.
- `07-web`: private versioned web artifact bucket and console hosting path.
- `08-observability-evaluation`: log groups, dashboards, alarms, traces,
  evaluation workers, and metric filters.

Deployment mechanics:

- Create `deploy-all.sh` as the only top-level deployment entry point.
- Define stack order, dependencies, and lifecycle hooks in
  `infrastructure/cloudformation/deployment.yaml`.
- Use a Python orchestrator to run tests, validate contracts, check AWS
  identity, build the web bundle, deploy stacks, publish web assets, and run
  optional smoke checks.
- Use a per-environment deployment context such as
  `infrastructure/cloudformation/build/deployment-context-dev.json`.
- Persist CloudFormation outputs as `CF_OUT_*` values for downstream stacks.
- Rehydrate predecessor outputs from CloudFormation for targeted retries.
- Deploy SAM-transformed stacks through `sam build` and `sam deploy`.
- Deploy non-SAM templates through `aws cloudformation deploy`.
- Support `--stack` for a single stack and `--from` for a stack plus all
  downstream dependents.
- Use unique release IDs and stable artifact prefixes.
- Provision/rebind AgentCore resources and reconcile Registry records after
  CloudFormation outputs exist. Keep record approval explicit; deployment must
  not silently publish an unreviewed agent, skill, or tool.
- Provide deployment-only verification and optional web/harness smoke tests.

Full deployment shape:

```bash
AWS_PROFILE=YOUR_AWS_PROFILE AWS_REGION=us-east-1 \
  ./deploy-all.sh \
    --environment dev \
    --release-id 20261123-opsfusion-initial \
    --resolve-s3
```

Targeted update shape:

```bash
AWS_PROFILE=YOUR_AWS_PROFILE AWS_REGION=us-east-1 \
  ./deploy-all.sh \
    --environment dev \
    --from 04-agentcore \
    --release-id 20261123-opsfusion-agent-update \
    --resolve-s3
```

Post-deployment provisioning should be a separate, explicit command so stack
deployment does not accidentally invoke a model or incur inference cost.

Exit criteria:

- A clean environment can be deployed from the documented commands.
- A single stack can be retried without destroying the context file.
- A downstream deployment hydrates predecessor outputs from AWS.
- The final console URL and deployment context are printed and persisted.
- AgentCore provisioning is repeatable and does not invoke the model by
  default.

### Phase 11 — Security, reliability, and demo hardening

Tasks:

- Review every IAM action and resource ARN for least privilege.
- Verify hidden ground truth is excluded from runtime roles, logs, API
  responses, prompts, and frontend bundles.
- Redact tokens, credentials, and sensitive payloads from logs.
- Add rate limits and bounded query windows to simulator tools.
- Add concurrency controls for approvals, remediation, evidence writes, and
  report publication.
- Add retention and cleanup behavior for synthetic runs and artifacts.
- Test repeated approvals, stale approvals, duplicate tool calls, retries,
  and partial remediation failures.
- Add cost controls: bounded model calls, tool-result limits, and optional
  local stub mode.
- Create a five-minute demo walkthrough using `INC-2048`.
- Document known limitations and clearly label all data as synthetic.

Exit criteria:

- Security and authorization tests pass.
- Failure injection and retry behavior are visible in the timeline.
- The demo can be repeated from a known seed without manual database edits.
- The operator can explain every state-changing action and its approval.

## 6. Milestones and Vertical Slices

Build in vertical slices so each milestone produces a demonstrable result.

### Milestone 0 — Contracts and local simulator

Deliver schemas, fixtures, deterministic simulator, and local tool functions.

Demo: query `INC-2048` metrics, deployment history, database state, queue
state, and order impact without any model calls.

### Milestone 1 — Read-only agent investigation

Deliver AgentCore Harness, Gateway, approved Registry records, orchestrator,
Telemetry Agent, Change Agent, and Data Agent.

Demo: three tracks run concurrently and produce structured evidence for a
root-cause hypothesis.

### Milestone 2 — Remediation and verification

Deliver Remediation Agent, approval workflow, simulated rollback, and
Verification Agent.

Demo: reject and approve paths visibly differ; approved rollback produces
gradual recovery.

### Milestone 3 — Durable execution

Deliver Step Functions checkpoints, retry behavior, failure injection, and
idempotent publication.

Demo: interrupt after telemetry, resume the same run, and complete without
discarding verified findings.

### Milestone 4 — Knowledge, memory, policy, and evaluation

Deliver S3 Vectors retrieval, AgentCore Memory, Agent Registry discovery and
approval tests, policy tests, adversarial runbook, deterministic checks, and
scorecards.

Demo: an untrusted runbook cannot bypass approval or broaden tool access.

### Milestone 5 — Console and deployment

Deliver the operator console, infrastructure manifest, deploy scripts,
observability, and technical documentation.

Demo: launch, inspect, approve, verify, and evaluate an incident from the
browser after a reproducible deployment.

## 7. Test Strategy

### Unit tests

- Scenario transitions and deterministic seeds.
- Hidden/observable projection boundaries.
- Tool schemas and validation.
- Evidence citation and tenant checks.
- Approval and idempotency rules.
- Agent output parsing.
- Checkpoint serialization.
- Evaluation scoring.

### Contract tests

- Every tool's JSON schema.
- Gateway adapter versus direct tool behavior.
- Agent result contracts.
- API request/response contracts.
- CloudFormation parameter and output contracts.

### Integration tests

- Local simulator with persistent state.
- DynamoDB/S3 adapters.
- AgentCore Gateway tool invocation.
- AgentCore Harness session and event-stream behavior.
- Agent Registry record creation, approval, search, and authorization.
- Step Functions worker transitions.
- Cognito/authenticated API boundary.

### End-to-end tests

- Full `INC-2048` investigation with rejection.
- Full `INC-2048` investigation with approval and recovery.
- Interruption and resume.
- Conflicting evidence and follow-up delegation.
- Prompt-injection runbook.
- All additional scenarios.

### Deployment tests

- Manifest validation.
- Non-mutating deployment plan.
- Clean deployment in a development environment.
- Targeted stack update.
- Downstream `--from` update.
- Deployment-context hydration.
- Web shell/API smoke test.
- AgentCore provisioning without invocation.

## 8. Traceability to the Case Study

| Case study requirement | Plan coverage |
| --- | --- |
| Multi-agent orchestration | Phase 4, Milestones 1–3 |
| Parallel investigation | Phase 4, `ParallelInvestigation` workflow stage |
| Multi-tool integration | Phase 2 and AgentCore Gateway in Phase 10 |
| Agent/tool discovery governance | Phase 4, Phase 7, and Agent Registry in Phase 10 |
| Dynamic collaboration | Phase 3 evidence gaps and Phase 4 follow-up tasks |
| Evidence-grounded reasoning | Phase 3 evidence workspace and Phase 9 checks |
| Policy-controlled autonomy | Phase 6 and Phase 7 |
| Stateful remediation | Phase 1 transitions and Phase 6 |
| Durable execution | Phase 5 |
| Evaluation | Phase 9 |
| Agent memory and retrieval | Phase 7 |
| Demo console | Phase 8 |
| Observability | Phase 8 and `08-observability-evaluation` |
| Four incident scenarios | Phase 1 and Phase 9 |
| Infrastructure as code | Phase 10 |
| Technical documentation | Phase 10 and Phase 11 |

## 9. Initial Implementation Order

Work in this order unless a dependency or test result justifies a change:

1. Freeze schemas and response envelopes.
2. Build the deterministic `INC-2048` simulator.
3. Implement and test read-only tools.
4. Implement the evidence workspace.
5. Add local stub agents and the orchestrator contract.
6. Register the agent/tool/skill records and connect the first three agents to
   AgentCore Harness/Gateway.
7. Add durable parallel workflow execution.
8. Add remediation planning, approval, and state transitions.
9. Add recovery verification and final report validation.
10. Add the remaining scenarios and evaluation suite.
11. Add knowledge retrieval, memory, identity, and policies.
12. Build the console around persisted execution records.
13. Add CloudFormation/SAM stacks and deployment orchestration.
14. Run deployment, smoke tests, failure injection, and acceptance checks.
15. Complete documentation and the repeatable demo walkthrough.

## 10. Working Checklist

### Foundation

- [x] Repository structure created.
- [x] Python and frontend toolchains configured.
- [x] Domain schemas versioned.
- [x] Local persistence adapter implemented.
- [x] Structured logging implemented.

### Simulator and tools

- [x] `INC-2048` fixture implemented.
- [x] Three additional scenarios implemented.
- [x] Deterministic seeded clock/state implemented.
- [x] Read-only tools implemented.
- [x] Remediation tools implemented with approval enforcement.
- [x] Gateway adapter implemented.
- [x] Tool authorization and tenant checks tested.

### Agents and workflow

- [x] Orchestrator implemented.
- [x] Telemetry Agent implemented.
- [x] Change Agent implemented.
- [x] Data and Business Impact Agent implemented.
- [x] Remediation Agent implemented.
- [x] Recovery Verification Agent implemented.
- [x] AgentCore Harness adapter and role configurations implemented.
- [x] Shared evidence workspace implemented.
- [x] Parallel delegation implemented.
- [x] Follow-up investigation implemented.
- [x] Step Functions workflow implemented.
- [x] Checkpoints, retries, and idempotency implemented.

### Safety and knowledge

- [x] Approval UI/API implemented.
- [x] Action-specific approval binding implemented.
- [x] S3 Vectors runbook retrieval implemented.
- [x] Adversarial runbook test implemented.
- [x] AgentCore Memory integration implemented.
- [x] AgentCore Policy and Identity integration implemented.
- [x] AWS Agent Registry created in the current namespace.
- [x] Registry client and idempotent provisioning manifest implemented.
- [x] Agent, tool, and skill records registered with versions and owners.
- [x] Registry approval and discovery path implemented.
- [x] Unapproved or deprecated records are excluded from runtime selection.
- [x] Registry discovery cannot bypass the static role allowlist or approval
      policy.
- [x] Hidden-state access boundary verified.

### Console and evaluation

- [x] Dashboard implemented.
- [x] Live workflow explainer implemented with Step Functions stage graph,
      AgentCore Harness loop, Gateway/tool-call trace, and evidence feed.
- [x] Collaboration view implemented.
- [x] Tool execution view implemented.
- [x] Evidence workspace view implemented.
- [x] Remediation console implemented.
- [x] Recovery verification view implemented.
- [x] Execution timeline implemented.
- [x] Deterministic evaluator implemented.
- [x] Semantic evaluator implemented.
- [x] Scenario scorecards implemented.

### Deployment and delivery

- [x] CloudFormation/SAM manifest implemented.
- [x] Independent stacks implemented.
- [x] Context hydration implemented.
- [x] `deploy-all.sh` implemented.
- [x] Web build and artifact publication implemented.
- [x] AgentCore post-deployment provisioning implemented.
- [x] Registry record reconciliation and explicit approval documented.
- [x] Deployment and web smoke tests implemented.
- [x] Cognito invite-only pool, admin group, token validation, and live
      anonymous-route boundary deployed with `NoTurningBack`.
- [x] Architecture and operating documentation completed.
- [x] Concept-to-implementation tutorial with Mermaid diagrams completed.
- [x] Five-minute demo walkthrough completed.

## 11. Verification record

- [x] `pytest -q` passes all 48 backend tests.
- [x] `apps/web` syntax check and production bundle build pass.
- [x] Live workflow explainer bundle published to the private web artifact
      bucket, including the concept-to-implementation tutorial link.
- [x] All nine `opsfusion-dev-*` CloudFormation/SAM stacks are deployed and
      healthy with the `NoTurningBack` profile.
- [x] AgentCore Harness, Gateway, Memory, Policy, Identity, and Registry
      resources are provisioned; Registry records are approved and discoverable.
- [x] Step Functions pause/resume was verified with the same run ID and a
      persisted DynamoDB checkpoint.
- [x] Real Harness end-to-end run `live-harness-e2e-final-1795481800` reached
      `completed`: approved rollback, healthy recovery at simulation tick 3,
      persisted report, and passing deterministic and semantic scorecards.
- [x] Anonymous `GET /` serves the login shell and `GET /healthz` remains
      public.
- [x] Anonymous `GET /auth/me` and `GET /api/scenarios` return `401` on the
      deployed API.
- [ ] Bootstrap the first `opsfusion-admin` user and run authenticated/admin
      live smoke tests after the operator supplies the initial administrator
      email.
