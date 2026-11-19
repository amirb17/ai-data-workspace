# Private ECS warehouse worker module — deployment review

**Status: implementation candidate, NOT DEPLOYED. No provider send authorization.**

Module: `deployment/terraform/modules/warehouse_workers`. No call to the module has been added to the production Terraform root. Nothing is created by merging.

## Architecture

- Exactly four independent ECS Fargate services, one task per worker duty.
- Each service has its **own AWS task IAM role and execution IAM role**, direct-login database secret, signed-RPC HMAC secret, and private EFS access point containing server certificate/key and trusted API-client CA.
- Private namespace: `provider-submission.warehouse.internal`, `recovery-attestation.warehouse.internal`, `accounting.warehouse.internal`, `result-authorization.warehouse.internal`.
- Tasks use private reviewed subnets with `assign_public_ip=false`; ingress is limited to API security group ports 18441–18444, egress limited to database SG 5432, TLS-material EFS SG 2049 and private VPC endpoint SG 443. Existing SGs/endpoints must also allow reciprocal directions.
- TLS material is mounted read-only. All four worker servers start with `--private-mtls` and verified direct-login role acceptance; the provider-submission worker STILL rejects `PROVIDER_SEND_BOUNDARY_NOT_APPROVED`.
- Image must be pinned by `@sha256:<digest>`. No GCP BigQuery creator or AWS provider permissions are attached.

## Infrastructure required before wiring the module

1. Review the exact AWS account `202064056386`, region `ap-southeast-2`, VPC, two private subnet IDs and their routes/DNS, existing ECS cluster and active API SG. Review current Terraform state drift against ECS API revision 22.
2. Provide private ECR API/DKR, S3 gateway (for ECR layer retrieval), Secrets Manager and CloudWatch Logs VPC endpoints, with endpoint SG permitting task SG on TCP 443. Private DNS should be enabled where required. The S3 gateway route must be attached to the private subnet route tables.
3. Create and validate separate restricted PostgreSQL login credentials `datarise_wh_submit`, `datarise_wh_attest`, `datarise_wh_account`, `datarise_wh_results` in the production database `aiworkspace` with reviewed table grants and catalog assertions. A fifth, separate `datarise_wh_control` credential is needed in API. **Do not run the development-only ACL installer on RDS.**
4. Provision distinct Secrets Manager secret ARNs for each duty's database DSN and 32-byte-or-longer Base64 HMAC key. Secrets must be raw strings, not JSON documents, because ECS injects them as exact environment values. API receives only the four HMAC keys and private mTLS client certificates, **not worker DB DSNs**.
5. Provision an EFS filesystem, mount targets and separate access points with `server.crt`, `server.key`, `client_ca.pem` securely staged per duty. Ensure the access point POSIX identity can read only its own TLS files. External EFS policy and mount-target SG must permit only the appropriate worker IAM role/SG. Rotations and CA trust must be managed.
6. Build a reviewed worker image with Python module entrypoint, pinned by digest and verified independently; validate CPU/memory and Fargate execution role permissions.
7. Require TLS handshake acceptance from the API task to each worker for: correct signed request, no client cert, bad CA, wrong hostname, wrong HMAC key, replayed request, and unrecognized operation. Static/offline tests are **not** sufficient.
8. Continue the atomic provider authorization and GCP three-identity IAM/BigQuery named result acceptance. Do not activate query runtime based solely on worker health.

## Invocation

Instantiate this module only in a reviewed isolated Terraform root or a new controlled workspace. Inputs:
`aws_region`, `name_prefix`, `vpc_id`, `cluster_arn`, `api_security_group_id`, `database_security_group_id`, `efs_security_group_id`, `vpc_endpoint_security_group_id`, `private_subnet_ids`, `image_digest_uri`, `image_repository_arn` and a `duties` map containing exact four duty names with separate `db_dsn_secret_arn`, `rpc_secret_arn`, `efs_file_system_id`, `efs_file_system_arn`, `efs_access_point_id`, `efs_access_point_arn`.

Run `terraform fmt -check`, `terraform init -backend=false`, `terraform validate` and review a targeted `terraform plan` with real inputs before requesting approval for any `apply`. Do not deploy unreviewed Terraform state into the existing application root.

## Known limitations

- This module provisions **infrastructure definitions only**, not credentials, EFS files, approved production PostgreSQL ACL changes, certificate issuance, BigQuery IAM or query runtime startup.
- A Cloud Map DNS record is not an application health probe. ECS RUNNING does not prove signed RPC health or DB role acceptance. After provisioning, explicitly verify each service using signed `probe_workers()`.
- The original in-memory replay guard is per-process; scale is intentionally one task per duty pending durable replay protection.
- TLS private keys are mounted read-only; they must not appear as Terraform variables or plaintext state.
- The API's `WarehouseWorkerRouter` still requires a trusted startup builder to load certificate-verifying SSL contexts and independent worker HMAC secrets.
- Private networking and EFS/file-system resource policies are assumed to be configured outside this module; this module deliberately does not create NAT, public Internet ingress, or broad cloud IAM.
