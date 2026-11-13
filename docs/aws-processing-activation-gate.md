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
