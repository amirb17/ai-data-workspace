# Step 414E1 - durable NON-DISPATCH send intent and recovery

## Design and threat model (before implementation)

Reviewed committed 414D5 at affd6fb, migration 0053, reservation transitions,
revocation/indefinite holds, deterministic BigQuery job identity and existing
lookup-only recovery/settlement interfaces. The worktree started clean on
feature/warehouse-model-ux. The existing provider uses project/location/job ID;
its RecoveryState has NOT_FOUND/IN_PROGRESS/SUCCEEDED/FAILED and maps cancellation
errors to FAILED. No live provider is instantiated in this step.

| Failure/threat | Required outcome |
| --- | --- |
| Crash before intent commit | No new record; original EXECUTING claim stays held. |
| Crash after intent commit, before send boundary | Same immutable identity; recovery/review only, never implicit send. |
| Crash during send or response loss | Write-ahead SEND_UNKNOWN persists; lookup exact original job, never mint a token or resubmit. |
| Duplicate workers or expired owner | Journal revision + opaque lease token + DB-clock expiry fence writes; reclaim rotates token without resetting phase. |
| Revocation during possible send | 0053 tombstone remains authoritative; block new boundary, allow lookup-only bookkeeping, retain all holds. No promise of instantaneous cloud cancellation. |
| Expired checkpoint/reservation lease | No new send boundary; trusted recovery bookkeeping can continue for the same EXECUTING identity. |
| Missing/transient provider lookup | Record NOT_FOUND/UNCERTAIN without regressing phase or releasing accounting. |
| Cancel requested vs cancelled | A request is not terminal proof. CANCELLED_OBSERVED is only an unsettled diagnostic; no automatic settlement/release. |
| Forged observation, job scope or terminal report | Reject wrong project/location/job; matching reports still do not constitute independently verified provider proof or authorization. |

Immutable send intent is tied to an existing 0053 checkpoint and exact executing
reservation. Session-authenticated preparation revalidates current stored draft,
compiled intent, publication/receipt and current budgets. It does not create a
checkpoint, reserve again, retroactively bless a legacy claim or accept caller SQL.

The append-only journal is the sole recovery-state/ownership authority. A row lock
on the immutable intent serializes append, while expected revision provides CAS.
State moves from INTENT_RECORDED to SEND_UNKNOWN, optionally PROVIDER_RUNNING,
then a terminal *_OBSERVED state. Unknown/missing lookup never goes backwards.
Lease acquisition is another event: same phase, fresh token/generation, bounded
DB-clock expiry. No background thread, renewal daemon or dispatcher is introduced.

A send-boundary operation records ambiguity only, after revalidation under the
same context locks. It returns no permission to execute and calls no provider.
Trusted backend workers can claim/record recovery after user revocation; these
functions are not user-facing authorization/status endpoints. Tokens are internal
metadata-writing capabilities only, excluded from repr/public preparation results.
No caller-controlled provider object, proof boolean, SQL or execution permit.

Lock order: existing global/workspace governance advisory locks; user/session/
workspace rows where checking live preparation; sorted resource retirement locks;
attempt/reservation rows; intent row; journal append. No DB transaction spans IO.
New operations use local 5-second lock/10-second statement bounds. Deadlock or
uncertain DB commit fails closed; inspect durable state, never generate a new job.

0053 holds remain unchanged and indefinite across every state. Journal terminal
observations do not settle governance accounting, release retention or authorize
result delivery. Independent provider identity/configuration/evidence, actual send
and cancellation/settlement integration require 414E2/414F/414G review. No grants,
roles, public endpoint, credentials or cloud calls are added. LIVE remains NO-GO.


## Implemented contract

0054 adds only two tables and their guards; 0053, existing governance accounting,
publication and revocation triggers are unchanged. Immutable intents copy the
checkpoint's exact reservation/compiled identity and deterministic provider
project/location/job ID. The initial event is enforced by a deferred commit-time
constraint: an intent without its CREATED revision cannot commit.

The journal is the state and lease authority, not a mutable status flag. Intent
row locks serialize writers, while exact expected revisions and token/generation/
DB-clock checks reject stale owners. Lease events keep the same phase. Update,
delete and truncate are forbidden. A missed response is resolved by inspecting
committed state; an old CAS must fail rather than duplicate an event.

