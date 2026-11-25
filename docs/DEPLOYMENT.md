# OpsFusion deployment

The deployment follows the manifest-driven pattern used by
`agent-harness-demo`: independent stacks, explicit dependencies, persisted
outputs, targeted retries, and CloudFormation-managed AgentCore resources.

Use an AWS profile and region explicitly:

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=your-region

./deploy-all.sh --environment dev \
  --release-id YYYYMMDD-opsfusion-initial \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" --resolve-s3
```

The non-mutating preflight is:

```bash
./deploy-all.sh --environment dev --plan \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

The runner validates all templates, runs Python tests, builds the console,
deploys stacks in dependency order, hydrates downstream context from
CloudFormation outputs, and uploads synthetic runbooks and the versioned web
artifact.  The context is saved at:

`infrastructure/cloudformation/build/deployment-context-dev.json`

The `04-agentcore` stack provisions the Gateway, Gateway target, Harness,
Memory, Policy Engine, and read-only Policy as one CloudFormation-managed
boundary. There is no separate AgentCore provisioning command.

Registry records are reconciled separately and remain DRAFT unless the
operator explicitly submits them:

```bash
python3 -m registry.provision --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --environment dev --records

python3 -m registry.provision --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --environment dev --records --submit
```

The submitted records remain `PENDING_APPROVAL` until the catalog owner makes
the separate, explicit decision:

```bash
python3 -m registry.provision --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --environment dev --records --approve
```

`--stack 04-agentcore` retries one stack. `--from 04-agentcore` retries that
stack and all downstream dependents. The deployment scripts never delete or
reset existing state.

## Cognito bootstrap and access

The `06-application` stack creates an invite-only Cognito User Pool. The
Function URL serves the login shell and `/healthz`, while the FastAPI
application validates Cognito ID tokens for `/api/*`, `/auth/me`, and admin
operations. User invitations are restricted to the `opsfusion-admin` group.

After the first deployment, bootstrap one administrator with an authorized AWS
operator. Replace the placeholder before running the commands:

```bash
POOL_ID=<cognito-user-pool-id-from-stack-output>
ADMIN_EMAIL=admin@example.com

aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --user-attributes Name=email,Value="$ADMIN_EMAIL" \
  --desired-delivery-mediums EMAIL \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --group-name opsfusion-admin \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

The administrator signs in with the temporary Cognito password, completes the
first-login password challenge in the console, and then invites additional
operators from the admin panel. Admin membership is read from the verified ID
token group claim; hiding a browser button is not the security boundary.
