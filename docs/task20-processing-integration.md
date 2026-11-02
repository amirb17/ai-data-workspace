# Task 20 — durable processing integration

Status: **NO-GO for production enablement**. Local integration is implemented;
deployment privileges, separated verifier operation, infrastructure policies,
capacity limits and explicit deployment approval remain release gates. Nothing
in this task deploys infrastructure, creates roles, applies application-schema
migrations, or turns on a real worker. Branch: `feature/lambda-processing-worker`,
starting checkpoint `2a04884`.

## Execution architecture

1. Opted-in upload completion reads a bounded, exact staging VersionId, hashes
   the bytes, writes a unique conditional Raw object, verifies its returned
   VersionId, and records that source identity with upload completion. PostgreSQL
   and S3 remain separate systems: an unsuccessful commit may leave an orphan.
2. Authenticated FastAPI admission (`POST /files/uploads/{id}/jobs`) or the
   injected-authenticator Lambda admission callable uses `task20_admit`.
   Current ownership of both workspace and dataset is required. Admission pins
   stage inputs, creates an idempotent job and schedules its outbox atomically.
   Messages contain **only** the persisted job UUID. No message supplies an
   actor, upload, source key, executable operation or verification evidence.
3. `dispatch_one` leases a due outbox row, commits that claim, calls an injected
   SQS client, and records delivery after the send. An uncertain send can create
   a duplicate. Job identity and publication receipts resolve that ambiguity.
   Dispatch runs in an independent context so a caller's outer repository
   transaction cannot defer the claim/delivery commits until after the send.
   Queued/retry jobs and running lease expirations are durably rescheduled by a
   database trigger; an independently supervised dispatch loop is still required.
4. `SqsTransport` receives one message. `SerialWorker` uses the shared seven-outcome
   claim contract. It checks source authority, renews the token-fenced job lease
   and, when supplied, SQS visibility at cooperative checkpoints. Malformed,
   busy, not-due and unknown deliveries are deferred, never interpreted as success.
5. The opt-in `IntegratedExecutor` takes the existing dataset → lifecycle →
   delivery locks. Bronze additionally takes a distinct physical-file lock.
   It derives the persisted stage contract, verifies matching published evidence
   before reuse, then invokes the **existing** processing service and algorithms.
6. A context-local I/O adapter redirects fixed processor keys to a unique
   `artifact-candidates/<candidate UUID>/` namespace. Conditional writes and
   exact-version readback precede a registered immutable manifest. A trusted
   verifier resolves only the registered candidate UUID and validates the bytes.
7. Existing profiles/schema, DQ/issues, Gold catalog, processing-attempt status
   and the generation-aware publication receipt/head commit together. SQL checks
   the current job token, database-clock expiry, ownership, input pins and
   predecessor receipt. Expiry is checked again after database waits and writes.
8. A SILVER job with a pinned load policy invokes the existing incremental
   coordinator under the same locks. Its immutable delivery/state manifests and
   checksum validation remain in use. A guarded lineage bridge requires the
   exact published Silver identity and DQ hash. Final dataset-state CAS is also
   job-fenced. Gold requires a successful application when a policy is selected.
9. Job completion uses the existing token-fenced `finish_job`. SQS delete happens
   only after durable completion, or when a claim resolves to an already durable
   success/terminal failure/source rejection. Claim success alone never ACKs.

The Lambda processing handler's default executor and the container CLI still
fail closed. The real adapter is injected only into the EC2 worker in a reviewed
bootstrap; no Bronze/Silver/Gold execution is wired into Lambda. Admission and
dispatch functions can serve lightweight Lambda wrappers, but no public AWS
authorizer, cloud entry-point wiring or infrastructure is deployed here.

## Stage and publication map

