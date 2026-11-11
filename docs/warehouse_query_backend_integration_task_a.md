# Warehouse SQL backend integration ? Task A

Implementation record for `feature/warehouse-model-ux`. This is not approval of
the draft runtime-role matrix or live provider activation.

## Implemented HTTP workflow

All routes are under `/workspaces/{workspace_id}/warehouse/queries`:

| Method and suffix | Behavior |
| --- | --- |
| POST `/validate` | Parse the approved owned model's logical SQL; verify pinned serving metadata, estimate and current budget. No admission or send. |
| POST (base) | Atomically persist the server attempt, logical draft, budget reservation, publication pins/link and original-session idempotency binding. Returns an opaque attempt handle. |
| GET `/{handle}` | Original-session status; no provider IO. |
| POST `/{handle}/execute` | Revalidate stored intent and budget, compose D5/E1/E3, commit the send marker, then use the explicitly injected test transport once. Verify metadata and settle before success. |
| POST `/{handle}/refresh` | Lookup/re-attest the original job or retry exact durable accounting. Never resubmit. |
| POST `/{handle}/cancel` | Record the existing cancellation intent/boundary, attempt cancellation once, then verify terminal metadata and billing. A cancel acknowledgement is not proof. |
| GET `/{handle}/results` | Only a settled verified success; derive approved result schema server-side and validate one bounded first page, rechecking authorization after IO. |

The existing catalog and result-readiness routes are unchanged. Mutation routes
use the existing session/CSRF middleware. Request bodies cannot supply owner,
physical SQL, provider identity, publication pins, policy, schema or lease tokens.
Validation errors and unexpected failures do not echo SQL, provider payloads or
credentials. Public results expose no physical table, job ID or recovery token.

`get_query_runtime()` always returns HTTP 503. There is no default SDK client,
credential discovery, environment enable switch or deployable provider factory.
Tests override only that dependency with offline fakes; authentication, PostgreSQL,
compiler, metadata checks, journal, attestation, accounting and result validation
remain real. Existing public-launch restrictions remain unchanged. Production
analytical execution is disabled and remains **NO-GO**.

## Reused guarantees and resolved gaps

Reused the existing owned catalog, strict single-model planner/compiler, serving
metadata verifier, governance budgets, immutable attempt/draft/pins/reservation
links, D5 original-session checkpoint, E1 recovery lease/journal, E3 provider
policy/request envelope, F1/F2 send-once/lookup, F3 attested accounting, F4
cancellation and F5 schema/first-page validation. No parallel query state machine
or new execution permit was introduced.

Migration 0061 adds only an immutable API retry-key-to-attempt binding. It stores
hashes of the client key and exact model/trimmed logical-SQL request and the
original session digest. Concurrent same-key admission returns one committed
attempt; altered input or another session cannot replace it. The existing stored
logical draft registration now has a private transaction-composable helper;
its public contract and validation are unchanged.

Admission uses the existing global/workspace governance locks. Provider preflight
IO occurs outside SQL transactions; model/source/publication authority is resolved
again before committing. A failed admission rolls back attempt, draft and budget
as one transaction. Stale or unavailable publication after preflight returns 409.

A send marker commits before the injected submission. After that marker, even a
crash before the provider receives anything is lookup-only and remains held if
NOT_FOUND. Active/stale recovery ownership is fenced by the existing revision,
token and PostgreSQL-clock checks. Provider metadata is independently revalidated;
failed, cancelled or successful terminal observations require complete billing
before existing idempotent accounting. Successful accounting followed by duplicate
execution reuses the settled result. Accounting failure after observation recovers
from that durable evidence. Terminal status with incomplete billing uses the
existing terminal evidence gate plus E3 GET-only re-attestation; it does not weaken
F2's initial-send phase restrictions.

Status vocabulary: ADMITTED, EXPIRED (unclaimed reservation), PREPARED, RUNNING,
SUCCEEDED, FAILED, CANCELLED and UNCERTAIN. A provider `deadlineExceeded` is an
attested FAILED outcome under the existing evidence vocabulary; no unsupported
TIMED_OUT proof is invented. Transport timeout, missing job, mismatched metadata
and incomplete billing remain uncertain/held. Pre-send cancellation is rejected;
there is no fabricated cancellation success or new reservation-release command.

Revocation denies customer access and new work. It cannot undo in-flight IO.
Trusted existing recovery can still reconcile original evidence separately. Every
publication dependency/indefinite hold remains; neither settlement nor cancellation
releases retention or authorizes physical deletion.

## Scope and deployment limitations

