# OpsFusion deployment

This is the operator guide for deploying the OpsFusion synthetic incident
investigation console. Deployment is manifest-driven: stacks have explicit
dependencies, predecessor outputs are hydrated from CloudFormation, and the
same command supports full deployments, plans, targeted updates, and retries.

There is one deployment entry point: `deploy-all.sh`.

## What the deployment does

The runner calls
[`infrastructure/cloudformation/orchestrate.py`](../infrastructure/cloudformation/orchestrate.py),
which reads the manifest, runs the declared checks, delegates each stack to
the SAM/CloudFormation adapter, saves outputs, and publishes synthetic
runbooks and the versioned web artifact.

The generated context is a retry cache, not source code:

```text
infrastructure/cloudformation/build/deployment-context-<environment>.json
```

When a downstream stack needs an output, the runner rehydrates it from the
predecessor stack and updates the context. Do not commit the generated context
or hand-edit it to bypass a failed deployment.

## Prerequisites

Install or make available:

- Python 3.12 or later and the dependencies in `requirements.txt`.
- AWS CLI credentials for the target account.
- AWS SAM CLI.
- Node.js and npm for the web console build.
- An AWS region and profile with permission to deploy the target resources.

Confirm the intended AWS identity before a real deployment:

```bash
export AWS_PROFILE=your-aws-profile
export AWS_REGION=us-east-1

aws sts get-caller-identity \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

The deployment identity must be able to create and update CloudFormation/SAM
resources, IAM roles, Lambda functions, Cognito resources, DynamoDB tables,
AgentCore resources, and the web artifact bucket.

## Preflight

Run the local checks directly while iterating:

```bash
python3 -m pytest -q
python3 -m compileall -q agents apps contracts evaluation evidence gateway infrastructure knowledge memory registry simulator workflow
(cd apps/web && npm test && npm run build)
```

Run a non-mutating deployment plan to resolve the selected manifest path and
stack order without calling CloudFormation:

```bash
./deploy-all.sh \
  --environment dev \
  --plan \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

## Full deployment

Use a unique release ID for each intentional deployment attempt:

```bash
./deploy-all.sh \
  --environment dev \
  --release-id YYYYMMDD-opsfusion \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --resolve-s3
```

The manifest deploys these stacks in dependency order:

| Stack | Responsibility |
| --- | --- |
| `00-foundation` | Shared deployment roles and foundations |
| `01-state` | Workflow, evidence, and simulator DynamoDB state |
| `02-simulator` | Synthetic incident runtime and Gateway target Lambda |
| `03-knowledge` | Synthetic runbook storage and retrieval |
| `04-agentcore` | AgentCore Gateway, target, Harness, Memory, Policy Engine, and read-only Policy |
| `05-workflow` | Durable worker and Step Functions workflow |
| `06-application` | Authenticated API, Cognito, and application Lambda |
| `07-web` | Web artifact bucket and publication resources |
| `08-observability-evaluation` | Logs, evaluation, and supporting observability |

The deployment builds the console locally and publishes the versioned artifact
to the web bucket. CloudFormation outputs provide the resulting access values;
the repository does not publish account-specific URLs.

## AgentCore provisioning

The `04-agentcore` stack is the single provisioning path for AgentCore. It
declares and manages the Gateway, Gateway target, Harness, Memory, Policy
Engine, and read-only Policy through CloudFormation. A normal deployment does
not require a second SDK provisioning command or a manual environment-variable
handoff.

On a fresh environment, the orchestrator may perform two CloudFormation passes:
the first creates the Policy Engine and Gateway, and the second attaches the
resulting Policy Engine identifier to the Gateway. This resolves the resource
dependency without creating an out-of-band AgentCore resource.

## Registry governance

Registry records are separate from runtime authorization. They describe the
OpsFusion agents, tools, and skills for discovery and governance; Gateway
policy and the application role allow-lists remain the runtime boundaries.

Reconcile records as drafts after deployment:

```bash
./deploy-all.sh \
  --environment dev \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --reconcile-registry
```

Submit or approve explicitly only when that governance decision is intended:

```bash
./deploy-all.sh \
  --environment dev \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --reconcile-registry \
  --submit-registry

./deploy-all.sh \
  --environment dev \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --reconcile-registry \
  --approve-registry
```

Approval is not required for the normal AgentCore Harness invocation and does
not authorize a state-changing action; the deployed tool catalog is read-only.

## Targeted updates and retries

Deploy one stack when a change is scoped to that stack:

```bash
./deploy-all.sh \
  --environment dev \
  --stack 04-agentcore \
  --release-id YYYYMMDD-agentcore-update \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --resolve-s3
```

Deploy a stack and all downstream dependents with `--from`:

```bash
./deploy-all.sh \
  --environment dev \
  --from 04-agentcore \
  --release-id YYYYMMDD-agentcore-rollout \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --resolve-s3
```

Use the smallest valid scope after correcting the implementation. The runner
hydrates predecessor outputs automatically; do not delete stacks, reset state,
or manually recreate AgentCore resources as a retry strategy.

## Cognito bootstrap and access

The `06-application` stack creates an invite-only Cognito User Pool. The
Function URL serves the login shell and `/healthz`; protected API routes
validate Cognito ID tokens. User invitations require membership in the
`opsfusion-admin` group.

After deployment, obtain `CognitoUserPoolId` from the stack outputs and create
an administrator with an authorized AWS identity:

```bash
POOL_ID=<cognito-user-pool-id-from-stack-output>
ADMIN_EMAIL=admin@example.com

aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --user-attributes Name=email,Value="$ADMIN_EMAIL" \
  --desired-delivery-mediums EMAIL \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --group-name opsfusion-admin \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

The invited administrator completes the first-login password challenge and can
then invite additional operators through the admin surface. The API enforces
the group boundary; hiding a browser control is not the security boundary.

## Verification

Check the stack state and public health boundary after deployment:

```bash
aws cloudformation describe-stacks \
  --stack-name opsfusion-dev-04-agentcore \
  --query 'Stacks[0].StackStatus' \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

The expected access model is a login shell and public health endpoint with
protected investigation and administration APIs. The console should show the
same run-scoped evidence and analysis state described in
[`FIVE_MINUTE_WALKTHROUGH.md`](FIVE_MINUTE_WALKTHROUGH.md).

## Related documentation

- [`TUTORIAL.md`](../TUTORIAL.md) — AgentCore Harness, Gateway, multi-agent
  workflow, evidence, and implementation details.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — authority and runtime boundaries.
- [`infrastructure/cloudformation/deployment.yaml`](../infrastructure/cloudformation/deployment.yaml) — deployment manifest.
