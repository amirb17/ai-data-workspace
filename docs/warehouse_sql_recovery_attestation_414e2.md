# Step 414E2 - lookup-only provider recovery attestation

## Security design before implementation

Reviewed committed 414E1 (776e86a), 0053/0054, D5/E1 security reports, the
provider-neutral QueryRecovery/RecoveryState contracts and BigQueryReadProvider.
The existing recovery verifier checks identity/labels but not the complete
configuration and calls result() on success. E2 must not reuse that row-reading
path or manufacture an ExecutionPermit.

Trusted boundary: backend-only lookup_and_attest accepts only an E1 lease and
expected revision. No provider/client, SQL, response, principal, permit or proof
flag is accepted. An empty-by-default server runtime registry supplies an exact
project/location lookup-only transport and reviewed creator/timeout policy.
No SDK client, credentials, endpoint or execution gate is created automatically.
Fake transports are installed only by tests via module-level server wiring.

1. Snapshot immutable checkpoint/intent/reservation scope and validate E1 CAS and
   owner under the existing lock order; close the transaction.
2. GET exactly the registered project/location/job ID, with bounded timeout and
   SDK retries disabled. No query/execute/result/getQueryResults/cancel/list call.
3. Independently validate detached, size-bounded REST metadata: full identity,
   approved provider creator, application labels, exact SQL SHA-256, query config,
   referenced physical tables/SELECT statistics, original budget ceiling and
   terminal state/available billing. Labels are corroboration, never authentication.
4. Revalidate the same revision/token/generation/DB-clock lease and original
   reservation, then atomically append the E1 observation and immutable attestation.
   A stale owner, settlement or contradictory terminal event rolls everything back.

### Evidence limitations / fail-closed policy

The official [Job resource](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/Job)
exposes creator, configuration, references and status. Some fields are optional;
principal_subject has limited availability. Labels are caller-supplied metadata, not proof.
Job timeout is a stop attempt, not a hard cancellation guarantee. Missing fields,
unknown configuration and unprovable destination behavior are uncertain. We will
not infer that a returned destination is an automatic anonymous table from its name.
Failure/cancellation may omit billing; any attestation must explicitly distinguish
identity/state checks from complete billing. No result rows are fetched.

[jobs.get](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/get)
requires the explicit regional location here and has finite history. A missing
job or denied/transient lookup cannot prove non-execution. Only a genuine NOT_FOUND
response becomes NOT_FOUND_HELD; all other errors remain UNCERTAIN. No resubmission.

D5/E1 do not persist the original cloud submitter/runtime-timeout policy. Therefore
an actual historical recovery principal/policy must be separately reviewed and
registered by trusted startup; absent policy fails closed. Check configured byte
caps within the immutable reservation/checkpoint budget, not a caller's new budget.
The current development/Mumbai binding remains unchanged. No production/Sydney
scope expansion is part of E2.

### Threats and races

- Wrong-scope IDs, creator, labels, SQL or configuration: rejected attestation;
  append only UNCERTAIN, never a terminal success/failure.
- Forged terminal report or incomplete metadata: no independent verification.
  Match against immutable expected values, preserve evidence gaps and holds.
- Timeout/response loss: no permission to resend; bounded lookup retry only.
- Concurrent workers, expiry/reclaim or stale revision: final token/revision CAS
  rejects stale evidence, even when the fetched payload was otherwise valid.
- Revocation during lookup: allow historical metadata verification/bookkeeping,
  retain revocation overlay and all accounting/retention holds; no result access.
- Contradictory terminal observations: journal monotonicity rejects and alerts;
  do not rewrite prior history or silently convert to success.
- SQL/admin or trusted factory compromise is outside DTO validation; schema-owner
  tests cannot prove restricted-role or authenticated live transport isolation.

No DB lock spans provider IO. No automatic settlement/hold release/public route,
AWS work, new grants or live analytical submission is permitted. LIVE remains NO-GO.

## Implemented checkpoint

The public backend entry point is `lookup_and_attest(lease, expected_revision)`.
Its immutable result contains only safe classification, revision, billing-evidence
completeness and the existing internal recovery view. It is not a public response
DTO: that view includes the expected internal job scope. No token, query text,
creator email, result rows or provider error text is persisted in attestations.
The payload SHA-256 and reviewed policy SHA-256 permit correlation; they do not
make raw metadata recoverable or constitute cryptographic proof of provider origin.
Trusted client construction, authenticated transport and IAM remain essential.