| Stage | S3 work before SQL publication | Atomic PostgreSQL publication | Retry/recovery |
| --- | --- | --- | --- |
| Upload/Raw | Bounded staging VersionId read; unique Raw write and checksum/version readback | Upload completion and source registry identity | Completion result is authoritative; failed SQL may leave an unreferenced Raw object |
| Bronze | Existing CSV inference/profiling and lexical preservation; immutable data + lexical Parquet + manifest | Physical schema/profiles/summary, dataset version/association, rule linkage, attempt SUCCESS and Bronze receipt/head | Matching evidence is read back and reused; only mapped abandoned attempts are recovered under the physical-file lock |
| Silver | Existing approved cleaning/transformation/DQ algorithms; immutable valid and optional quarantine Parquet + manifest | DQ run, issue counts, stored SHA, READY_FOR_GOLD, attempt SUCCESS and Silver receipt/head | Pins Bronze receipt and approved rule version; never reads arbitrary latest Bronze; approval/source changes fail closed |
| Incremental | Existing immutable delivery snapshot, candidate state Parquet, outcomes and state manifest, including lineage/checksums | Existing state/application metadata and dataset head CAS through a job-fenced function | Successful application reused; interrupted candidates rebuilt/revalidated by existing recovery logic; no overwrite of a newer head |
| Gold | Existing base and planned marts; immutable manifest; explicit skip proof for zero valid input | Gold run, artifact/model/column catalog, final association state, attempt SUCCESS and Gold receipt/head | Pins Silver/DQ, profiles, model configuration epoch and applicable dataset-state dependency; historical generations remain referenced |
| Warehouse | No new warehouse execution in this adapter | Existing immutable state manifests, source vectors and warehouse CAS contracts remain authoritative | Compatibility coverage exercises existing warehouse registration/CAS; no live warehouse was accessed |

S3 writes cannot be rolled back by PostgreSQL. Before publication, objects are
staged and unreferenced by authoritative metadata. After publication, the exact
object VersionIds and hashes are retained. Historical catalog/DQ paths remain
valid; legacy objects are neither rewritten nor implicitly adopted.

## Verification, inputs and database authority

`VersionedArtifacts` checks VersionId, length and SHA-256 with bounded streaming,
closes response bodies on failure, enforces per-object and aggregate read bounds,
and restricts writes to a server-owned candidate namespace. The in-memory
versioned client is offline only. The verifier reads registry-authorized keys;
a caller cannot pass keys/manifests/evidence to `RegistryVerifier.verify`.

Bounds: 32 MiB/object, 128 MiB read budget per stage/request, 1 MiB manifest,
32 artifact roles, 100,000 Parquet rows, 256 Parquet columns and 128 MiB declared
uncompressed Parquet content. Raw CSV validation caps 100,000 rows/128 columns
and rejects duplicate/ragged/reserved lineage columns before pandas allocation.
The verifier independently validates Raw structure. These bounds are admission
limits, **not** a measured container memory or duration guarantee.

The trusted verifier also recomputes expected Bronze, non-Exact Silver and Gold
results with existing algorithms. It validates complete planned roles, lineage,
DQ accounting and explicit Gold skip evidence. Its verification report and
object identities are immutable. Worker-side checksum validation alone does not
grant VERIFIED status.

Verification first validates the current lease, ownership and canonical input
identities in PostgreSQL **before any storage read**, then repeats that fence
when recording evidence. Upstream object/manifest identities must exactly match
the pinned published receipt, and intent file/association scope must match its
authorized job contract. Directly inserting a registry row cannot authorize an
arbitrary source read or publication for a different file. Focused tests prove
zero storage calls for forged source/scope and expired-lease requests.

Migration `0043_processing_integration.sql` is additive and transaction-wrapped;
0041/0042 are unchanged. It adds version-bound intent/candidate evidence and
publication generations, plus source identities, job input/admission pins,
job-to-stage-attempt mappings, dependency epochs and a dispatch outbox. Existing
processing-attempt schemas and existing advisory-lock namespaces are unchanged.
The prior 0042 migration already requires PostgreSQL 15+ (`NULLS NOT DISTINCT`).
There is no new automatic migration runner or migration ledger.

SNAPSHOT admission additionally requires an existing immutable delivery
declaration. Its complete context is pinned with the load policy and checked
again at execution/publication; a queued job cannot acquire different snapshot
coverage or effective time later. Non-SNAPSHOT contracts carry an explicit null
context. This amendment is to the unapplied 0043 proposal, not an application
database migration.

