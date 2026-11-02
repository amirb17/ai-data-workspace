# Durable processing jobs foundation

Task 20 follow-up: the opt-in EC2 processing integration, immutable publication,
admission/outbox and manual deployment gates are documented in
[task20-processing-integration.md](task20-processing-integration.md). The
historical validations below describe their original checkpoints. Execution
and dispatch remain disabled by default; production enablement is NO-GO pending
the listed deployment/security acceptance steps.

Artifact safety review: [publication and interruption-recovery contract](artifact-publication-recovery-contract.md).
Real processing remains NO-GO pending immutable publication, transactional
fencing and receipt-based recovery; the new verifier is not wired to services.

Current architecture: [shared execution and EC2 container worker](container-processing-worker.md).
Heavy processing is assigned to EC2; Lambda retains lightweight API/validation
and dispatch/recovery responsibilities. Both executors remain disabled by default.

Latest integration review: [Lambda processing integration plan](lambda-processing-integration-plan.md).
Real processing remains disabled; the plan identifies the required publication
fencing, immutable storage and stage-receipt work before an executor can be enabled.

## Audit and migration integration

Implemented on `feature/lambda-processing-worker`, starting at `6d9f60d`.
Migrations are ordered SQL files, not Alembic revisions. The established
`tests/test_rule_approval_postgres.py::pg` fixture discovers only sorted
`migrations/*.sql`, applies them in a UUID temporary schema using startup
`search_path`, and drops only that schema in its cleanup. Pending ownership
migration 0028 is intentionally outside this discovery path. No startup
auto-migration mechanism or migration ledger was found in the application.

`0041_processing_jobs.sql` follows 0040 and uses an explicit transaction, as
existing additive migrations do. It creates only a new table and its indexes;
it changes no existing rows, algorithms, locks, constraints or Exact V2 gates.
It is a once-only migration, not an idempotent deployment script. PostgreSQL
13+ provides `gen_random_uuid()` without installing an extension. Production
and development application require a separate reviewed migration application;
this task applies it only through isolated test fixtures.

Ownership is the current `users.owner_key` matching **both** workspace and
dataset owners, with dataset/workspace membership verified. Upload `user_id`
is provenance, not current authorization. Canonical `owned_upload` requires
`UPLOADED` with an existing physical file; legacy completed uploads need not
have `completed_at`. New staged sessions additionally require durable
completion metadata. Jobs also reject archived uploads and disabled actors.
Admission and claim lock the joined source/ownership rows `FOR SHARE` through
their transaction. A claim revalidates ownership and the pinned source IDs.

`processing_attempts` remains stage execution history with its active-attempt
unique indexes. Existing dataset, lifecycle and delivery advisory locks still
protect real execution. The job lease does not replace those mechanisms.

## Model and repository contract

- Database-generated UUID `job_id`; foreign keys to requester, upload,
  workspace, dataset and physical file. The latter three pin admitted scope.
- Operations: `BRONZE`, `CONTINUE`, `SILVER`, `GOLD`, describing existing entry
  points/stage selections. No operation executes in this repository.
- Idempotency is unique over `(requested_by, operation, idempotency_key)`.
  Concurrent admission returns the same row; another upload, source scope or
  retry budget with that key raises `IdempotencyConflict`. New intentional
  submissions require new keys. Keys survive terminal completion.
- Lifecycle: `QUEUED -> RUNNING -> SUCCEEDED | FAILED | RETRY_WAIT`.
  `CANCELLED` is reserved terminal schema vocabulary; cancellation is not yet
  exposed. Terminal states cannot be claimed or completed again.
- `available_at` controls retry eligibility. Claims increment `attempt_count`
  (including reclaimed leases), bounded by immutable `max_attempts` (default
  3). Expired final attempts become `FAILED/LEASE_EXPIRED` on the next claim.
- Claim locks the specific job with `FOR UPDATE SKIP LOCKED`. The compatibility
  `claim_job` API returns the full claimed row or `None` as before. Workers
  should use `claim_job_result` for the explicit outcomes below. Claims
  revalidate source eligibility. Invalid source becomes `FAILED/SOURCE_INELIGIBLE`.
