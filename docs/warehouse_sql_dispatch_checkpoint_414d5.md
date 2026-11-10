# Step 414D5 ? durable non-dispatch authorization checkpoint

## Security design (written before implementation)

Threats: forged principals/owner keys/SQL or permit objects; stale stored drafts,
compiled intent, serving receipts or publication heads; concurrent duplicate
claims; account/session/workspace revocation racing claim; and crashes around
claim/commit/send. Schema-owner SQL and cloud administrators remain privileged.

Verified gaps in 413H and 414D1?4e: session authentication and recompilation use
separate transactions; QueryGovernanceService is forgeable; ACTIVE?EXECUTING
alone does not establish compilation provenance; 0051 only retains tables while
reservations are live; no atomic network-send, cancellation or deletion protocol
exists. No fresh independently trusted provider attestation is persisted here.

Chosen minimum checkpoint: an internal function accepts only ASGI Request,
workspace ID and server-issued attempt handle. It validates the real session in
PostgreSQL, locks user/session/workspace, reuses stored-draft authoritative
recompilation in the same transaction, locks exact publication heads/receipt and
source membership, rechecks current server budgets including held reservations,
and atomically claims and records immutable provenance. No caller SQL, principal,
provider, policy override, permit or enabling flag is accepted. No HTTP route.

The record is NOT permission to send: execution_authorized and provider_dispatched
remain false. The sole first result is CHECKPOINTED_NO_DISPATCH. A duplicate is
RECOVERY_REQUIRED; an existing claim without this checkpoint also requires
recovery and is never retroactively authorized. All cloud query execution remains
NO-GO. Failed transactions roll claim and checkpoint back together.

Revocation: row locks serialize preparation with ordinary session/user/workspace,
model/resource/dataset and publication-head writers. Additive database triggers
append immutable revocation notices after relevant changes. Before-claim changes
are re-read under locks and denied; after-commit changes revoke the checkpoint.
A later reversal cannot erase the notice. Expiry is database-clock bounded.
There is no promise to stop an already-running cloud query: 414E must review a
server-owned send-intent/job-recovery/cancellation boundary and explicit policy
for revocation after that boundary. No PostgreSQL transaction spans cloud IO.

Retention: every checkpoint holds its exact publication indefinitely in the
existing dependency/retirement protocol, even after revocation, lease expiry,
settlement or a crash. Neither time nor terminal accounting proves a physical
job stopped. No hold-release API or table deletion is implemented; 414E must
require independent terminal-job evidence plus serialized cleanup/tombstones.
Out-of-band BigQuery deletion cannot be prevented by PostgreSQL: cloud ACLs,
immutable table policy and compliant cleanup remain deployment blockers.

Lock order: existing global/workspace governance advisory locks first, then
user, session, workspace; resource retirement locks in ascending order before
attempt/reservation locks (matching 0051 link insertion), resource/head/source rows; authoritative recompilation and reservation claim.
The new boundary uses transaction-local 5-second lock and 10-second statement
timeouts; no existing consumer's configuration is changed. Existing writers may
cause deadlock/timeout aborts: roll back and fail closed,
never retry by generating another identity. Nothing changes their lock semantics.

## Scope

Additive 0053 follows 0048?0052 (both existing 0048 migrations remain unchanged).
Only disposable PostgreSQL schemas may apply it. No AWS/GCP calls, role changes,
production migrations, analytical endpoint, UI or publication algorithms.

## Implemented boundary and limitations

`checkpoint_authenticated_warehouse_dispatch` accepts only Request, workspace ID
and handle. It hashes the real session cookie and resolves/locks the session and
current database user in the authorization transaction, using the existing auth
principal projection and digest. It does not trust request.state, an owner_key
argument or a previously constructed governance object. Session absolute/idle
expiry and revocation are checked with the database clock. This specialized locked
resolver deliberately avoids the old authenticate-in-another-transaction race.

The existing trusted recompiler now has a private transaction-composable helper;
its original public non-executing interface and result are unchanged. The new
boundary stores exact compiler intent/SQL digest, deterministic **expected** job
identity, publication/serving receipt evidence, server policy and expiry, and
claims the same reservation in that transaction. `provider_job_id` is the predicted
recovery identity, NOT evidence of a provider submission. No SQL/token/table or
permit is returned; the result contains only the original handle and state.
Budgets include existing held reservations once, not an extra charge at claim.

