# Shared execution and serial EC2 worker foundation

## Architecture decision

Heavy processing belongs in a separate container worker on EC2, alongside the
FastAPI deployment boundary but **not** spawned inside each API process.
Lambda is reserved for lightweight ingestion validation, public API work and
dispatch/recovery. PostgreSQL/RDS remains authoritative for jobs and metadata;
SQS carries committed job UUIDs and S3 holds source/artifact data.

This task adds no SDK transport, Docker deployment, cloud configuration, public
API or real processing executor. Both Lambda and container execution still fail
closed by default. Moving heavy work to EC2 removes Lambda's runtime ceiling;
it does not remove leases, duplicate delivery, blocking I/O or stale-writer risks.

## Audited reusable contracts

The existing job repository already provides atomic/idempotent admission,
authorization and pinned source checks, seven claim outcomes, database-clock
lease checking/renewal, bounded retries and token-fenced completion. It is
unchanged. Existing dataset/lifecycle/delivery session locks and stage recovery
are also unchanged. Bronze/Silver/Gold and incremental processing are not wired.

The existing execution and boundary implementations were extracted into
`app/processing_worker/execution.py` and `boundary.py`. The old Lambda modules
are compatibility imports of the same functions/classes, not duplicate engines.
The only extraction change inside the core is its boundary import path.
`app/lambda_worker/handler.py` retains event-envelope validation and partial-batch
failure responses; it now uses the shared UUID-only payload parser.

Execution remains synchronous in an independent `Context`: a caller's pending
repository transaction cannot make acknowledgment precede completion commit.
Terminal success/failure/source rejection can return without executing. BUSY,
NOT_DUE and NOT_FOUND raise for retry. CLAIMED requires successful injected work
and committed token-fenced completion; executor/persistence/lease failures do not
ACK. A failure may persist RETRY_WAIT/FAILED, but its current delivery still fails.

## Transport and local entry point

`QueueMessage` contains an identifier, JSON body and delivery receipt. Body and
receipt are excluded from repr/logging. The shared parser accepts only
`{"job_id": "<UUID>"}`; upload/principal/operation instructions are rejected.

Inject a `QueueTransport` implementing:

- `receive_one(stop)`: bounded polling, at most one message, no internal prefetch;
  return `None` if empty. Honor shutdown or use a bounded SDK read timeout.
- `acknowledge(message)`: delete/ack only this delivery's receipt. Called only
  after shared execution returns an ACK-permitted result.
- `retry(message)`: **must not delete/ack**; retain/defer the delivery, optionally
  adjusting visibility according to a separately reviewed policy.
- `close()`: release local resources, without acknowledging outstanding work.

`SerialWorker.run()` polls and processes one message fully before receiving the
next. It uses interruptible idle backoff and closes transport on every exit.
Receive/retry/ACK transport failures propagate for supervision rather than
silently continuing. ACK uncertainty can cause redelivery; durable job success
then avoids re-execution. There is no automatic restart or redrive provisioned.

The local entry point is `python -m app.processing_worker`. With no configured
transport it exits **2** without connecting to AWS or PostgreSQL. An approved
future bootstrap can call `worker.main(transport, boundary_executor=...)` in
the container's main thread. It installs/restores SIGTERM/SIGINT handlers that
only request cooperative shutdown. No dynamic plugin loading or credentials
configuration is introduced.

Example injection contract (no real adapter is supplied):

```python
from app.processing_worker.worker import main

# transport implements QueueTransport; executor accepts ExecutionBoundary.
exit_code = main(transport, boundary_executor=approved_executor)
```

## Runtime budgets, leases and shutdown

The container worker accepts only the cooperative `boundary_executor` injection,
not the older raw lease-executor shortcut. Default execution is still explicitly
not implemented. An injected executor receives the same `ExecutionBoundary` as
the existing controlled Lambda test path. Scope checks, synchronous renewal and
dataset → lifecycle → delivery locking remain available through its checkpoints
and `stage` context; the worker loop does not independently acquire/reimplement
those locks or start processing attempts.

Instead of a Lambda invocation clock, `SerialWorker` supplies a monotonic per-job
budget (default one hour, configurable on construction). It returns zero after
shutdown is requested. This is cooperative admission, not a hard execution timer.
The shared boundary still renews at explicit checkpoints (default 120-second
window, repository bounds 1–900 seconds) and requires each declared chunk plus
completion reserve to fit its lease window. A long job may span many renewals;
no background thread performs them during a blocking call.