- Random UUID lease tokens fence metadata updates. Database-clock lease
  expiry is bounded to 1–900 seconds. Reclaim rotates the token and increments
  `recovery_count`; `last_claimed_at` records the most recent claim.
- `finish_job` requires the current unexpired token, checking expiry after
  obtaining the row lock. Explicit retryable failures schedule `RETRY_WAIT`
  only within budget. Delay is bounded to 0–86400 seconds. Otherwise failure
  is terminal. Stale/expired/duplicate completion returns `None`.
- Only allowlisted result and failure codes persist: no raw exceptions,
  credentials, object paths or arbitrary JSON payload. All lifecycle times
  use PostgreSQL `TIMESTAMPTZ`; completion is present only for terminal rows.
- Repository calls compose with the existing `repository_transaction`.
  Inner savepoints and outer rollback preserve atomicity. Existing processing
  services and their transaction/advisory-lock behavior are untouched.

This is a trusted backend repository boundary, not row-level security. A
caller must provide a server-authenticated user ID, never a principal or
upload ID asserted by an SQS body. Queue consumers will resolve persisted job
IDs. Database roles that can issue arbitrary SQL remain privileged. The new
table's foreign keys establish existence; repository admission/claim enforce
cross-row ownership and pinned membership. There are no public routes here.

## Remaining architectural decisions

No SQS dispatch, outbox, scan/reaper, production stage executor, cancellation
API, API-key issuance, cloud resources or migration deployment is included.
Future external credentials must resolve to an authenticated application user
and explicit permissions; client IDs must not substitute for ownership.

Leases fence job **metadata**, not side effects: an expired worker can still
be alive. Before enabling execution, integrate the existing lifecycle/dataset
locks and stage idempotency, revalidate authorization under execution locks,
choose heartbeat/lease policy, and handle successful processing followed by
lost job completion. Reclaim is explicit on delivery of that job ID; eventual
redelivery/reconciliation and dispatch atomicity remain future work.

Different keys can admit separate jobs for the same upload. Existing stage
and dataset locks remain essential; this task does not claim exactly-once
processing. The typed claim result distinguishes durable job state from
contention before a future adapter decides whether to acknowledge a queue
message. Ownership changes after admission invalidate the original request;
the new owner must submit a new job. Ownership changes after claim still
require execution-time checks.

Stage prerequisites (for example rule approval before Silver) remain the
responsibility of existing processing services at execution time. Admission
asserts source completion and authorization, not stage readiness.

## Worker outcome contract

`claim_job_result(job_id, lease_seconds=120) -> ClaimResult` resolves and
optionally claims in the same transaction. It shares the complete claim path
with `claim_job`; no second state read is required by an adapter. The result
contains only a `ClaimOutcome`, optional `JobLease`, and optional `retry_at`.
The immutable lease contains job UUID, token, expiry and attempt number. It
omits principal/source identifiers, idempotency keys and raw job metadata.
The token is an internal capability: excluded from `repr`, but must also be
excluded from logs and any serialization exposed outside the trusted worker.
This is not a public job-status or authorization endpoint.

| Outcome | Meaning | Adapter policy |
| --- | --- | --- |
| `CLAIMED` | A new lease was acquired, including eligible expired-lease recovery. | Execute only after claim commit. ACK only after the injected executor returns successfully and token-fenced completion commits. Executor exceptions fail this delivery even if failure persistence exhausts the budget; a later delivery can ACK the terminal state. |
| `ALREADY_SUCCEEDED` | Committed terminal success already exists. | Safe to ACK the duplicate without executing. |
| `TERMINAL_FAILED` | Terminal failure, exhausted lease budget, or reserved `CANCELLED` state. | Safe to ACK without executing; retain the durable failure for monitoring/operator review. A new intentional request needs a new job/key. |
| `BUSY` | Row is locked by another transaction, or its running lease has not expired. | Retry with backoff/visibility handling. `retry_at` is the observed lease expiry when known, otherwise absent. Do not treat this as successful processing. |
| `NOT_DUE` | A queued/delayed retry is not yet eligible. | Retry at/after `retry_at`. ACK only if a separate durable scheduled redelivery/reconciliation guarantee already exists. |
| `NOT_FOUND` | No committed visible job exists for that UUID at this resolution. | Do not execute or assume success. Bounded retry, then an explicit unknown-job quarantine/DLQ policy and alert; no unconditional ACK policy is implemented here. |
| `SOURCE_INELIGIBLE` | Ownership/source validation failed and durable `FAILED/SOURCE_INELIGIBLE` exists. | Safe to ACK without execution; monitor the safe reason. Replays return the same outcome. |