SECURITY DEFINER functions have fixed `pg_catalog, <trusted schema>, pg_temp`
search paths and no PUBLIC execution privilege. The manual privilege script
separates API admission, worker publication, trusted verification and dispatch.
Runtime identities must not own/inherit authority objects, be superusers, grant
verification to the worker, or directly mutate receipts/heads/evidence. New
worker candidates can enter only STAGED through restricted INSERT columns.
Current source authorization is rechecked under row locks before publication.

Incremental publication compares the application policy to the pinned contract
with null-safe equality. A job admitted without a policy cannot authorize one
prepared later, even if a caller incorrectly invokes the application guard.
Existing manifest NOT NULL and lineage constraints remain in force.

The composable savepoint helper exposes its owned connection through the
existing repository context. Nested repository calls therefore share its
rollback; inside an outer transaction, a failed inner savepoint preserves the
outer writes. The existing prohibition on nested `repository_transaction`
contexts is unchanged.

The script is a reviewed deployment **proposal**, not an applied or certified
ACL configuration. No role was created or altered during local validation.
Its preflight rejects missing, shared or privileged identities before any grant.
Worker sequence grants are restricted to sequences owned by stage metadata
columns; the proposal does not grant all schema sequences. These checks do not
certify inherited permissions or replace actual-login deployment acceptance.
Existing application auth/API grants and all inherited memberships must be
audited with actual runtime logins. The worker remains a trusted backend role
with existing stage-metadata write access, not an untrusted SQL sandbox or RLS
boundary. Verifier credentials must never be mounted in that worker container.

## Recovery and concurrency guarantees

| Interruption | Implemented behavior |
| --- | --- |
| Before/during object creation | No stage publication; partial/complete abandoned candidates stay unreferenced. Retry uses a new lease/candidate |
| Object exists, verification/SQL fails | No authoritative head change; existing trusted head remains. Retain orphan for reviewed retention cleanup |
| Stage commit succeeds, completion/ACK is lost | Reclaim reads matching immutable receipt/bytes; no successful stage rewrite; complete job with current token |
| Duplicate delivery | Seven-outcome claim contract applies; an active lease is BUSY; committed success is ACKable |
| Lease expires during a blocking call | The call is not cancelled. No subsequent publication/completion is authorized; SQL and checkpoint checks reject stale ownership |
| Old worker finishes after reclaim | Old token cannot verify/publish/finish. An interrupted mapped attempt cannot later become SUCCESS |
| Competing head publication | Expected predecessor receipt CAS rejects stale generation; legitimate changed input advances a new immutable generation |
| Ownership/rules/dependencies change | Admission pins/current authorization prevent silently reinterpreting queued or retrying work; submit a reviewed new request after resolution |

Session locks still span storage I/O. Cooperatively renewed leases fence
metadata, not the termination of pandas, S3 or SQL calls already running.
There is **no background heartbeat**. Long uninterruptible computation can
exceed the 120-second default lease and fail publication safely but repeatedly.
Larger workloads require measured chunking/cancellation or a separately tested
heartbeat design before increasing supported limits. SIGTERM stops admission
of later chunks/stages; a supervisor needs enough grace for in-flight work.

Outbox rows provide durable scheduling intent, not an active scheduler. Dispatch
must be supervised and monitored. Busy/NOT_DUE/NOT_FOUND still retry; malformed
and unknown messages require an explicitly configured bounded redrive/DLQ
policy before deployment. No implicit redelivery mechanism is assumed.

## Remaining blockers, ranked

1. **Critical deployment gate:** execute and verify the reviewed ACL plan with
   separate least-privilege logins in a disposable deployment environment.
   Owner-credential tests prove transaction semantics, not runtime privilege
   isolation. Supply a separately authenticated verifier service boundary and
   review its deployment/availability/credentials; local tests inject it.
2. **Critical operational gate:** explicit approval, versioned S3/IAM/no-delete
   policies, queue visibility/redrive limits, supervised outbox dispatch/recovery,
   container bootstrap and monitoring are not deployed or live-validated.