| Observation | Evidence/classification | Effect |
| --- | --- | --- |
| NOT_FOUND | `jobs.get` itself returned NotFound; verdict ABSENT | Preserve phase/accounting/holds; never permission to resend. |
| UNCERTAIN | Transport failure, missing/ambiguous metadata or rejected mismatch | Safe bounded reason, no raw exception; preserve phase and holds. |
| IN_PROGRESS | Matching PENDING/RUNNING metadata with sufficient identity/configuration/statistics | Journal PROVIDER_RUNNING, no execution permission. |
| SUCCEEDED | Matching DONE, no terminal error, required timestamps and billing present | SUCCEEDED_OBSERVED only; no rows or settlement. |
| FAILED | Matching DONE with provider errorResult | FAILED_OBSERVED; billing may remain incomplete. |
| CANCELLED | Matching DONE with errorResult.reason=stopped | CANCELLED_OBSERVED; no cancel RPC or settlement. |

Cancellation uses the documented [BigQuery error reason stopped](https://docs.cloud.google.com/bigquery/docs/error-messages).
A cancellation request, timeout or state spelling CANCELLED is not terminal proof.
[Cancellation documentation](https://docs.cloud.google.com/bigquery/docs/managing-jobs)
also warns that stopping a query may not succeed and costs can persist.
A transient lookup after terminal evidence preserves the E1 terminal phase;
contradictory terminal/running evidence is rejected by the existing journal guard.

`VERIFIED_METADATA` means the explicit identity/configuration/state checks passed,
not a settlement receipt. `billing_complete` means both nonnegative provider byte
counters were present, not final invoice reconciliation. Even matched failed or
cancelled metadata never releases a reservation or physical-table dependency.
Existing provider-neutral `QueryRecovery` is unchanged: its SUCCEEDED contract
requires query results, so E2 deliberately uses a separate metadata-only type.
E1 supplied diagnostic observations remain distinguishable from E2 attestations.

0055 adds only `warehouse_query_recovery_attestations`, a guarded append-only table
keyed by the exact E1 observation revision. It forbids update/delete/truncate,
requires a matching journal observation and supplies a database-clock timestamp.
It stores enum classifications, policy/payload digests and optional byte counters.
The E1 event and E2 row commit/roll back together; no attestation is written before
final lease/CAS checks. Existing E1 public signatures and semantics are preserved
by extracting its transaction body into a private composable helper.

Lock order remains E1: global/workspace governance advisory locks, sorted resource
retirement locks, attempt/reservation rows, intent row, journal append. The snapshot
and final append use separate bounded transactions with the original 5-second lock
and 10-second statement limits. Neither transaction spans provider IO. The final
transaction rechecks the reservation is EXECUTING and the same expected context,
revision, lease token/generation and database-clock expiry. Recovery remains allowed
after account/publication revocation solely for historical bookkeeping; revocation
and checkpoint expiry overlays remain authoritative. No stale result authorizes work.

Metadata parsing is conservative. It requires a SELECT referencing the exact pinned
physical table, reviewed creator, original application labels and exact stored SQL
fingerprint; it rejects unreviewed configuration, routines, scripts, cache/legacy
behavior, destinations and excessive byte/timeout settings. Missing statistics on
PENDING or failed jobs can therefore remain UNCERTAIN. A provider-populated automatic
destination is not provably distinguishable from an explicit destination using its
name alone; this checkpoint does not guess. Live API/default-field compatibility
requires approval and additional evidence, never loosening the validator blindly.

Transport disables SDK retries and uses a 10-second timeout. Detached JSON is capped
at 256 KiB before validation/persistence. This is not a hard process-memory or total
wall-clock bound: the SDK/JSON encoder may allocate the response first and credential
refresh/transport internals require separate runtime acceptance. No scheduler,
background thread or credentials/client factory is activated.

## Minimum privileges to review (not applied)

Use a dedicated trusted observer with `bigquery.jobs.get` on the exact job project;
review a custom least-privilege role rather than broad project/job-admin roles.
The same-creator jobs.get permission exception depends on jobs.create; E2 recommends
an independently scoped observer with explicit jobs.get, not granting create just
to recover. No jobs.create/query submission, jobs.list/listAll, jobs.update/cancel,
table data-read or result-fetch permission is required by this implementation.
Permission details and same-creator exception: [jobs.get](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/get).
Metadata includes SQL and internal scope, so this authority must never be exposed
to customer-supplied SDK clients or a public status endpoint. Identity/credentials,
project restrictions, endpoint/TLS and effective IAM require deployment review.

PostgreSQL needs the existing trusted controller's SELECT rights over checkpoint,
intent, reservation, journal, pin and revocation context; E1 journal INSERT; and
SELECT/INSERT on the new attestation table. Existing FOR UPDATE paths require
reviewed UPDATE privilege sufficient for row locking on their tables/columns,
even though immutable triggers reject actual mutations. SECURITY INVOKER guards
need the referenced journal SELECT. Trigger installation is a migration-owner
action; review function dependencies rather than granting PUBLIC execution. PUBLIC access is
revoked and search_path is pinned by 0055. No role, grant, SECURITY DEFINER bypass
or privilege deployment is added. Schema-owner tests do not establish least privilege.

## Remaining release blockers

- **P0:** No trusted runtime registry/client/creator policy is wired in production;
  default lookup fails closed. D5/E1 did not persist original cloud submitter or
  timeout policy. Historical policy provenance and exact real metadata/configuration
  compatibility must be reviewed before lookup activation. Development/Mumbai only.
- **P0:** No reviewed send, cancellation, settlement or result-delivery bridge exists.
  Matching metadata and billing fields alone cannot settle accounting or bypass
  authorization. No hold-release protocol is introduced; all 0053 holds remain.
- **P0:** Effective provider IAM, independent authenticated transport, restricted
  database-role privileges and cross-tenant trusted-controller isolation remain
  untested. Supplied fake REST metadata is not evidence of live provider authenticity.
- **P1:** Durable lookup scheduling/backoff/escalation, finite provider job history,
  missing jobs, network partitions, shutdown and deployed resource limits need an
  operational design. No automatic retries, ACK policy or resubmission is added.
- **P1:** Existing unverified terminal reports can conflict with later independently
  checked metadata. E2 fails closed and retains history; operator reconciliation
  must be reviewed separately rather than overwriting E1 observations.

LIVE analytical BigQuery execution remains NO-GO. No 414F/414G, endpoints, grants,
retention release, cloud access, infrastructure or UI changes are part of this step.

## Exact PowerShell verification

Run from the feature worktree using the existing populated interpreter. Only the
already-authorized local DATABASE_URL is copied process-locally; never print it.
The existing runner validates loopback before connecting, blocks cloud/network SDK
access, enables RUN_DB_TESTS=1, disables processing/Exact overrides, and fixtures
apply 0055 only to UUID temporary schemas. No application/public-schema DDL occurs.
The auth key below is synthetic test data, not a deployment credential.

```powershell
Set-Location 'D:\Project\datarise-warehouse-ux'
$env:RUN_DB_TESTS='1'
$env:APP_ENV='development'
$env:AUTH_MODE='session'
$env:AUTH_SECRET='synthetic-dispatch-session-test-secret-414d5'
$python='D:\Project\ai-data-workspace\.venv\Scripts\python.exe'
$bootstrap="import os,runpy; from dotenv import dotenv_values; os.environ.setdefault('DATABASE_URL',dotenv_values(r'D:\Project\ai-data-workspace\.env')['DATABASE_URL']); runpy.run_path('tools/task20_local_tests.py',run_name='__main__')"
$tests=@(& $python -c "import json,pathlib; d=json.loads(pathlib.Path('docs/warehouse_sql_send_recovery_414e1_validation.json').read_text()); print('\n'.join(k.replace('.','/')+'.py' for k in d['validation']['runs'][-1]['modules']))")
$tests+='tests/test_warehouse_sql_recovery_attestation_414e2.py'
& $python -c $bootstrap @tests -q --tb=short -ra -p no:cacheprovider --evidence-dir "$env:TEMP/414e2-final-events" --junitxml="$env:TEMP/414e2-final.xml"
```

Focused command: same environment, interpreter/bootstrap with only
`tests/test_warehouse_sql_recovery_attestation_414e2.py`, `-q --tb=short -ra -p no:cacheprovider`
and `--junitxml="$env:TEMP/414e2-focused.xml"`. Final evidence/results follow below.

## Exact change inventory

- Modified `app/services/warehouse_sql_send_recovery_service.py`: extract private
  transaction-composable observation body; preserve E1 public contract.
- Added `app/warehouse/query_recovery_attestation.py`: lookup-only metadata transport,
  reviewed immutable policy and independent conservative evidence validator.
- Added `app/services/warehouse_sql_recovery_attestation_service.py`: backend-only
  snapshot/lookup/fenced atomic E1 observation plus E2 attestation.
- Added `migrations/0055_warehouse_query_recovery_attestations.sql`: additive immutable
  evidence table and guards; no changes to 0053/0054 or permission grants.
- Added `tests/test_warehouse_sql_recovery_attestation_414e2.py`: fake-provider metadata
  plus real isolated PostgreSQL/fault/concurrency acceptance.
- Added this document and `warehouse_sql_recovery_attestation_414e2_validation.json`:
  design, exact commands, inventory, counts, hashes and limitations.

## SDK compatibility finding

Offline source inspection of the installed `google-cloud-bigquery 3.46.2` confirmed
that `Client.get_job` performs GET with full projection, while
`QueryJob.to_api_repr()` returns only jobReference/configuration for submission.
Using it would discard independent creator/status/statistics evidence and make
legitimate recovery uncertain. E2 now detaches the full response retained in the
SDK job's private `_properties`. A test hydrates an actual `QueryJob.from_api_repr`
from synthetic metadata and proves this boundary; no SDK client/credentials/network
are constructed. The fake job now deliberately rejects submission-representation
access rather than masking this distinction.

SDK hydration converts creation/start/end timestamps to floats. Only these three
fields are normalized, only for integral nonnegative values below 2^53; fractional
or unsafe values fail closed. Accounting/configuration floats remain invalid.
This private SDK dependency needs explicit compatibility tests on any dependency
upgrade; it is not a provider-independent public API guarantee. No dependency was
installed or updated. The previously started combined selection predates this
transport correction and is distinguished from final affected-module acceptance.

Final affected-module command (using the same variables/environment above):

```powershell
& $python -c $bootstrap tests/test_warehouse_sql_recovery_attestation_414e2.py -q --tb=short -ra -p no:cacheprovider --evidence-dir "$env:TEMP/414e2-acceptance-events" --junitxml="$env:TEMP/414e2-acceptance.xml"
```

The completed combined run collected 234 cases before the SDK correction and
passed all 234 (187 prior + 47 E2), zero failures/errors/skips, in 636.58 seconds.
Its E2 results are superseded by final affected-module acceptance. The prior 187
cases exercise unchanged sources and the unchanged extracted E1 transaction body;
none uses the new E2 transport. This is not represented as a complete final-source
combined run. Retained XML: `$env:TEMP/414e2-final.xml` and incremental records under
`$env:TEMP/414e2-final-events`.

Transaction composition remains fail-closed: the existing `repository_transaction`
rejects nesting rather than borrowing a caller-held transaction, so this API cannot
silently keep an outer transaction open across lookup. No shared database helper
was changed. Physical-table immutability/ACLs and compliant deletion remain inherited
P0 deployment obligations: an indefinite PostgreSQL hold cannot prevent out-of-band
cloud deletion. This checkpoint grants no physical protection it cannot enforce.

## Final local acceptance - 2026-11-11 (Asia/Calcutta)

**414E2 local checkpoint PASS; LIVE analytical execution remains NO-GO.**

| Selection | Passed / failed / errors / skipped | Console duration | Interpretation |
| --- | --- | --- | --- |
| Intermediate focused E2 | 45 / 0 / 0 / 0 | 153.00 s | Before final additions/SDK correction; superseded for E2. |
| Combined prior + pre-correction E2 | 234 / 0 / 0 / 0 | 636.58 s | All 187 prior cases pass; 47 older E2 cases superseded below. |
| Final complete E2 module | 50 / 0 / 0 / 0 | 175.54 s | Corrected final source, including actual SDK hydration and unsafe timestamp cases. |

Latest-per-case evidence is **237 distinct passing cases (187 prior + 50 E2),
zero failures/errors/skips**. This is a union of successful selections, not a
claim that a single final-worktree 237-case combined run occurred. The SDK fix
changed only E2 transport/tests, which the prior 187 paths do not use. The complete
final affected module passed after that correction; no source/test change followed.
All prior module counts match committed E1 evidence. No full-repository, live
provider, real IAM/credential/TLS, restricted-role or deployed acceptance is claimed.

Final E2 XML: `$env:TEMP/414e2-acceptance.xml`; incremental events and exact collected
case IDs: `$env:TEMP/414e2-acceptance-events`. The adjacent validation JSON records
source/evidence hashes, module counts, precise commands and run distinctions.
The initial resumed launch was blocked before pytest by interpreter sandbox access;
the same authorized local command ran with sandbox escalation. Reading prior JSON
with PowerShell 5 failed during command preparation, so Python stdlib JSON supplies
the unchanged module inventory. Neither setup issue is a test failure or skip.
The earlier unavailable process/session was not counted as passing evidence.

Real PostgreSQL plus fake provider cases exercise exact identity/configuration,
creator/optional subject, unknown/missing/oversized evidence, API errors and lost
responses, duplicate concurrent lookup CAS, expiry/reclaim during IO, account/head
revocation during IO, cancellation and later NOT_FOUND, contradictory terminality,
immutable evidence, once-only migration and journal/attestation rollback. Explicit
rendezvous events establish inflight overlap, and a separate connection acquires
the governance advisory lock and intent row during blocked lookup. A valid returned
payload cannot bypass final revision/token/generation/DB-time checks. No test fetches
query results or submits/cancels a job; actual SDK hydration is entirely synthetic.

Migration 0055 ran only in disposable UUID schemas after 0053/0054. No application
migration, role/grant, dependency, cloud credential/API, infrastructure, UI, worker
activation or execution gate change occurred. Branch/HEAD and index are unchanged;
only the seven listed files belong to this phase. Review the checkpoint and minimum
privileges before any further work. STOP here: no 414F/414G or deployment approval.