Terminal results carry no lease or retry time. `BUSY` and `NOT_DUE` carry no
lease token. Lock contention uses a nonblocking MVCC existence lookup after
`SKIP LOCKED` to distinguish `BUSY` from `NOT_FOUND`; it deliberately does not
infer terminality from a row another transaction owns. Outcomes describe the
observed state, not a promise that it will remain unchanged. `retry_at` is an
absolute database timestamp and a scheduling hint, not a reserved future slot.

An unexpired running lease returns `BUSY` even if ownership has since changed;
it does not authorize that worker to continue. Once reclaim is eligible, claim
revalidates ownership and pinned scope before issuing another token. Terminal
success needs no source revalidation because no new execution is authorized.

Database errors, malformed UUIDs and invalid lease parameters raise rather
than becoming misleading outcomes. A future adapter must validate its envelope
and handle transient repository errors as retryable, without ACKing them.
As with the compatibility API, results inside an outer `repository_transaction`
are provisional until that outer transaction commits. Never enqueue before
admission commit, execute before claim commit, or ACK before terminal commit.

Reconciliation is still required for lost deliveries, expired leases that
are never redelivered, due retries beyond queue retention/redrive bounds, and
processing success followed by lost completion metadata. Until such a durable
mechanism exists, ACKing `BUSY`, `NOT_DUE`, or `RETRY_WAIT` can strand work.
No scheduler, background heartbeat or dispatcher is implemented. The handler adapter below
implements acknowledgment decisions without guaranteeing another delivery.
Lease rotation and completion CAS fence metadata only: an expired worker can
still perform side effects. Existing processing locks, authorization checks and
stage idempotency must protect those effects when execution is integrated.

## Lambda execution adapter

`app/lambda_worker/service.py::execute_job(job_id, executor=...)` is the trusted
synchronous worker boundary. A normal return means this **delivery** may be
acknowledged; an exception means retry. The handler preserves
`{"batchItemFailures": [{"itemIdentifier": "..."}]}` for individual failures.
It returns no raw repository metadata or exception details. Logs contain only
a generic failure message, not executor/driver tracebacks or lease tokens.

Messages must be JSON objects containing exactly one `job_id` string parseable
as a UUID. Upload IDs, principals, operations or execution instructions cannot
be supplied in the body. Invalid bodies are individual batch failures. Invalid
record envelopes (unsupported source or missing message identifier) reject the
invocation because they cannot safely participate in this SQS batch contract.
Malformed UUIDs never reach the repository. A valid UUID still must resolve to
an eligible persisted job; possession of a UUID alone grants no execution.

The adapter consumes all seven outcomes above. It uses a fresh `Context` so
claim and completion cannot borrow an inherited repository transaction and
return before commit. `CLAIMED` requires a matching, nonempty lease; only then
is the injected executor called with that trusted `JobLease`. A synchronous
`None` return signifies successful completed execution. Any other return is
rejected. The adapter then calls `finish_job` with the same job ID and token;
only a committed `SUCCEEDED` result allows acknowledgment. Missing/rejected
completion, stale/expired tokens and completion database errors all fail the
delivery. It does not convert completion uncertainty into an execution failure:
processing side effects might already have succeeded.

On an ordinary executor exception, the adapter attempts
`finish_job(..., failure_code='PROCESSING_FAILED', retry=True,
retry_delay_seconds=30)`. Within budget this commits `RETRY_WAIT`; exhaustion
commits `FAILED`. Raw exception text is never persisted. The current delivery
still fails in either case, or if failure persistence is unavailable/stale.
A later delivery sees `NOT_DUE`, retries when due, or acknowledges terminal
failure. The 30-second delay is a job eligibility policy, not an SQS dispatch
or visibility guarantee. Abrupt termination/BaseException can leave `RUNNING`
until expiry and requires redelivery/reconciliation.