0053 creates append-only checkpoints/revocations and extends the existing
`warehouse_query_publication_dependency_present` function; there is no parallel
publication or budget authority. Relevant session, account, workspace, model,
source, resource and head changes append revocations. Undoing a change does not
unrevoke an old checkpoint. Time expiry never releases its hold. Current
warehouse heads are required before claim; an advance after commit revokes the
checkpoint while preserving its old physical snapshot dependency. Dataset source
changes are conservatively revoking events; this is not a promise of zero-lag
warehouse ingestion freshness.

Existing claims without checkpoint provenance return recovery-only, never a new
claim. A failed transaction, including an exception after claim, leaves ACTIVE
and no checkpoint. A committed checkpoint followed by a lost response returns
recovery-only on retry. Recovery cannot use this result to submit missing jobs.
This internal non-dispatch checkpoint intentionally leaves EXECUTING accounting
held: do not expose it as an end-user endpoint or routine operational rehearsal.

## Remaining blockers (live execution remains NO-GO)

- Actual server-controlled provider construction, independent fresh metadata and
  dry-run attestation, exact configuration/labels/project/location verification,
  provider job evidence and a reviewed send-intent/cancellation boundary are still
  required. Persisted reservation estimates are revalidated, not freshly attested
  by BigQuery here. No statement about live provider acceptance is made.
- Revocation forbids future work; it cannot undo a submitted or running query.
  A commit/send ambiguity must use the original deterministic identity and
  accounting, not re-admission. No 414E/414F/414G implementation is included.
- Holds are intentionally indefinite. There is no hold-release, physical deletion
  or garbage collector. Future release needs independently verified provider
  termination, recovery/concurrency fencing, retention policy and cleanup authority.
- This is a trusted-backend, schema-owner-tested boundary, not RLS. The migration
  uses SECURITY INVOKER triggers, revokes PUBLIC on new tables/functions, and
  installs pinned trusted search_paths. Runtime roles must have reviewed SELECT
  and checkpoint/revocation INSERT rights, including trigger-writing auth/control
  roles, before deployment. No roles/grants were changed or tested here; missing
  privileges abort the writer transaction rather than silently omitting revocation.
- Future HTTP integration needs existing session/CSRF middleware, authorization,
  safe error handling and rate limits. There is no route in this checkpoint.
- Existing publishers and auth writers retain their original lock semantics.
  Deadlock, lock timeout or database loss must abort, with same-handle retry or
  recovery; broader process/provider fault testing remains a subsequent review.

## Verification commands

Run from the existing feature worktree. Its local virtualenv lacks dotenv and it
has no .env; use the existing populated interpreter without installing packages.
Only DATABASE_URL is copied from the established local configuration into the child
process environment. The existing runner checks loopback before connecting, blocks external
sockets/SDKs, sets RUN_DB_TESTS=1, disables Exact/processing flags, and fixtures
create/drop only UUID schemas. No application-schema migration was performed.
`AUTH_SECRET` below is a public synthetic test value, never a deployment secret.

```powershell
Set-Location 'D:\Project\datarise-warehouse-ux'
$env:RUN_DB_TESTS='1'
$env:APP_ENV='development'
$env:AUTH_MODE='session'
$env:AUTH_SECRET='synthetic-dispatch-session-test-secret-414d5'
$python='D:\Project\ai-data-workspace\.venv\Scripts\python.exe'
$bootstrap="import os,runpy; from dotenv import dotenv_values; os.environ.setdefault('DATABASE_URL',dotenv_values(r'D:\Project\ai-data-workspace\.env')['DATABASE_URL']); runpy.run_path('tools/task20_local_tests.py',run_name='__main__')"
$tests=@(
 'tests/test_warehouse_sql_dispatch_checkpoint_414d5.py',
 'tests/test_warehouse_sql_gateway_readiness_414d1.py',
 'tests/test_warehouse_sql_authenticated_readiness_414d2.py',
 'tests/test_warehouse_sql_compilation_comparison_414d3.py',
 'tests/test_warehouse_sql_stored_draft_414d4a.py',
 'tests/test_warehouse_sql_attempt_identity_bridge_414d4b.py',
 'tests/test_warehouse_sql_stored_publication_evidence_414d4c.py',
 'tests/test_warehouse_sql_trusted_recompilation_414d4d.py',
 'tests/test_warehouse_sql_trusted_recompilation_414d4e.py',
 'tests/test_warehouse_query_reservation_publication_links_postgres.py',
 'tests/test_warehouse_query_publication_retirement_postgres.py',
 'tests/test_warehouse_query_publication_inspection_postgres.py',
 'tests/test_query_governance_postgres.py',
 'tests/test_warehouse_sql_claim_rehearsal_postgres.py',
 'tests/test_warehouse_sql_recovery_inspection_postgres.py',
 'tests/test_warehouse_sql_recovery_settlement_postgres.py'
)
& $python -c $bootstrap @tests -q --tb=short -ra -p no:cacheprovider --evidence-dir "$env:TEMP/414d5-final-events" --junitxml="$env:TEMP/414d5-final.xml"
```

