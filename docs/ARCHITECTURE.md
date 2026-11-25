# OpsFusion architecture

OpsFusion separates three authorities:

1. AgentCore Harness owns model reasoning, tool selection, iteration, and
   stopping for each role-scoped invocation.
2. AgentCore Gateway exposes the bounded synthetic operational tools.
3. The application and Step Functions own evidence writes, checkpoints, and
   bounded retries; the simulator remains read-only during analysis.

The Registry catalogs the orchestrator, three specialist roles, Gateway, and
reference skills. Approved Registry metadata may be used to discover a
capability, but runtime selection still passes the static role allowlist and
application policy.

The simulator stores hidden ground truth only for evaluation.  Agent-facing
tool responses contain observations and evidence references, never the hidden
root-cause label or a conclusion.  The evaluator accesses hidden truth through
an evaluator-only interface after the run.

```text
Operator -> API -> DurableInvestigationWorkflow -> role-scoped AgentCore Harness
                    |                                  |
                    |                                  +--> AgentCore Gateway -> synthetic tool target
                    |                                                               -> metrics/logs/deployments/orders
                    +--> DynamoDB checkpoints/evidence
                    +--> analysis checkpoint -> read-only evaluation

Step Functions -> workflow.worker -> DynamoDB durable stage checkpoint
```

The operator API uses the durable workflow adapter directly for the interactive
console path. The deployed Step Functions state machine provides a separate,
replay-safe stage boundary over the same checkpoint contract; it does not
reimplement the Harness model/tool loop.

The local `agents.stub.LocalStubClient` is opt-in for repeatable tests and the
console demo.  It is not the cloud execution path and does not replace the
managed Harness loop.