3. **High:** capacity/latency testing, blocking-operation deadline policy and
   safe shutdown grace are unverified at realistic input sizes. Retry budget
   exhaustion during contention needs an operator policy.
4. **High:** Exact APPEND V2/Exact Gold V2 remain restricted. The integrated
   executor rejects Exact rule contracts. No Phase 11G gate is enabled.
   New-path APPEND, UPSERT and SNAPSHOT have isolated PostgreSQL/in-memory S3
   acceptance coverage. The keyed cases verify idempotency, queued upstream-input
   staleness, actual lock overlap, failed application recovery and committed
   row/lineage integrity. This does not establish a complete new-path business-time
   late-arrival matrix or real-cloud concurrency acceptance.
5. **High:** historical source/stage adoption is explicit future work. A
   deduplicated physical file without a version registry cannot be admitted
   safely. Do not infer its historical S3 version from the latest object.
6. **Medium:** abandoned Raw/candidate/incremental artifacts accumulate safely.
   No automatic deletion is implemented. Cleanup must prove non-reference,
   retention age and lack of active execution, preserving all historical receipts.
7. **Medium:** warehouse provider/source-vector semantics are preserved, but no
   live warehouse acceptance, complete repository regression or real AWS crash
   test was performed. Local fakes do not prove cloud end-to-end reliability.
8. **Medium:** changing a Gold plan to remove historical artifacts can be
   rejected by the existing catalog-completeness check. The prior head/catalog
   transaction is preserved; removing obsolete catalog roles/columns needs a
   reviewed reconciliation policy. Same-role generations update the Gold run's
   base pointer and catalog paths atomically with the new receipt.

## Ordered deployment checklist — manual only

1. Review this diff, migration and security design; approve a deployment window.
   Keep execution/dispatch/admission disabled. Back up metadata and record the
   exact existing migration checkpoint, role memberships and historical paths.
2. In a disposable environment, apply sorted prerequisite migrations through
   0043 once, using the established trusted schema. Verify PostgreSQL version,
   constraints/triggers and protected function search paths. Do not rerun this
   once-only migration against an already-migrated schema.
3. Have the DBA supply existing dedicated roles: NOLOGIN authority owner,
   non-owner/non-superuser API, worker, verifier and dispatch identities. Review
   then apply `deployment/task20_runtime_privileges.sql` with psql identifier
   variables `schema`, `authority_owner`, `api_role`, `worker_role`,
   `verifier_role`, `dispatch_role`. The script creates no roles. Audit prior
   inherited/table/column/function grants; a REVOKE from one role does not erase
   permissions inherited from another role.
4. Under each actual login, prove denied direct receipt/head/verification writes,
   admitted source scope, allowed function calls, savepoint rollback, lease expiry
   after lock waits and verifier/worker separation. Review authority-owner grants
   needed by existing algorithms. Do not place secrets in test reports or source.
5. Review private versioned bucket policies: conditional unique writes, exact
   version reads, restricted prefixes, no worker deletion/version deletion and
   independent verifier read authority. Existing live objects remain untouched.
6. Configure SQS/DLQ/redrive/retention/visibility, bounded SDK connection/read
   timeouts, supervised outbox polling and alerting. Exercise uncertain sends,
   lease-expiry recovery, poison messages, lost completion and shutdown in that
   disposable deployment. Wire the lightweight authenticated Lambda admission
   and dispatch wrappers; do not attach the processing executor to Lambda.
7. Build the EC2 container bootstrap with injected transport/storage and a
   separately authenticated verifier client. Validate resource bounds and SIGTERM
   behavior. Keep `IntegratedExecutor(enabled=False)` and dispatch disabled until
   these gates pass and explicit deployment approval is recorded.
8. Deploy readers/legacy-writer guards before durable sources are admitted.
   Only after acceptance, set `DATARISE_PROCESSING_READERS=true` and
   `DATARISE_PROCESSING_DEPLOYMENT_APPROVED=true`, then explicitly enable the
   reviewed API/dispatch/worker bootstrap. Start with one message and one bounded
   non-Exact stage. Bronze ends at rule review; Silver/Gold are separate jobs.