**Default execution still fails closed:** `_not_implemented` raises explicit
`NotImplementedError`. No Bronze, Silver or Gold service is called. A claimed
default invocation records a retryable failure when possible and never returns
a successful acknowledgment for that claim. Repeated attempts consume the job
budget and can become terminal, so this placeholder must not be connected to
live work before an approved executor is implemented.

The original `executor(lease)` injection remains compatible and must finish
within the lease; it is not a production-safe long-running executor. The opt-in
cooperative boundary below adds renewal and admission checks. Neither interface
can undo side effects from an overrun. Long-running processing is not supported
merely because Lambda can run longer than the lease.

`BUSY`, `NOT_DUE` and `NOT_FOUND` always fail the delivery in this implementation;
there is no configured durable-schedule bypass. Before deployment, finalize a
bounded poison-message/redrive policy for unknown or malformed jobs, visibility
timeout/backoff, event-source partial-batch configuration, and reconciliation
for missed or exhausted redeliveries. No receive-count limit or automatic
redelivery is assumed or provisioned by the application. The adapter is not
ready for real processing deployment yet.

## Cooperative lease and execution safety

`renew_job_lease(job_id, token, lease_seconds=120)` locks the job row before
checking PostgreSQL `clock_timestamp()`. Only a matching, unexpired `RUNNING`
lease renews. It returns a `JobLease` or `None`, retains token/attempt/recovery
counts, and sets expiry to the greater of existing expiry and database time
plus the requested 1–900 seconds. Concurrent shorter renewals cannot reduce a
longer lease. Expired, superseded, missing and terminal jobs cannot renew.
Renewal participates in the existing savepoint/outer-transaction contract; an
outer rollback undoes it. It changes neither migration 0041 nor attempts.

`get_execution_scope` checks token, live lease, pinned source and current
ownership in a short transaction. Its internal result contains only IDs and
operation, not storage paths or credentials. An optional minimum lease duration
is checked against database time **after** source/ownership lock acquisition.
Renewal alone does not grant authorization: a worker must also revalidate scope.

The opt-in `ExecutionBoundary` receives a trusted lease and a required remaining
time callback (eventually `context.get_remaining_time_in_millis`). It provides:

- `checkpoint(required_ms=..., renew=True)` to synchronously revalidate,
  renew, and verify source/lease again, including a minimum DB-clock lease
  budget. Use between bounded processing chunks. A failed checkpoint permanently
  stops this boundary, even if the caller catches the exception.
- `stage(required_ms=...)` to check the invocation budget, renew, acquire the
  existing dataset → lifecycle → delivery locks, revalidate after preparation,
  and admit the caller's bounded work. It checks again on successful exit and
  releases all contexts on failure. No processing callback is called by this
  context manager. Without a Bronze association it holds dataset/lifecycle
  locks and omits the nonexistent delivery lock.

The default completion reserve is 5000 ms, renewal interval 120 seconds.
Renewal requests are capped by sampled invocation time minus the reserve
(without shortening an already longer valid lease).
Declared stage work plus reserve must fit within the renewal window and the
remaining invocation time. DB/connection/lock preparation consumes that same
time budget; the callback is sampled again before admission. These are safety
budgets, not measured worst-case stage guarantees. There is no background
thread, watchdog, timer sleep or automatic heartbeat. During a long operation,
the eventual executor must split work into bounded chunks and call renewing
checkpoints **before** expiry; expired leases cannot be revived. A blocking
operation longer than the renewal window is not yet supported.

`service.execute_job(..., boundary_executor=..., remaining_time_ms=...)` is an
opt-in injection interface, exclusive with the original lease executor. The
adapter checks the boundary before calling that executor and after it returns,
so a swallowed checkpoint failure cannot become successful completion. The
normal handler still uses the disabled default executor. No real processing
path is selected or connected by this change.

### What the existing recovery mechanisms actually guarantee

Audit of the current services found these recoverable boundaries:

