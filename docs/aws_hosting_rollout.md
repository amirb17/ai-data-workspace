# DataRise AWS web deployment — staged, no-go for public launch

**Scope:** GitHub branch `feature/warehouse-model-ux`, AWS `ap-southeast-2`.
The app is **not deployed by these files**. The current local warehouse
query was observed `SUCCEEDED`, but production result reader authorization
and actual rows remain unverified. NEVER run the local privileged
`tools.warehouse_local_live_api` in AWS.

## Recommended layout

- **Amplify Hosting**: React/Vite static UI from `frontend/` and this
  repository's root `amplify.yml`.
- **FastAPI container service**: evaluate ECS/Fargate (including ECS Express
  Mode) only after confirming actual account permissions, costs, private RDS
  networking and outbound connectivity. App Runner stopped accepting new
  customers on April 30, 2026 and is not our target. The existing worker
  Dockerfile is NOT a web API container.
- **Private RDS PostgreSQL**: no public IP, secrets in AWS Secrets Manager,
  TLS certificates and a transactional schema migration strategy. The
  current Terraform RDS configuration is opt-in; verify the target engine
  version and connectivity before apply.
- **S3, SQS, Lambda/EC2 workers**: reuse the existing dev Terraform modules
  only after account, VPC, IAM, cost, and worker requirements are reviewed.
- **Warehouse/BigQuery**: keep production runtime fail-closed. Separate
  GCP workload-identity authorization, controlled network egress, independent
  worker duties, and result-read acceptance are release prerequisites.
  Do not copy developer impersonation credentials into AWS.

## Permission-first service inventory — read-only

The Sydney project account has private RDS and a running EC2 worker.
`apprunner:ListServices` was explicitly denied by the AWS Organizations SCP.
AWS's new-account Free-plan catalog lists Amplify, ECS, CloudFront and S3,
but each actual operation must be checked against the project-specific SCP
and identity policies. Do not upgrade merely to enable App Runner.

```powershell
$profile = 'datarise'
$region = 'ap-southeast-2'
aws amplify list-apps --region $region --profile $profile --query 'apps[].{Name:name,Id:appId}' --output table
aws ecs list-clusters --region $region --profile $profile --output json
aws cloudfront list-distributions --profile $profile --query 'DistributionList.Items[].{Id:Id,Domain:DomainName}' --output table
aws s3api list-buckets --profile $profile --query 'Buckets[].Name' --output table
```

These calls are read-only; successful listing does not establish create
permissions. An explicit SCP deny is a stop signal, not a reason to try a
different role or workaround. Existing infrastructure and spending controls
must be reviewed before deployment.

## Stage 0: inspect target identity without mutating AWS

```powershell
aws --version
terraform version
aws sts get-caller-identity --profile datarise
aws configure get region --profile datarise
python -m pytest tests/test_aws_readiness_preflight.py -q
python -c "from deployment.operations.aws_readiness_preflight import review_aws_source; p=review_aws_source(); print(p.verdict, p.blockers)"
terraform -chdir=deployment/terraform/environments/dev fmt -check
terraform -chdir=deployment/terraform/environments/dev init -backend=false
terraform -chdir=deployment/terraform/environments/dev validate
```

Confirm STS account is the account pinned in
`deployment/terraform/environments/dev/versions.tf` and the region is
`ap-southeast-2`. Share neither credential files nor session tokens.

**Do not apply Terraform yet**: it uses a local tfstate backend; state
security, ownership, existing assets, default-off variables, and expected
monthly cost require review. Existing `docs/aws_dev_readiness.md` is NO-GO.

## Stage 1: prepare authenticated API hosting

1. Build a dedicated pinned and reviewed web API container with `uvicorn`
   serving `app.main:app` on a non-root container port. Use the repository's
   dependency lock and validate imports and startup before pushing to ECR.
2. Provision a private PostgreSQL service and secure application credentials.
3. Configure production `APP_ENV`, `AUTH_MODE=session`,
   `AUTH_COOKIE_SECURE=true`, `AUTH_SECRET`, `AUTH_FRONTEND_URL`,
   `AUTH_BACKEND_URL`, `DATABASE_URL` via AWS secret/config controls.
4. Review ECS task networking for private RDS access. Assess S3, cloud API and OAuth
   connectivity **separately**—VPC routing can change public egress.
5. Health checks: `/health` for process; authenticated API/db integration
   separately. Do not use a public DB health endpoint as proof of security.
6. Turn warehouse query execution OFF in production until complete worker
   routing, IAM, trigger/privilege negative tests and result reader acceptance.

## Stage 2: deploy frontend

1. Connect the repository's intended branch to Amplify Hosting; monorepo
   `appRoot` is `frontend`. `amplify.yml` runs `npm ci` and
   `npm run build` and publishes `frontend/dist`.
2. Configure `VITE_API_BASE_URL` to the actual HTTPS API origin **at build
   time**. This is public frontend configuration, not a secret.
3. For React Router, configure Amplify SPA rewrite to `/index.html` for
   application routes. Do not rewrite API or static asset errors as HTML.
4. Configure frontend/backend allowed origins, Google OAuth callbacks,
   CSRF/session cookie domain rules and verified TLS custom domains.
5. Smoke test sign-in, project/workspace CRUD, upload, S3 processing and
   analyst workflows before making the app accessible to broader users.

## Deployment acceptance

- Correct AWS account and region verified; costs and state reviewed.
- No embedded keys or publicly reachable PostgreSQL.
- Backend TLS, private RDS, least-privilege role and runtime health pass.
- Frontend HTTPS, CORS, session/CSRF and OAuth pass real-browser tests.
- Processing end-to-end upload→S3→SQS→worker→database verified.
- Warehouse may remain visible as a disabled feature but must not claim
  completed production execution until its data-read integration passes.

**Important:** A successful frontend build does not mean the full app has been
deployed, and a local BigQuery success is not an AWS production acceptance.
