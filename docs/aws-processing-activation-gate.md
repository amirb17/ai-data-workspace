# Consolidated processing release (corrected architecture audit)

**Use the existing complete integration, not the experimental Bronze implementation.**

The repository already has the integrated executor at app/processing_worker/integrated_executor.py. It executes the established Bronze, Silver and Gold services with lease control, immutable versioned S3 artifacts, independently verified candidate manifests, and atomic publication receipts. The verifier is app/processing_worker/verification_service.py, the SQS transport is app/processing_worker/sqs_transport.py, the runtime bootstrap is app/processing_worker/bootstrap.py and the dispatcher is app/processing_worker/dispatcher.py.

IMPORTANT: execution._not_implemented is a fail-closed fallback only when a trusted executor is not injected. Existing integrated executor and PostgreSQL tests cover real stage execution, crash recovery, duplicate arrivals and stale receipts. The recently created parallel Bronze candidate and boundary files have been removed rather than integrated, because their artifact schema differs from the verified publication path.

## One complete milestone, not many micro-tasks

Production processing cannot yet be declared working. It requires ALL of:
1. Read-only RDS readiness inventory in deployment/operations/sql/processing_release_readiness.sql, including source version pins for the uploaded file, migration RPCs and distinct API/dispatcher/worker/verifier DB roles.
2. Review source registration: the live upload-only completion did not automatically create a durable processing_source_objects pin. Do not infer an immutable version from an S3 key; verify the raw object's VersionId, length and SHA-256 and register it only with the existing authorized task20_register_source function.
3. Dedicated SQS queue, dispatcher, worker, verifier Lambda, S3 artifact read/write rights, DB secrets, distinct IAM identities, network access and logging. The existing public API S3 task identity is NOT the processing worker identity.
4. Reviewed new processing-admission policy: public csv-readonly still intentionally disallows every processing POST and requires disabled processing flags. Do not flip production flags in-place just to enable a button.
5. A single live canary covering admitted BRONZE, committed verified data/lexical artifact and receipt, row/schema/profile counts, interrupted execution retry, then approved-rule SILVER with quarantine, then load-policy GOLD with immutable publication and read-only analyst read.
6. An off switch and rollback retaining audit evidence and S3 versions.

## Consolidated verification

Run this suite once against an isolated local PostgreSQL database, not production:

    $env:RUN_DB_TESTS = "1"
    python -m pytest -q --tb=short tests/test_task20_processing_postgres.py tests/test_artifact_publication_postgres.py tests/test_task20_security_postgres.py tests/test_execution_boundary.py tests/test_dispatch_supervision.py tests/test_processing_worker_bootstrap.py tests/test_runtime_readiness.py tests/test_upload_sessions_postgres.py tests/test_public_product_capabilities_read.py

This is not a deployment authorization. A live processing milestone is only complete when the whole canary and recovery evidence pass.

---

## Prior design notes (superseded where inconsistent)

# AWS processing activation gate (after live S3 CSV upload)

## Verified first milestone
The user verified a browser presigned SigV4 PUT returned 200, POST /files/complete
returned 200, and the dataset Processing UI showed one READY_TO_PROCESS delivery.
This establishes S3 ingestion, not Bronze/Silver/Gold execution.

## Current blocking implementation facts
- `app/security/public_launch.py` explicitly denies every processing POST in
  `csv-readonly` mode and requires processing deployment flags set false.
- `app/api/processing_jobs.py` rejects admission unless
  `DATARISE_PROCESSING_DEPLOYMENT_APPROVED=true`.
- `app/processing_worker/execution.py` defaults to `_not_implemented` which
  raises `NotImplementedError`. A real stage executor and its bounded
  checkpointed, idempotent publication must be implemented and tested.
- `app/processing_worker/worker.py` refuses to start without an injected
  transport; an SQS adapter, dispatcher and supervised runtime need explicit
  deployment and credentials.
- `app/processing_worker/runtime_security.py` enforces separate narrow API,
  dispatch, worker and verifier SQL authority, independently of HTTP flags.
- Existing `deployment/terraform/environments/dev/processing_queue.tf`,
  `lambda_worker.tf`, and `ec2_worker.tf` are opt-in infrastructure components,
  not proof of an operational end-to-end stage worker. Do not enable all
  infrastructure based on the upload success alone.
- Product `capabilities`/job-list GETs previously attempted FOR SHARE on
  ownership tables using a SELECT-only public role. The separate metadata-only
  read fix/grant must be deployed and verified.

## Required staged engineering acceptance
1. **Bronze offline:** Build a trusted stage adapter from a committed
   `JobExecutionScope`; read exact pinned source from S3, verify SHA-256 and
   size, enforce maximum bytes/time, write versioned Bronze artifacts and
   profile/schema evidence, use idempotent transaction and checkpoint before
   publish. Test duplicate delivery, interrupted execution and source mismatch.
2. **Isolated runtime:** Provide SQS transport, outbox dispatcher and a
   supervised worker with one message in flight; implement real
   `boundary_executor`. Enforce lease renewal, retry/DLQ, no ACK before
   committed result, and worker logs without data/credentials.
3. **Database roles:** Establish separate least-privilege API admission,
   dispatcher, executor and verifier identities. Run runtime permission
   inventory and PostgreSQL transaction/recovery tests on isolated schemas.
4. **AWS preflight:** Review existing queue, worker compute plan, S3 objects,
   Secrets Manager, CloudWatch, network, cost and human-approved Terraform plan.
   Do not change an existing healthy API task role to a worker role.
5. **Bronze live canary:** Explicitly enable only approved Bronze admission for
   a single owned test upload; confirm S3 Bronze artifacts, persisted
   RUNNING/SUCCEEDED evidence, materialized dataset schema and row counts,
   idempotent replay, and no automatic Silver/Gold.
6. **Silver:** Require pinned dataset-version rule approval, validate quality
   and quarantine invalid rows, verify exact numeric/parquet representations
   and recovery, preserve immutable source.
7. **Gold:** Require approved load policy, publish append/upsert/snapshot
   atomically with recoverable predecessor lineage; test retry and stale head.
8. **Analytics:** Enable read-only warehouse/analyst UI only after Gold
   published state and model authorization are verified.

**No automatic promotion:** An UPLOADED file is not an approved processing
result, and a passed offline test is not live execution authorization.
