# DataRise warehouse private VPC endpoints (opt-in)

This module adds ONLY the three absent endpoints seen in the user's read-only AWS inspection on 2026-11-19:

* `com.amazonaws.ap-southeast-2.ecr.api`
* `com.amazonaws.ap-southeast-2.ecr.dkr`
* `com.amazonaws.ap-southeast-2.logs`

Existing VPC: `vpc-0235c4ff54488355c`. Worker subnet IDs:
`subnet-07e43e5559f36b21c` (2b) and `subnet-044eb7e6626f02bbf` (2a).
These are attached to private route table `rtb-013a4f556df67b2c2` (only VPC-local and S3 gateway route). Existing S3 gateway and Secrets Manager interface endpoints are intentionally preserved.

This is a separate, **opt-in module not instantiated from the application root**. It creates three interface endpoints in two AZs (six endpoint ENIs), which incur ongoing AWS hourly and data-processing charges. Do not apply without reviewing costs and confirming account, VPC DNS attributes, available IP addresses, IAM authority and Terraform state ownership.

## Required integration

The module takes the **worker security group ID**, not the API SG. Its endpoint SG accepts TCP 443 only from that worker SG. Pass its `security_group_id` output into the four-duty worker module's `vpc_endpoint_security_group_id` variable.

Before launching workers:

1. Check worker SG egress TCP 443 to this endpoint SG (worker module declares it).
2. Confirm **existing** Secrets Manager endpoint `vpce` SG `sg-0756562b88e103959` permits 443 inbound from the new worker SG and that the worker SG permits TLS egress to it. The four-worker module currently allows endpoint egress to only **one** SG, so add a separate narrowly-scoped egress rule before deployment; do not replace the existing Secrets Manager endpoint.
3. Confirm the existing S3 gateway route table association is intact and any S3 endpoint policy allows ECR image layer retrieval.
4. Validate actual image registry URI and immutable digest in existing `ai-workspace-dev-worker` ECR repository; a repository's existence alone is not proof of a runnable image.
5. Confirm CloudWatch Logs endpoint and task execution role connectivity before worker service rollout. No EFS filesystem or certificate access points exist yet, and no RDS worker credentials are verified.

## Validation

Run `terraform fmt`, `terraform init -backend=false` and `terraform validate` inside this module. Run `pytest -q tests/test_warehouse_private_endpoints.py` from repository root.

A successful static validation is NOT a real AWS deployment plan or security acceptance. Do not wire the API runtime or authorize BigQuery sends as a side-effect.