- Bronze reuses existing physical schema metadata/associations on repeat calls.
  This supports re-entry after committed Bronze metadata, not cancellation of
  an arbitrary storage/profile operation.
- Silver uses stage attempts, approved rule pins and committed DQ outputs.
  `delivery_lock` marks abandoned active attempts `CRASHED` after obtaining the
  actual lock. Retry must use those existing service checks.
- Gold checks publication completeness and repairs incomplete publication;
  a successful attempt alone is not proof all outputs were published.
- Incremental applications use dataset/lifecycle locks, immutable delivery
  manifests and transactional publication/head checks. Re-entry must use their
  existing retry/recovery services; job completion loss must not blindly replay
  processing. Exact V2 gates remain unchanged and disabled.

None of these stage functions currently accepts cooperative cancellation or
this new job token. The boundary prevents **future admission** at checkpoints;
it cannot interrupt a call already running, cancel a blocked DB/storage call,
undo published artifacts, or protect a session whose advisory-lock connection
has died. Existing `delivery_lock` recovery is intentionally retained and can
mutate abandoned-attempt metadata during lock acquisition. The boundary checks
eligibility before that acquisition and again before yielding work; it does
not make recovery atomically fenced by the job lease.

Source/ownership checks are snapshots, not locks held throughout external I/O.
Real publication must still revalidate authorization and execution ownership at
its transactional commit boundary, retain stage idempotency and advisory locks,
and define cooperative cancellation after lease loss. Abrupt termination,
network partitions and successful side effects followed by lost completion
still require reconciliation. Driver/network deadlines and per-stage measured
time budgets must be established before real execution is enabled.

## Validation — 2026-11-01

Initial job/Lambda run: **30 passed, 1 failed, 0 skipped/errors**, 96.98 s.
The new outer-transaction rollback test exposed premature commit when a
savepoint was opened before the borrowed connection's outer transaction had
started. Fixed solely in the new repository by explicitly starting the outer
transaction before its savepoint. The shared database helper was unchanged.
All 12 existing Lambda handler tests passed in that run.

Final run: **38 passed, 0 failed/errors/skipped**, 93.85 s. This includes 27 job
tests, five existing PostgreSQL upload-session tests, two existing unattached
upload authorization tests and four existing processing rule-gate tests.
The job tests cover concurrent idempotent admission, conflicting keys,
ineligible sources, ownership/pin revalidation, an actually held competing row
lock, duplicate claims, lease recovery and stale-token fencing, retry delay
and exhaustion, terminal behavior, outer rollback and database-error rollback.

Exact commands (PowerShell; the temporary runner is retained for review):

```powershell
$runner = Join-Path $env:TEMP 'run-processing-jobs-tests.py'
uv run --no-sync python $runner tests/test_processing_jobs_postgres.py tests/test_lambda_worker.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/processing-jobs-results.xml"
uv run --no-sync python $runner tests/test_processing_jobs_postgres.py tests/test_upload_sessions_postgres.py tests/test_unattached_upload_authorization.py tests/test_processing_rule_gate.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/processing-jobs-final-results.xml"
```

The runner enables `RUN_DB_TESTS=1`, disables live BigQuery, removes Exact V2
test overrides from its own process environment, disables bytecode generation,
and provides the existing tests namespace. It asserts the configured database
host is local before using `hostaddr=127.0.0.1` and a 10-second connection
timeout. The original configuration and environment files are not modified.
Only the existing temporary-schema fixtures perform DDL/cleanup. No AWS calls
or development-schema migrations were performed. No full regression was run.

File-hash verification found no changes to any pre-existing tracked or
untracked file. Four files were added; the branch and HEAD stayed at the
starting checkpoint. Nothing was staged, committed or pushed.

### Explicit worker outcome hardening — 2026-11-01

Added the backward-compatible typed result without changing migration 0041,
admission, completion, existing gates or stage execution. Only the repository,
this document and its PostgreSQL test file changed in this follow-up.