9. Monitor job/outbox age, attempts, lease loss, verification failures, head
   generations, stage/catalog consistency and orphan volume before expansion.

## Rollback checklist — preserve evidence

1. Disable new asynchronous admission and dispatch; stop receiving new work.
   Request cooperative worker shutdown and wait for/reconcile active leases.
2. Keep immutable-aware readers and managed-source legacy-writer guards active.
   Turning off the readers guard and falling back to fixed-key writers is unsafe
   for sources already managed by this path. Stop those writes instead.
3. Preserve the additive tables, receipts, source versions, catalog references,
   outbox rows and all artifacts. No destructive down migration or S3 cleanup
   is supplied. Inspect uncertain sends/completions before replay.
4. Revoke only newly introduced runtime execution grants after DBA review;
   retain read access needed for historical references. Restore application
   behavior only for unmanaged legacy sources, then repair forward under review.

## Validation and exact file inventory

Final focused results (2026-11-01):

| Run | Passed | Failed/errors/skipped | Duration |
| --- | ---: | --- | ---: |
| Existing worker/job/publication foundation | 200 | 0 / 0 / 0 | 416.38 s |
| Existing upload, rule, stage, incremental and warehouse compatibility | 45 | 0 / 0 / 0 | 2068.34 s |
| Verifier preauthorization and full Bronze/Silver/Gold flow | 3 | 0 / 0 / 0 | 374.32 s |
| Final forged-source/scope/expiry probes and incremental flow | 4 | 0 / 0 / 0 | 377.72 s |
| Offline storage/adapters, execution plan and rule gates | 52 | 0 / 0 / 0 | 1.19 s |
| Final expanded admission/adapter cases | 19 | 0 / 0 / 0 | 0.36 s |
| Durable upload/source-pin rollback | 1 | 0 / 0 / 0 | 10.71 s |
| Outbox renewal/retry scheduling | 1 | 0 / 0 / 0 | 13.97 s |
| Final runtime guard and transaction checks | 1 | 0 / 0 / 0 | 12.19 s |
| Final disabled-default runner check | 1 | 0 / 0 / 0 | 2.07 s |

Runs overlap; do not sum these rows as distinct tests. Durations include fixture
setup and local connection latency, not just stage computation. Earlier failed
runs are retained in the JSON report: fixture bucket mismatch, nested transaction
composition, CIDR loopback parsing, an intermediate admission-fixture mismatch,
the immutable Silver lineage bridge, and oversized Windows pytest parameter IDs.
The original 20-case integration run had 19 passes/one failure; its APPEND failure
was corrected and passed alone, then passed again with final scope fencing.
There are no unresolved failures in the final targeted runs.

Compatibility reported two existing deprecations: Starlette TestClient and
Authlib's HTTPX integration. Dependencies were not changed. Actual least-privilege
login acceptance is **blocked by the explicit no-role/no-permission-change
restriction**, not counted as a passed or skipped test. Cloud acceptance and
realistic capacity testing remain unperformed release gates.

See `task20-validation.json` for commands, recorded outcomes, evidence limitations
and exact task-owned added/modified files. Runs use offline clients and UUID
temporary PostgreSQL schemas; no application `public` migration, AWS call,
warehouse mutation or role change occurred. No files were staged, committed or
pushed. The pre-existing
`docs/phase8a-safe-fixture.html` edit and unrelated untracked work are preserved.

Reproducible runner (uses installed dependencies; no sync/install):

```powershell
uv run --no-sync python tools/task20_local_tests.py --offline tests/test_task20_adapters.py tests/test_task20_storage.py tests/test_lambda_processing_plan.py tests/test_processing_rule_gate.py -q --tb=short -ra -p no:cacheprovider
uv run --no-sync python tools/task20_local_tests.py tests/test_task20_security_postgres.py tests/test_task20_processing_postgres.py -q --tb=short -ra -p no:cacheprovider
```

The runner disables deployment/reader gates in its child environment, removes
Exact test overrides, prohibits real SDK/network use, and requires local PG
configuration for DB mode. Only selected temporary-schema fixtures apply DDL.
