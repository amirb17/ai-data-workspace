# DataRise warehouse AWS/GCP rollout — implementation gate (2026-11-19)

Status: **NO-GO** for live BigQuery SQL. This document is not release authorization.

## Verified baseline

- AWS account `202064056386`, region `ap-southeast-2`; ECS `ai-workspace-dev-api` revision 22 stable, single API container.
- Public workspace Ask AI uses the **existing Gold analytics engine**, not BigQuery warehouse SQL.
- 31 PostgreSQL-backed HTTP integration tests with fake BigQuery passed.
- 17 restricted DB identity/security unit tests passed.
- Production PostgreSQL DB name is `aiworkspace` and has now been accepted by the **source** connection allowlist.
- AWS inventory found no other ECS service or query/worker-named Lambda functions. A secret called `ai-workspace/dev/db/worker` exists, but its role and privileges are not verified.
- `DATARISE_WAREHOUSE_CONTROL_DSN` absent from the API task.
- `app/main.py` does not bind a reviewed `InjectedQueryRuntime`.
- `provider_submission/record_send_boundary` currently rejects `PROVIDER_SEND_BOUNDARY_NOT_APPROVED` *before any SQL*.
- `WarehouseWorkerClient` connects to `127.0.0.1`, so the current RPC protocol is only suitable for co-located worker processes, not independent ECS service endpoints.

## One coordinated release implementation plan

1. **Database cutover and least privilege — before secrets.** Make a reviewed, backup-backed RDS migration for canonical data and isolated `datarise_warehouse_dev` protocol objects, including cross-schema FKs, trigger ACLs, default privileges and post-cutover integrity verification. Create *five distinct direct LOGIN roles* for query control, provider submission, recovery attestation, accounting and result authorization with no role memberships. Verify exact effective table, column, sequence and function privileges and negative DML using actual connections; never run the dev-only `tools.warehouse_worker_acl --apply` on production RDS. Do not use the existing generic worker secret as all five identities.
2. **Private worker placement and cloud IAM.** Current loopback RPC means private workers need an explicit transport redesign if deployed as separate ECS services. Do **not** place all cloud privileges in one shared ECS task role and mistake per-container environment isolation for independent IAM. Choose authenticated private worker RPC (e.g., private-only service endpoints with narrowly scoped signing, durable replay protection and network policies) and per-service IAM roles; or document and security-review a task-sidecar architecture with non-task-role GCP credential injection and strict privilege controls. Preserve exact fixed RPC duty/operation contracts and all negative tests.
3. **Atomic send boundary.** Design and test a single authoritative database linearization point that verifies original user/session authority, current publication, immutable request/policy digest, unexpired reservation and lease, then commits `SEND_UNKNOWN` exactly once. Only the approved sender may attempt the exact pinned BigQuery job ID and request once. Unknown insert outcome is lookup-only; no second provider submit. Keep the existing rejection in place until cross-process session proof and worker-effective ACL tests pass.
4. **GCP provider isolation.** Separately provision BigQuery creator, independent lookup/attester and results-reader identities, scoped to the reviewed project, region and exact datasets. Verify no table deletion capabilities for historical-serving resources, real BigQuery dry-run estimates, per-job maximum billed bytes and timeout limits, named result destinations, result schema/row bounds, and explicit cost approval.
5. **Runtime and UI binding.** Only after signed worker probes, policy and GCP verification should trusted application startup bind `InjectedQueryRuntime`; retain 503 otherwise. Add warehouse-specific query validation, estimate, admission, status, execution, refresh, cancel, bounded results and history to the analyst UI. Never route warehouse requests through existing Gold SQL or Ask AI.
6. **Real end-to-end acceptance.** Authenticate a non-admin workspace session; execute one bounded real BigQuery query; verify one job ID across retries, actual readback, settled billed bytes, retained publication dependencies, revoke/session expiry, cancellation and ambiguous network failures. Run full frontend/build/lint and backend restricted-role tests, review Terraform plan for ECS task definition drift (current API revision 22), then perform staged rollout and rollback drill.

## Read-only inventory

Run `python -m tools.warehouse_aws_release_inventory` from a local shell using AWS CLI profile `datarise`. It prints a JSON NO-GO verdict and blockers without fetching secret values, modifying resources or querying BigQuery. Exit code 3 indicates non-authorized readiness (expected), 2 indicates wrong account.

**Do not deploy workers, set live switches, or execute Terraform apply solely because the API regression suite passed.**