Validation: **55 passed, 0 failed/errors/skipped**, 110.02 s: all 27 original
job tests, 16 new outcome cases, and all 12 existing Lambda handler tests.
The new cases cover every outcome, actual row-lock overlap, concurrent initial
claims and expired-lease recovery, stale success/failure completion attempts,
ownership/account/archive changes, durable source rejection, delayed queued
and retry jobs, cancellation/exhaustion, rollback and propagated database errors.

```powershell
uv run --no-sync python "$env:TEMP/run-processing-jobs-tests.py" tests/test_processing_jobs_postgres.py tests/test_lambda_worker.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/processing-jobs-outcomes-results.xml"
```

Used the same isolated runner and fixtures described above; no development
schema migration or AWS operation occurred. No full regression was run.
All pre-existing files outside these three files were preserved by hash.

### Lambda execution adapter validation — 2026-11-01

**93 passed, 0 failed/errors/skipped**, 125.96 s: 25 handler cases, 18 adapter
unit cases, and 50 isolated PostgreSQL cases. All 43 prior repository cases
remain passing. Existing handler fixtures now use real UUID syntax; their
partial-batch and failure assertions are retained.

Real PostgreSQL adapter cases verify durable success and duplicate delivery,
disabled default execution, retryable executor failure, delayed retry,
expired/replaced tokens rejecting completion, ownership rejection in a mixed
batch, and independent completion despite rollback of an inherited transaction.
Unit tests cover all seven outcomes, invalid claim contracts, persistence
failures, synchronous execution requirements and safe logging. No real
processing stage executes in these tests.

```powershell
uv run --no-sync python "$env:TEMP/run-processing-jobs-tests.py" tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_processing_jobs_postgres.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/lambda-adapter-results.xml"
```

This follow-up changes `handler.py`, the two existing worker/job test files,
and this document; it adds `app/lambda_worker/service.py` and
`tests/test_lambda_worker_service.py`. The repository implementation, migration
0041, Phase 11G gates and all unrelated work remain unchanged by file hash.
Only UUID temporary-schema fixtures apply/clean up DDL; no development-schema
migrations, AWS provisioning, stage execution, full regression, staging,
commits or pushes were performed. Deployment remains blocked on the real
executor, lease/cancellation policy, bounded redrive and durable reconciliation.

### Cooperative lease/boundary validation — 2026-11-01

The broader focused run passed **120 tests**, 0 failed/errors/skipped,
237.08 s (60 PostgreSQL, 17 boundary, 25 handler, 18 adapter cases). After final
admission-guard and renewal-budget refinements, the final targeted run passed
**64 tests**, 0 failed/errors/skipped, 16.30 s: all 20 boundary unit cases,
all 43 existing handler/adapter cases and a new controlled-worker PostgreSQL
case. The runs overlap; together they cover **124 distinct passing cases**.

Tests verify DB-clock renewal, interval validation, wrong/expired/superseded
tokens, terminal jobs, concurrent renewal/claim behavior, actual row-lock
exclusion, outer rollback, revoked ownership, minimum lease duration, Lambda
time exhaustion and preparation latency, sticky stop behavior, swallowed stop
exceptions, and renewal capped by invocation budget. Real PostgreSQL probes
verify dataset/lifecycle/delivery locks are held during a synthetic stage and
released on lease loss; no processing attempts are created. The controlled
adapter renews under those locks and commits completion without invoking a
processing stage. Original job and worker cases remain passing.

```powershell
uv run --no-sync python "$env:TEMP/run-processing-jobs-tests.py" tests/test_execution_boundary.py tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_processing_jobs_postgres.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/lease-boundary-results.xml"
uv run --no-sync python "$env:TEMP/run-processing-jobs-tests.py" tests/test_execution_boundary.py tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_processing_jobs_postgres.py::test_controlled_worker_renews_under_locks_and_commits_completion -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/lease-boundary-final-results.xml"
```

Changed this follow-up: job repository, worker service, PostgreSQL tests and
this document; added `execution_boundary.py` and `test_execution_boundary.py`.
Handler, migration 0041, existing advisory-lock/recovery services, processing
algorithms and all unrelated work remain unchanged by hash. No background
renewal thread, AWS access/configuration, credentials, application-schema DDL,
full regression, staging, commit or push occurred. Real pipeline integration
remains intentionally disabled pending architectural review.