Shutdown stops new polls. A delivery returned during shutdown is deferred before
claiming. Active synchronous work is allowed to unwind: its next checkpoint and
the executor's post-check reject further work/completion when stopped; the current
delivery is deferred. Already committed terminal results can still be ACKed.
Transport closes only after active code returns/unwinds. There is no forced
thread cancellation and no proven maximum shutdown latency for blocking code.
An external forced kill leaves leases/attempts for recovery and receipts for
redelivery; container termination grace and client timeouts need deployment review.

## Remaining blockers before real processing

The [execution-path review](lambda-processing-integration-plan.md) still applies
to storage/publication integrity even though its Lambda-only sizing proposal is
superseded by EC2 processing. In particular:

1. Implement/review a real SQS adapter with single-message receive, visibility
   renewal coordinated with the PostgreSQL lease, bounded poison-message/redrive
   handling and safe shutdown/client timeouts. PostgreSQL renewal alone does not
   extend SQS visibility or ensure a receipt remains usable for deletion.
2. Approve real stage adapters, durable intent/receipt association and Apply job
   semantics. Preserve rule approval, lineage, state transitions and Exact V2
   restrictions. Do not use `continue_processing` as an assumed single-stage call.
3. Address fixed-key artifact overwrites and job-aware transactional publication
   fencing. Checkpoints cannot cancel already-running SDK/pandas/SQL work or undo
   side effects. Existing session locks are not durable storage fences.
4. Provide bounded chunks or an explicitly reviewed renewal/cancellation design
   for non-cooperative long calls, plus client deadlines and resource limits.
   EC2 alone does not make a call longer than the current lease safe.
5. Implement durable dispatch/outbox/reconciliation and completion-loss recovery.
   Finalize whether contention/dependency waits should consume execution retries;
   currently ordinary controlled-executor exceptions use the existing retry policy.

No production processing is safe to enable from this foundation alone.

## Validation — 2026-11-01

- Initial fully offline regression: **109 passed**, 0 failed/errors/skipped,
  2.21 s. SDK/session/storage and all database connections were blocked before
  collection; this included the existing integration-plan and rule-gate probes.
- Final worker/job regression after transport cleanup additions: **158 passed**,
  0 failed/errors/skipped, 176.42 s: 33 container-worker, 25 Lambda handler,
  18 adapter, 20 boundary and 62 isolated PostgreSQL cases.
- The runs overlap and must not be summed. The PostgreSQL
  container test observes committed SUCCEEDED state from an independent
  connection inside ACK, then verifies duplicate delivery executes only once.
- Final container-worker unit check after stabilizing a payload parameter ID:
  **33 passed**, 0 failed/errors/skipped, 0.16 s.
- Tests cover invalid payloads, all claim outcomes, execution/renewal loss,
  completion failure, ACK uncertainty, single-message ordering, stop before/during
  receive/execution, signal-handler restoration and transport failure cleanup.
- `python -m app.processing_worker` was checked as a child process: exit **2**,
  with the disabled-transport message and no cloud/database calls.

```powershell
uv run --no-sync python "$env:TEMP/run-lambda-plan-offline.py" tests/test_container_worker.py tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_execution_boundary.py tests/test_lambda_processing_plan.py tests/test_processing_rule_gate.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/ec2-worker-offline-results.xml"
uv run --no-sync python "$env:TEMP/run-ec2-worker-db-tests.py" tests/test_container_worker.py tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_execution_boundary.py tests/test_processing_jobs_postgres.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/ec2-worker-db-results.xml"
uv run --no-sync python "$env:TEMP/run-lambda-plan-offline.py" tests/test_container_worker.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/ec2-worker-final-unit-results.xml"
```

The DB runner blocks SDK/session/storage access before imports, enables only
the established local UUID-schema PostgreSQL fixtures, and removes Exact test
overrides from its process environment. No application-schema migrations or
configuration-file changes were made. No full repository suite was run.

Fourteen files belong to this change: six shared-package files; three Lambda
compatibility/parser files; container-worker and PostgreSQL tests; this document
and links in the two existing foundation/integration documents. Extraction
matches the prior source after import-path/line-ending normalization. Repository,
migration 0041, lock services, processing algorithms, gates and unrelated work
are unchanged by hash. Nothing was staged, committed, pushed or deployed.
