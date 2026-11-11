# AWS development deployment — readiness and next execution gate

**Current status: NO-GO for Terraform apply and live BigQuery dispatch.**
B8 has 11/11 combined PostgreSQL tests passed, but operational least-privilege
security acceptance is incomplete. The user also has working Docker locally.

## Existing AWS infrastructure code

The dev Terraform configuration already defines:
- Optional private PostgreSQL RDS in private subnets, encryption, deletion protection
- Optional SQS processing/DLQ, Lambda queue consumer, EC2 worker, verifier Lambda
- ECR and a versioned, encrypted, public-access-blocked S3 artifact bucket
- A pinned Terraform AWS profile and allowed account list

Region defaults in variables.tf were aligned to `ap-south-1`, matching the
existing dev tfvars example. **This changes defaults only: it deploys nothing.**

## Local checks — no cloud mutations

From the repository root:
```powershell
python -m pytest tests/test_aws_readiness_preflight.py -v --tb=short
python -c "from deployment.operations.aws_readiness_preflight import review_aws_source; x=review_aws_source(); print(x.verdict, x.blockers)"
terraform -chdir=deployment/terraform/environments/dev fmt -check
terraform -chdir=deployment/terraform/environments/dev init -backend=false
terraform -chdir=deployment/terraform/environments/dev validate
aws sts get-caller-identity --profile datarise
```
The final STS command is a **read-only AWS API** request. Verify that the
returned account ID matches the pinned `allowed_account_ids` in versions.tf;
do not post access keys, session tokens, passwords or tfstate contents.

Do not run `terraform apply`, `destroy`, `state rm`, S3 delete,
IAM policy mutation or DB role provisioning. A local backend can contain
secrets, so review state storage, encryption and access before planning.

## Work needed to deploy

1. AWS target-account identity and ap-south-1 region evidence; verify
   expected inventory already exists and is not managed elsewhere.
2. Separate production-oriented cloud IAM (S3 writer/reader,
   queue consumer, worker/verifier, secrets access) and network reachability.
3. Production database runtime-role credentials, function/trigger ownership,
   complete privilege matrix and negative authenticated SQL tests.
4. Explicitly review Terraform init/validate and read-only plan with
   all enable flags OFF; review AWS resource cost before enabling components.
5. Incremental approved S3/SQS/Lambda/RDS deployment with smoke tests
   and rollback/cleanup strategy; no live BigQuery analytics until its
   separate P0 security gates and publication-retention checks are completed.

The offline Python checker is deliberately conservative. It is not a
Terraform parser, a real cloud security scan, a pricing estimate or permission
to deploy.