| Operation | Meaning and authority |
| --- | --- |
| `prepare_authenticated_send_intent` | Session cookie resolved from the real DB, current workspace checked, existing 0053 checkpoint required. New intents recompile stored logical SQL against exact current publication/receipt and current budgets. No new reservation/claim; repeated preparation preserves identity/phase. |
| `inspect_recovery` | Internal trusted-worker metadata lookup, including disabled/revoked accounts. No customer authorization or results API. |
| `acquire_recovery` | CAS lease on the journal, 1-120 seconds (default 30); fresh UUID/generation. Expiry permits another lookup owner, never resubmission. |
| `record_send_boundary` | Fresh locked session/checkpoint/publication/compiler/budget revalidation, then INTENT_RECORDED -> SEND_UNKNOWN. This records ambiguity only, never permission or an actual send. |
| `record_observation` | Token-fenced bounded enum report for the exact expected job triple. NOT_FOUND/UNCERTAIN preserve phase; IN_PROGRESS records PROVIDER_RUNNING; terminal reports remain *_OBSERVED and unsettled. No provider object or attestation boolean accepted. |

Public preparation results contain only the handle, revision and classification.
Worker leases contain opaque internal metadata-writing tokens (repr excluded),
and worker views contain internal expected job scope; neither may be serialized
into a public API or treated as an execution permit. `execution_authorized` and
`provider_dispatched` are always false. The module is not wired to a startup loop,
HTTP route, provider or worker dispatcher.

Routing labels are conservative: NO_DISPATCH_REVIEW_REQUIRED, LOOKUP_REQUIRED,
LOOKUP_LATER, NOT_FOUND_HELD, LOOKUP_RETRY, TERMINAL_OBSERVED_HELD, REVOKED_HELD,
EXPIRED_HELD, or SETTLED_HELD. None means resubmit, release, return data or ACK a
queue message. Revocation/expiry overlays are derived from 0053, not new parallel
authorities. Settlement by existing services remains authoritative and causes
new lease/writes to fail closed; it does not delete or release journal history.

Terminal observations may be re-observed under a fresh lookup lease without
resetting phase. Contradictory terminal/running reports are rejected and require
review. Matching reports are **not independently verified provider proof**. The
existing provider represents cancellation errors as FAILED; the local CANCELLED
report is diagnostic vocabulary for a future trusted adapter, not a cancel RPC
or evidence that a cancellation request finished. This step never calls even the
existing lookup-only provider; tests supply bounded metadata reports to exercise
journal behavior, not fake live-provider acceptance.

Current compilation/pins remain single-model **development/Mumbai** as in 414D5.
Project and location are derived from existing authority, not caller arguments;
0054 does not enable Sydney/production or broaden region/name restrictions.

## Remaining release blockers

**P0:** No reviewed cloud send/response-loss/cancellation integration, independent
provider attestation, billing/configuration/labels/reference verification or
verified settlement bridge exists in this checkpoint. Matching job IDs or terminal
enum reports cannot settle accounting. Do not feed these diagnostics directly into
existing settlement APIs as provider evidence. No 414E2/414F/414G work is included.
An expired/revoked checkpoint with no registered E1 intent remains held for
existing/manual recovery; E1 does not retroactively authorize it. Historical query
reconstruction and production controller integration remain outside this checkpoint.

**P0:** Physical table immutability/ACLs and an independently justified retention
release/deletion protocol remain open. 0053 holds are deliberately indefinite.
Neither journal terminality nor expired ownership proves a physical job stopped.

**P0:** Recovery functions deliberately operate after owner/session revocation for
bookkeeping; they must remain reachable only by trusted backend controllers.
There is no new permission grant or HTTP interface. Restricted deployed-role and
cross-tenant controller review is mandatory; schema-owner fixtures do not prove
least privilege. New tables/functions revoke PUBLIC; roles/grants were not changed.
Deployment needs reviewed SELECT/INSERT and row-lock privileges (FOR UPDATE on
immutable intents still requires appropriate SQL UPDATE privilege; triggers reject
actual mutations). No such grant is included or implied here.

**P1:** Durable scheduling, bounded lookup backoff/escalation, permanently missing
jobs, process termination, network partitions, operational resource limits and
live provider retention behavior need review. Recovery leases fence metadata,
not in-flight physical queries. No background thread/renewal daemon is added.

**P1:** Future HTTP integration needs session/CSRF, safe error mapping and rate
limits; region/production bindings need their own approvals. No deployment or
application-schema migration is authorized by these tests.

## Exact verification command

The feature worktree's virtualenv is incomplete and has no .env. Use the existing
populated interpreter without installing packages. Only the authorized local
DATABASE_URL is copied process-locally; its value is never printed. The established
runner rejects non-loopback hosts before connecting, blocks external sockets/SDKs,
sets RUN_DB_TESTS=1, disables processing/Exact overrides and applies migrations
only through the existing UUID-schema fixtures. No application/public-schema DDL
or roles are changed. The auth key is a public synthetic test value only.