## Files and scope

- Modified: `app/services/warehouse_sql_trusted_recompilation_service.py` ? extract
  a shared private transaction body; preserve the original public read contract.
- Added: `app/services/warehouse_sql_dispatch_checkpoint_service.py` ? locked
  server session, exact intent/budget revalidation, atomic claim/provenance.
- Added: `migrations/0053_warehouse_query_dispatch_checkpoints.sql` ? immutable
  checkpoint/revocation metadata, retirement holds and revocation notices.
- Added: `tests/test_warehouse_sql_dispatch_checkpoint_414d5.py` ? genuine sessions,
  immutable publications/receipts, claims, expiry, adversarial overlap and rollback.
- Added: this design document and `warehouse_sql_dispatch_checkpoint_414d5_validation.json`
  with exact result counts, module inventory, source hashes and diagnostics.

No analyst UI, ingestion/publication algorithm, infrastructure or earlier migration
was edited. The original runtime-readiness checkout and master were not modified.
Nothing was staged, committed, pushed, deployed or activated.



## Final local verification ? 2026-11-11 (Asia/Calcutta)

**Checkpoint PASS; live analytical BigQuery dispatch remains NO-GO.**

The final combined selection actually ran: **156 passed, 0 failed, 0 errors,
0 skipped**, 312.29 seconds. It includes all **33 new checkpoint tests**, all ten
prior 414D4d/e trusted-recompilation cases, and 113 other compatibility cases.
The new tests use real PostgreSQL sessions, actual immutable publication and
serving receipts and the fake warehouse provider only for fixture materialization.
The final-source selection was not rerun unnecessarily; no production/test source
changed afterward. XML evidence: `$env:TEMP/414d5-final.xml` and incremental case
records under `$env:TEMP/414d5-final-events`.

Both competing checkpoints and claim-first/revocation-first interleavings prove
actual backend blocking with `pg_blocking_pids`, not thread-start assumptions.
They use explicit events and bounded cleanup. Real database-clock lease expiry,
revoked/expired/foreign sessions, changed ownership/model/head, permanent revocation
after restoration, exact compiler and row-budget mismatch, pre-commit rollback,
post-commit retry, unproven old claims, immutable records and retention after
terminal accounting passed. No stale/expired checkpoint becomes permission to send.

Earlier evidence is preserved separately: smoke **1 passed** (4.46 s); first
focused run **22 passed / 2 failed** (66.00 s), from model-archive test setup
omitting the existing required archived_at field; corrected intermediate suite
**40 passed** (113.13 s). The final suite includes the corrected archive cases,
new configuration/transaction checks and final bounded-lock/retirement order.
No schema constraint or security assertion was weakened. Two initial launch
attempts failed before pytest (missing dotenv in the worktree virtualenv, then
missing DATABASE_URL); these are setup blockers, not passing tests or skips.

Compatibility fixtures keep their established migration selections; the 33 new
cases install 0053 after 0052 on real served metadata and exercise subsequent
publication/auth/accounting. This is not a full-repository regression, deployed
restricted-role acceptance, penetration test or live BigQuery test. Migration
0053 is once-only; reapplication is rejected without partial changes. The auth
writer and checkpoint runtime grants require review before any real deployment;
otherwise SECURITY INVOKER revocation triggers can abort the writer transaction.

AST, scoped content secret screening and git diff --check passed. The requested
feature worktree started clean and has exactly the six inventoried phase files
(one modified, five added); HEAD remains 5f673aebcb0dba96700ef09168dcb912b567ec18.
Master, original checkout work, earlier migrations, UI and infrastructure were
not changed. No dependencies installed, application migrations applied, roles
created/modified, cloud requests sent, or Git staging/commit/push performed.
