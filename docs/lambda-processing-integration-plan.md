# Lambda processing integration plan

Task 20 now provides an opt-in local implementation of immutable stage
publication and the EC2 adapter. See
[task20-processing-integration.md](task20-processing-integration.md) for the
implemented boundaries, validation and remaining NO-GO deployment gates.
The default Lambda processing executor remains disabled.

Architecture update: [heavy processing now belongs to the EC2 container worker](container-processing-worker.md).
The storage, fencing and recovery findings below still apply. Lambda-specific
runtime placement recommendations are superseded by this architecture decision.

**Decision: do not enable real processing.** The default executor remains
disabled. This review changes documentation/tests only; it adds no stage adapter.

## Evidence and limits

Inspected the actual services, processors, repositories, locks and worker on
`feature/lambda-processing-worker`, HEAD `6d9f60d`. Findings below are verified
from code and offline contract tests. Failure scenarios are consequences of
those code paths, not observed production incidents. No AWS access, credentials,
database connections, migrations, live-object checks or runtime benchmarks were
used. No safe worst-case duration or input-size threshold has been established.
The deployed invocation limit and service maximum were not inspected; workloads
must be checked against both before any future deployment.

## Execution-path map

| Entry/call chain | Lock and transaction boundaries | Storage/blocking work and visibility |
| --- | --- | --- |
| [Worker](../app/lambda_worker/handler.py) → [service](../app/lambda_worker/service.py) → `claim_job_result` | Claim commits independently before executor entry; controlled executor checkpoints before/after callback; `finish_job` is a separate fenced transaction. | Default raises `NotImplementedError`. No pipeline is wired. Completion rejects lost/expired tokens, but cannot undo earlier effects. |
| [`start_processing`](../app/services/processing_service.py#L62) → `run_bronze_stage` | `@active_delivery` holds upload lifecycle lock. New attempt and subsequent repository writes commit separately; no enclosing Bronze publication transaction. Schema-hash reuse returns before checking active attempts. | Full Raw CSV download/read, two CSV interpretations, profiling, whole-frame Parquet serialization, lexical and normal Bronze PUTs. Raw is only read. Fixed output keys are externally visible before metadata completes. |
| [`continue_processing`](../app/services/delivery_execution_service.py#L49) → Silver → optional apply → Gold | Checks owned upload/rule context; selected policy determines dataset lock, then lifecycle and delivery locks. These span stage calls; there is no transaction spanning the entire chain. | This is a multi-operation coordinator, not a bounded-stage executor. Final public-context read may also fail after processing succeeded. |
| [`run_silver_processing`](../app/services/processing_service.py#L259) → `process_silver` | `@guarded_delivery`; verify approved pinned rules/schema. Transaction 1: attempt + `SILVER_PROCESSING`. Transaction 2 after storage: DQ run/issues + attempt success + `READY_FOR_GOLD`. Failure transaction marks `SILVER_FAILED`. | Full Bronze download/read; cleaning, conversion, validation, copies, Silver PUT and optional quarantine PUT. Exact Silver additionally reads stored bytes/checksums before DQ registration. None accepts a worker checkpoint. |
| [`apply_incremental`](../app/services/append_application_service.py#L438) → prepare → delivery manifest → current state → APPEND/UPSERT/SNAPSHOT engine → candidate validation → `_publish_metadata` | Dataset then lifecycle lock. Preparation pins application policy/DQ; RUNNING commit; delivery-manifest commit; STAGED state commit; final transaction validates state, publishes head/application and marks analytics stale. | Reads full Silver and current cumulative state; full-frame engine/Parquet; immutable delivery/candidate/outcome/manifest PUTs plus read-back validation. Objects exist before head publication. |
| [`run_gold_processing`](../app/services/processing_service.py#L648) → `process_gold` → base and mart writes | Delivery lock; verify successful DQ matches pinned approved rules. Transaction 1: attempt + `GOLD_PROCESSING`. Transaction 2: run/artifacts/catalog + attempt success + `SUCCESS`. Failure marks `GOLD_FAILED`. Zero valid rows records `SUCCESS_WITH_WARNINGS` in a transaction. | Full Silver download/Parquet read; base serialization/PUT; planning, aggregation and each mart serialization/PUT. Fixed output keys can overwrite visible bytes before catalog transaction commits. |

Supporting code: [Bronze processor](../app/processing/bronze_processor.py),
[Silver processor](../app/processing/silver_processor.py),
[Gold processor](../app/processing/gold_processor.py),
[immutable artifact store](../app/storage/incremental_artifacts.py),
[incremental preparation/publication](../app/services/incremental_application_service.py),
[repository transactions](../app/db/database.py).

### The stage selector is not a one-stage contract

Verified by invoking only the existing coordinator with mocked stages/locks:

| `continue_processing(stage=...)` | No incremental policy | Incremental policy selected |
| --- | --- | --- |
| omitted | Silver → Gold | Silver → apply → Gold |
| `SILVER` | Silver | Silver → apply |
| `GOLD` | Gold | apply → Gold |

Without a load policy, terminal association status short-circuits the chain.
With a policy, the coordinator still calls the stages; each service must resolve
its own idempotency. Therefore mapping a queued `SILVER`/`GOLD` operation directly
to this selector would silently include incremental processing.

## Retry, checkpoint and recovery classification

Every stage can exceed the current job lease: full reads, pandas/Arrow work,
SQL connection/lock waits and SDK calls have no demonstrated bounded runtime.
Incremental cost depends on cumulative head size, not just the new upload.

| Operation boundaries | Existing retry/recovery evidence | Checkpoint feasibility and visible effects |
| --- | --- | --- |
| Bronze: before Raw read → after all profile/schema/association/rule/attempt writes | Schema-hash fast path reuses physical metadata and initializes the association/rules. An active file-scoped attempt with no schema hash causes rejection. There is no equivalent automatic Bronze recovery in `delivery_lock`, whose lookup is association-scoped. | Pre-entry admission is possible. Internal hooks are absent. PUTs overwrite fixed keys; schema hash is committed before profiles/summary/association, so hash presence alone is not a complete success receipt after interruption. Repeated bytes can differ because ingestion timestamps are added. |
| Silver: after approval/readiness → after DQ/attempt/status commit | Successful association-scoped DQ at the pinned rule version returns `already_processed` and may heal status. It does **not** read/validate stored Parquet bytes on that path. An abandoned delivery attempt is marked CRASHED under the existing delivery lock before retry. | Checks can be proposed before/after bounded I/O/compute and before final publication, but no such job-aware hook exists. Silver/quarantine writes precede DQ commit and use deterministic keys. Checkpoints outside the call cannot prevent a stale in-flight PUT. |
| Apply: after pinned Silver/policy prerequisites → after trusted head/application commit | SUCCESS application short-circuits without republishing an old head. Delivery manifest pins source/checksums; retries use the immutable source. New candidates are checked against the current head. `_publish_metadata` locks application/state/dataset, compares predecessor/schema/policy, then atomically publishes head/application. | Existing durable manifest/STAGED boundaries are promising future resume points, but there is no supported worker resume/checkpoint API. Candidate objects are immutable (`IfNoneMatch='*'`, read-back/checksum) and may orphan on failure. The final DB transaction has no job-token fence. Do not invoke `_publish_metadata` alone to skip storage validation or gates. |
| Gold: after successful pinned DQ → after catalog/attempt/status commit (or explicit zero-row skip) | Reuses a matching source-DQ run when catalog completeness passes; otherwise reuses its run during repair. `_is_gold_publication_complete` checks artifact-name/catalog/grain/column metadata, **not S3 object existence or bytes**. | No internal checkpoint parameter. Base/mart keys are deterministic despite an “attempt-specific” comment; attempt ID is a column, not part of these keys. Partial writes or retries can overwrite objects already referenced by catalog rows. Catalog rollback cannot restore those bytes. |

Successful SQL transactions are externally visible metadata effects; S3 PUTs
are externally visible independent effects, not rollbackable by PostgreSQL.
Session advisory locks are released by `finally` or session death, not by stage
transaction rollback. `dataset_lock`, `lifecycle_lock` and `delivery_lock` use
nonblocking acquisition and ContextVar reentrancy. They coordinate participating
live sessions, not storage fencing after a session dies. A local ContextVar can
still say “held” after connection loss. Attempt constraints prevent simultaneous
active metadata records; they do not fence stale SDK writers.

## Ranked risks and required changes (proposed, not implemented)

1. **Critical — no job fence at side-effect/publication boundaries.** The
   boundary's final checkpoint and `finish_job` occur after service side effects.
   A lease can be lost during a blocking call; a post-check catches loss but
   cannot undo a write. Real metadata publishers must accept a server-trusted
   execution guard, lock/check the job token and current authorization in the
   *same publication transaction*, hold the job row through commit, and recheck
   DB-clock expiry before commit. Establish a consistent lock order with existing
   application/head locks. A guard before entering a transaction is insufficient.
2. **Critical — fixed-key Bronze/Silver/Gold writes are not fenced by leases.**
   Checking a token then PUTting is a check/use race. Before Lambda retry is
   enabled, design immutable attempt/candidate objects and atomic validated
   metadata references (or an equivalently proven storage fencing protocol).
   Preserve rule pins, lineage and existing consumers via a separately reviewed
   storage/publication change. No key changes are made here.
3. **High — no bounded interruptible compute/I/O interval.** No pipeline method
   consumes the new boundary. Whole-frame pandas/Arrow and SDK calls cannot renew
   from the same thread while blocked. Define measured size/row/head/mart limits,
   SDK/SQL deadlines and bounded chunks. Otherwise keep that operation off Lambda.
   No generic wrapper or larger renewal interval solves unbounded work.
4. **High — recovery receipts are incomplete for new orchestration.** Job rows
   pin upload/file/scope, not an immutable stage attempt/rule/DQ/policy receipt.
   Add a reviewed durable stage-intent/receipt association before enabling retries.
   Verify complete artifacts and matching pins before marking the job successful;
   neither schema hash, DVF SUCCESS nor catalog rows alone establish this.
   Bronze also needs a reviewed file-scoped abandoned-attempt recovery protocol
   and completeness repair; never blindly clear a PROCESSING row.
5. **High — operation granularity and dependencies.** `CONTINUE` is multi-stage;
   even selector-specific calls can apply state. A distinct APPLY operation is
   not in migration 0041's allowed operations. Its addition and dispatch contract
   need separate review; do not disguise APPLY as GOLD or change 0041 here.
6. **High — delivery and deployment guarantees remain absent.** No outbox,
   scheduled reconciliation or bounded poison-message/redrive policy exists.
   Handler does not wire Lambda time to a production executor. Loss of a queue
   delivery, worker termination and completion uncertainty can strand jobs.
7. **Medium — import-time SDK creation and diagnostic safety.** Silver/Gold
   construct SDK clients at module import. In particular, `ExecutionBoundary.stage`
   imports locking services before its first checkpoint, indirectly importing
   processing modules. Review lazy/injected client creation before deployment.
   Existing stage errors can contain internal details; keep them out of worker
   responses/logs and persist only safe job codes.

## Recommended bounded-job strategy

This is a proposed contract requiring review, not an enabled executor:

1. Authenticate admission on the application server. Persist an immutable stage
   intent with upload, association/schema, rule version and, where applicable,
   DQ/policy/application pins. Queue only the committed job UUID. Rehydrate the
   authorized actor from server data; never accept user IDs or source paths from
   SQS. Keep stage readiness distinct from upload completion.
2. Use one admitted bounded operation per job: **Bronze**, then human/approved
   rule gate, **Silver**, optional **Apply**, then **Gold**. A successor must be
   durably scheduled only after its prerequisite receipt commits. No automatic
   rule approval, no dataset-wide pending sweep, and no monolithic `CONTINUE`
   job. With no incremental policy, omit Apply. Preserve the current apply-before-
   Gold dependency; Gold currently consumes per-delivery Silver, not the entire
   cumulative state. Cumulative analytics is a separate operation.
   Success must be stage-specific: Bronze can finish inspection at
   `AWAITING_RULES`; Silver's committed DQ receipt can finish at
   `READY_FOR_GOLD`; Gold's zero-row `SUCCESS_WITH_WARNINGS` is an explicit
   valid outcome. A future adapter must validate and translate the service's
   result/receipt, not discard its returned dictionary or require terminal
   dataset-file SUCCESS for every operation.
3. Retain existing source/approval/state checks. Direct Silver/Gold functions do
   not replace upload ownership/lifecycle validation: use the server-derived
   scope and dataset → lifecycle → delivery locks around the approved operation.
   Boundary reentrancy must stay on the same context/thread. Do not share lock
   ContextVars with a competing thread or move processing inside one giant SQL
   transaction. Retain independent stage commits and Exact V2 restrictions.
4. Before each bounded chunk, run time/source/lease checks and synchronous
   renewal. The stage budget, completion reserve and preparation overhead must
   fit both lease and invocation time. After each blocking call, check again
   before admitting another effect. Add publication guards at the exact SQL
   transactions listed above. Never interpret these checks as cancellation of
   the call already in progress.
5. Resolve a matching completed receipt before redoing work. Silver needs its
   DQ/rule pin and verified bytes; Gold needs matching DQ and verified complete
   artifacts/catalog or an explicit zero-row skip; Apply needs its SUCCESS
   application/result-state receipt and must not rewind a newer head. Then finish
   the newly claimed job with its current token. If evidence is incomplete or
   stale, use reviewed repair/resume logic rather than blind replay.

| Condition | Required handling |
| --- | --- |
| Insufficient remaining time | Admit no new stage/chunk. Persist retry/defer when the token remains valid; fail the current delivery. Reserve enough time for cleanup/metadata. Awaiting rule approval should not burn execution retries. |
| Lease lost before admission | Start no work; close the boundary and release locks. The stale token cannot complete/renew. |
| Lease lost during a call | No subsequent stage/write/publication; a blocking call may already have completed an external effect. Reconcile receipts/orphans. Only publication/storage fencing can prevent stale visibility. |
| Stage already complete | Verify exact intent/receipt/artifacts under the proper locks; do not infer success from a generic status or latest unrelated run. |
| Stage success, job completion fails | Do not ACK on an uncertain completion. A later claim reconciles the committed stage receipt and completes the job without rerunning successful work. The current schema does not yet guarantee an unambiguous receipt association. |
| Repeated SQS delivery | ALREADY_SUCCEEDED or durable terminal rejection/failure may ACK. CLAIMED requires durable completion; BUSY/NOT_DUE/NOT_FOUND remain retryable without independent durable scheduling. |
| Lock contention | Fail/defer conservatively and release acquired resources; never mark stage success. Record no duplicate stage attempt. A contention-vs-processing-failure retry-budget policy still needs review. |
| Work exceeds invocation/service maximum or cannot be chunked | Reject Lambda admission or route to separately approved durable long-running compute. Do not chain partial mutable work or merely increase the lease. No cloud resource is configured here. |

## Focused verification

`tests/test_lambda_processing_plan.py` exercises the actual continuation
coordinator with mocked locks/stages, Silver's real metadata reuse path, Gold's
real catalog completeness helper, pre-admission budget exhaustion and the
inability of a post-check to undo a simulated in-flight side effect.
Processors are never executed through Lambda. SDK client construction is
stubbed **before test collection/imports**; all actual SDK/storage and PostgreSQL
connection attempts are blocked. The test file also installs offline fixtures
for standalone collection.

The boundary proposals involving internal publication fences/immutable storage
have no existing safe executable adapter. They are intentionally documented
rather than implemented. Mock probes do not establish S3/database crash safety,
deployment duration limits, live checksum integrity or production readiness.

Validation on 2026-11-01: **79 passed, 0 failed/errors/skipped, 0.67 s**:
12 new plan probes, 20 boundary, 25 handler, 18 worker adapter and 4 existing
rule-gate cases. No PostgreSQL suite/full regression was needed for these
documentation and mock-test-only changes.

```powershell
uv run --no-sync python "$env:TEMP/run-lambda-plan-offline.py" tests/test_lambda_processing_plan.py tests/test_execution_boundary.py tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_processing_rule_gate.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/lambda-integration-plan-results.xml"
```

The retained temporary runner stubs session creation before importing the
processors, prohibits real SDK session/client creation, replaces PostgreSQL
connect with a failing sentinel, disables DB tests and removes Exact overrides
from its own process environment. It changes no configuration files. No new
production code, migrations, staging, commit or push occurred.
