# DataRise FastAPI container — build preparation only

**No ECS deployment, ECR push, IAM change, RDS mutation or cloud cost is
performed by this change.** Keep the established worker and verifier images
separate; they have unrelated security duties.

## Security shape

- Dedicated `deployment/docker/Dockerfile.api` uses an externally reviewed
  Python 3.13 base image pinned **by SHA256 digest**, pinned source commit SHA,
  a pre-exported fully hashed dependency lock and lock-integrity verifier.
- Non-root Linux user, non-development authentication, secure cookies and
  fail-closed `csv-readonly` public route allowlist.
- Warehouse query processing and BigQuery metric promotion OFF. This is **not**
  the local privileged demo runner.
- Bundles the RDS root CA and requires `PGSSLMODE=verify-full`, with DB
  credentials supplied securely by the runtime (never COPY `.env`).
- API listens on container port 8000. A private HTTPS-aware ingress is a
  separate deployment requirement, not provided by the Dockerfile.

## Offline verification

```powershell
python deployment/docker/verify_lock.py
python -m pytest tests/test_aws_api_container.py -q
```

## Reviewed image build after base-image selection

Supply a **trusted Python 3.13 Linux image digest** for the target architecture;
never use a mutable tag alone. Calculate the requirements hash from the
*existing tracked* lock and use the exact source revision.

```powershell
$base = 'python:3.13-slim@sha256:REPLACE_WITH_REVIEWED_DIGEST'
$revision = (git rev-parse HEAD).Trim()
$lockHash = (Get-FileHash deployment/docker/requirements.lock -Algorithm SHA256).Hash.ToLowerInvariant()

docker build --file deployment/docker/Dockerfile.api `
  --build-arg API_BASE_IMAGE=$base `
  --build-arg SOURCE_REVISION=$revision `
  --build-arg RUNTIME_LOCK_SHA=$lockHash `
  --tag datarise-api:review .
```

The provided `REPLACE_WITH_REVIEWED_DIGEST` is intentionally invalid.
Do not invent or blindly paste a digest. Review base provenance and
architecture first. The lock verifier rejects stale or changed inputs.

## AWS release prerequisites

1. Confirm ECS resource creation, task execution-role and ECR write
   permissions **without** bypassing Organizations SCP restrictions.
2. Review ECS/Fargate cost, ALB/API HTTPS cost, VPC endpoint/NAT traffic,
   and Free plan eligibility before creating resources.
3. Use a *dedicated* API ECR repository, never worker/verifier repository.
4. Configure the exact public API hostname in
   `AUTH_BACKEND_URL` and `DATARISE_PUBLIC_HOSTS`. Set
   `AUTH_FRONTEND_URL` to the actual Amplify HTTPS origin.
5. Supply strong session secret, SMTP/OAuth config where applicable,
   restricted PostgreSQL runtime DB credentials, DB security group access
   and task IAM secrets securely. Do not grant the API database superuser
   or unrestricted S3 permissions.
6. Audit `PublicLaunchBoundary` behind load-balancer HTTPS termination:
   it checks ASGI `scope['scheme']=='https'` and deliberately does **not**
   trust unreviewed proxy headers. The ingress must provide a reviewed
   verified scheme; do not disable this protection just to obtain HTTP 200.
7. Test TLS, CSRF/cookie behavior, private DB connectivity, Auth flow and
   negative route authorization before enabling public users.
8. Frontend still has fail-closed static-preview mode. Provide the real
   HTTPS API origin in Amplify as `VITE_API_BASE_URL` and rebuild only
   after backend readiness has been verified.

The health endpoint may be blocked by the public launch route allowlist.
A readiness/health strategy must be reviewed for the actual ingress.
Do not force production routes open to satisfy a default health check.