```powershell
Set-Location 'D:\Project\datarise-warehouse-ux'
$env:RUN_DB_TESTS='1'
$env:APP_ENV='development'
$env:AUTH_MODE='session'
$env:AUTH_SECRET='synthetic-dispatch-session-test-secret-414d5'
$prior=Get-Content docs/warehouse_sql_dispatch_checkpoint_414d5_validation.json -Raw | ConvertFrom-Json
$tests=@($prior.runs[-1].modules.PSObject.Properties.Name | ForEach-Object { $_.Replace('.','/')+'.py' })
$tests+= 'tests/test_warehouse_sql_send_recovery_414e1.py'
$python='D:\Project\ai-data-workspace\.venv\Scripts\python.exe'
$bootstrap="import os,runpy; from dotenv import dotenv_values; os.environ.setdefault('DATABASE_URL',dotenv_values(r'D:\Project\ai-data-workspace\.env')['DATABASE_URL']); runpy.run_path('tools/task20_local_tests.py',run_name='__main__')"
& $python -c $bootstrap @tests -q --tb=short -ra -p no:cacheprovider --evidence-dir "$env:TEMP/414e1-final-events" --junitxml="$env:TEMP/414e1-final.xml"
```

For focused verification, the same interpreter/bootstrap command was run with
only `tests/test_warehouse_sql_send_recovery_414e1.py`, without --evidence-dir,
and `--junitxml="$env:TEMP/414e1-acceptance.xml"`. Final results follow below.

## Exact phase inventory

All six files are new; no committed source, earlier migration, infrastructure,
UI or historical report is modified:

- `app/db/warehouse_query_send_repository.py`: bounded transactions, identity locks,
  journal append, exact revision/owner CAS and safe conflict vocabulary.
- `app/services/warehouse_sql_send_recovery_service.py`: authenticated preparation,
  lookup-only leases, ambiguity boundary, bounded observations and classifications.
- `migrations/0054_warehouse_query_send_recovery.sql`: immutable intents, append-only
  transition/lease guards and deferred initial-event invariant; no grants.
- `tests/test_warehouse_sql_send_recovery_414e1.py`: real PostgreSQL session,
  identity, rollback, clock-expiry and adversarial transaction coverage.
- `docs/warehouse_sql_send_recovery_414e1.md`: this design, threat model and commands.
- `docs/warehouse_sql_send_recovery_414e1_validation.json`: exact results, hashes,
  module inventory and diagnostic history.


## Final local acceptance - 2026-11-11 (Asia/Calcutta)

**414E1 checkpoint PASS. LIVE analytical BigQuery execution remains NO-GO.**

The exact combined command above actually passed **187 tests, 0 failed, 0 errors,
0 skipped**, in **390.35 seconds**. It re-exercised the complete prior 156-case
selection (including all 33 D5 cases and all ten D4d/e recompilation cases) plus
**31 new E1 PostgreSQL cases**. No source/test change followed this final run.

The focused final E1 module separately passed **31 / 0 / 0 / 0**, 93.76 seconds.
An earlier intermediate 26-case selection passed in 74.34 seconds; it started
before the final invariant/case additions and is not final-source acceptance.
The two final-source runs cover the deferred creation invariant, token fencing
and revocation-order cases. The smoke
passed one case in 4.50 seconds. The initial smoke launch had one collection error
from quoting in a new test SQL literal, before any test/DB fixture ran; it was
corrected without changing a security assertion. That diagnostic XML is retained.

Actual backend blocking is asserted with pg_blocking_pids for competing lease
claims and both revocation/send-boundary commit orderings. Explicit events,
bounded waits and finally cleanup establish overlap. Tests also prove deferred
commit rejection without an initial journal, rollback before commit, stale-CAS
response-loss behavior, unchanged job identity/phase on expired-owner reclaim,
wrong scope/token rejection, held NOT_FOUND/UNCERTAIN/terminal/cancel reports,
expired or revoked checkpoint restrictions and unchanged old settlement/holds.

Final JUnit XML: `$env:TEMP/414e1-final.xml`; incremental cases:
`$env:TEMP/414e1-final-events`. Hashes, counts per module, diagnostics, commands
and exact inventory are in the adjacent validation JSON. All 31 new cases install
0054 after real 0053/served metadata; older regression fixtures preserve their
established migration selections. This is not a full-repository, deployed-role,
OS/process-kill, live cloud, actual network-send/cancellation or billing test.
Fake warehouse materialization and supplied diagnostic reports are not live
provider acceptance or independent provider attestation.

Only the six new inventoried files were added; all committed 414D5/other source,
historical evidence, master, UI and infrastructure remain unchanged. Syntax,
whitespace and scoped content-secret checks passed; no index or HEAD change.
No dependency installation, cloud API/credentials, application/public-schema
migration, role/permission grant, staging, commit or push occurred. Stop here for
review; do not implement 414E2/414F/414G or activate execution from this result.