The integrated contract is the existing bounded **single-model logical projection**
with explicit LIMIT and current development/Mumbai policy. It does not promote
join/SCD2 planners, unrestricted SQL, production/Sydney policy, arbitrary cursors,
full exports or new model/publication behavior. FACT-backed HTTP lifecycle is
verified here; other model kinds are not a new end-to-end acceptance claim.

0061 follows 0060, is once-only like the preceding SQL migrations, and references
the existing auth/attempt and no-truncate objects. Its invoker guard has a pinned
trusted search_path and denies mutation; PUBLIC has no new table/function access.
No runtime grant/role provisioning is included. Apply only through reviewed schema
migration procedures after role-matrix approval. Roll back application integration
by keeping the runtime unavailable; do not drop immutable retry/audit records or
release holds as a rollback shortcut. This phase applies it only in disposable
UUID test schemas, never the application's public schema.

Remaining release blockers: approved role matrix and isolated connection routing;
actual restricted-login/trigger/sequence privilege acceptance; separately reviewed
real provider factories, IAM, dry-run attestation, network timeout/lease budgets,
controller scheduling and post-revocation recovery; operational handling of
permanently absent jobs; and independently justified retention release. Fake
provider results do not establish cloud readiness. No frontend work, cloud call,
application migration, role change, deployment or activation occurs here.

## Verification

Final API integration: **28 passed, 0 failed/errors/skipped**, 141.84 s.
Prior 24-module regression: **317 passed, 0 failed, 2 setup errors, 0 skipped**,
6121.67 s. Both errors were PostgreSQL connection startup timeouts in fixture
setup before accounting test bodies ran. The two exact cases were rerun unchanged:
**2 passed, 0 failed/errors/skipped**, 10.33 s. No timeout or assertion changed.
Their precise environmental latency cause was not established; the long broad-run
elapsed time is not performance evidence. The broad run is retained as non-clean.
Latest per-case coverage: **347 distinct passes (28 new + 319 prior)**, no remaining
failed/errored/skipped selected cases. This is not a claim of one clean 347-case run.
The new API module was rerun after the final provider-byte-cap change; prior
regressions exercise unchanged earlier service paths. No source/test change
followed the final API acceptance run.
Exact executed commands, JUnit hashes, per-module counts, diagnostic history and
source hashes are recorded in `warehouse_query_backend_integration_task_a_validation.json`.

The final provider cap regression rejects an estimate above the trusted server
provider byte ceiling before admission; governance limits cannot widen that cap.

New cases cover approved catalog-to-results HTTP flow, duplicate keys/execution,
terminal errors/cancellation, lost insert responses, missing jobs, forged metadata,
missing and later recovered billing, busy recovery, accounting persistence failure,
SQL rejection, budgets, disabled runtime, original session/CSRF/foreign scope,
revocation during IO, result schema corruption, real send overlap, stale heads,
write-ahead crash, expired lease and transactional admission rollback. Concurrent
execution confirms the committed SEND_UNKNOWN marker and live database lease while
the fake transport is blocked; thread startup alone is not considered proof.

Initial diagnostics are retained: one collection syntax error; focused 19 passed /
1 failed (terminal incomplete-billing lookup routing); expanded 26 passed / 1
failed (stale-publication HTTP classification). Both causes were corrected without
weakening assertions. Existing Starlette/httpx and Authlib/httpx deprecations remain.

Reproduce either recorded selection in PowerShell with existing dependencies:

```powershell
Set-Location 'D:\Project\datarise-warehouse-ux'
$env:RUN_DB_TESTS='1'
$env:APP_ENV='development'
$env:AUTH_MODE='session'
$env:AUTH_SECRET='synthetic-dispatch-session-test-secret-414d5'
$python='D:\Project\ai-data-workspace\.venv\Scripts\python.exe'
$bootstrap="import os,runpy; from dotenv import dotenv_values; os.environ.setdefault('DATABASE_URL',dotenv_values(r'D:\Project\ai-data-workspace\.env')['DATABASE_URL']); runpy.run_path('tools/task20_local_tests.py',run_name='__main__')"
# Choose api-final or prior-regression; the evidence JSON contains the exact modules.
$run=(Get-Content docs/warehouse_query_backend_integration_task_a_validation.json -Raw | ConvertFrom-Json).runs | Where-Object name -eq 'api-final'
$tests=@($run.modules.PSObject.Properties.Name | ForEach-Object { $_.Replace('.','/')+'.py' })
& $python -c $bootstrap @tests -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/warehouse-workflow-reproduce.xml"
```

The runner requires loopback before PostgreSQL connection, blocks external
sockets/SDKs and disables processing/Exact test overrides. No developer login is
presented as restricted-role acceptance. No full-repository/frontend/live-cloud
regression is claimed. Stop after this backend checkpoint for review.
