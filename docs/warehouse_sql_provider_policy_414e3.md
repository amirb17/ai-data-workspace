# Step 414E3 - server-owned, NON-DISPATCH provider policy

## Security design before implementation

Reviewed committed D5/E1/E2 at 6e42063 on feature/warehouse-model-ux, migrations
0053-0055, deterministic SQL compiler and BigQuery configuration builder, session
resolver, recovery journal, SDK hydration and prior final verification evidence.
Worktree began clean. E2 has 50 final affected cases plus 187 prior cases; its
237-case union is not misrepresented as one final combined run.

Verified gaps: E2 compares maximumBytesBilled/jobTimeoutMs as upper bounds and
labels as a required subset. Its reviewed runtime policy is hashed per observation,
but not durably bound to an authenticated attempt before lookup; policy drift can
change expectations. D5 pins SQL/intent/publication and governance policy but not
provider creator/runtime configuration. E3 will not retroactively attest the
historical submitter or authorize any job submission.

Threats: caller-built principals/compiler/provider/configuration objects; wrong
project/dataset/location/job/labels/SQL/billing; stale publication/draft/ownership;
policy replacement, including during lookup; concurrent preparation/observation;
response loss; stale recovery owners; denied provider/SQL privileges; and forged
terminal reports. Trusted schema owners and compromised backend factories remain
privileged. Metadata is not a cryptographic provider signature or billing receipt.

Chosen checkpoint:

1. Empty-by-default trusted workspace-to-provider-policy registry. Exact server
   project, development/Mumbai location, dataset naming, creator, timeout and
   maximum byte policy; reuse E2's empty trusted lookup runtime registry.
2. Session-authenticated preparation accepts only ASGI Request/workspace/handle.
   Resolve and lock the actual PostgreSQL session/user/workspace; require original
   D5 session identity, existing E1 intent, live checkpoint, exact stored logical
   recompile, current publication/receipt, original intent/reservation and budgets.
   No caller-owned governance/query/provider/configuration object is accepted.
3. Derive and persist one immutable expected policy per attempt in additive 0056.
   It binds exact provider scope/job/labels/SQL digest, publication/receipt digest,
   byte cap, timeout and provider/lookup/governance policy digests. Retries compare
   all fields; never overwrite/rebind after a policy change.
4. Backend lookup takes only E1 lease/revision. Read immutable expected evidence,
   current trusted policies and pinned historical receipt under E1 locks. Close
   transaction before E2's GET-only full-metadata transport. Strictly check exact
   labels, exact billing cap/timeout, required config and provider evidence using
   the E2 validator, never submission/result/cancel APIs.
5. Recheck policy/configuration and original evidence plus token/revision/DB-clock
   lease after IO. Atomically append E1 observation and 0055 attestation, storing
   the exact expected-policy digest. Policy drift aborts without a journal write;
   mismatched provider evidence records UNCERTAIN/REJECTED, never success.

Lock order: global/workspace governance; user/session/workspace for preparation;
ascending publication retirement locks; attempt/reservation; immutable intent;
journal append. Use existing 5s lock/10s statement bounds. Nested repository
transactions remain rejected. No transaction spans provider IO. DB loss/denied
privilege/uncertain commit must fail closed; inspect same identity, never resubmit.

Revocation forbids new preparation. Existing pinned historical lookup may continue
for trusted bookkeeping with original ownership provenance, preserving D5 revocation
and expiry overlays. Current heads are required only at authenticated preparation;
a later advance cannot authorize another query or release old holds. No automatic
settlement, data access, cancellation, retention release or public route exists.

Policy changes are not historical proof. Before any activation, reviewed submitter
provenance, real metadata/default compatibility, TLS/IAM, least-privilege DB roles,
physical table ACLs, operational recovery and settlement need independent acceptance.
All public results remain non-executing; LIVE analytical BigQuery remains NO-GO.

## Implementation and evidence policy

`prepare_authenticated_provider_policy` accepts only Request/workspace ID/server
handle. It resolves the real session in PostgreSQL, requires the exact D5 user
and original session digest, reuses authoritative stored-draft recompilation and
live publication/serving/budget checks, and pins the policy once. A newly issued
session for the same user cannot silently substitute for the checkpoint session.
No QueryGovernanceService, CompiledQuery, owner_key, SQL, provider, config, permit
or enabled flag is accepted. The result contains only handle/classification and
inherits non-executing properties.

Server ProviderPolicy is frozen and workspace-bound; both it and E2's lookup
registry are empty by default. E3 requires explicit matching client project and
location, the reviewed creator/optional subject and exact timeout relationship.
It never inspects credential objects or treats client defaults as identity proof.
Dataset grammar stays `dr_dev_w{workspace}` and location stays asia-south1; no
production/Sydney binding is added. Exact job identity derives from original
workspace/reservation/request token. SQL and compiled intent derive from immutable
D5 evidence revalidated against the actual stored draft. All labels are the four
original server-generated application labels, with no caller-added extras.

0056 adds only `warehouse_query_provider_policies`, one immutable expected JSON
and SHA-256 per existing E1 intent. It stores server provider/lookup policy metadata
(not credentials), exact scope/labels, SQL/intent fingerprints, original pinned
receipt digest, byte cap and timeout. The cap is the minimum of original reserved/
maximum-billed/governance ceilings and server provider maximum, never a new client
budget. Full governance policy must still equal the D5 snapshot. Even changes that
leave the effective cap unchanged cause drift because the policy identity changed.
Retries cannot overwrite or rebind the row. PUBLIC access is revoked, functions
have pinned search_path, and update/delete/truncate are rejected. It is a once-only
migration after 0055, applied only by disposable test schemas in this phase.

`lookup_with_provider_policy` accepts only an E1 lease/revision. It reloads the
current server policy, immutable binding, original attempt/reservation/job identity,
and exact historical pins/receipt, then uses E2's GET-only full SDK metadata path.
Before and after IO it compares the complete expected policy with its pinned hash;
policy drift aborts without writing a misleading observation. Correctly attributed
but mismatched provider configuration is recorded as UNCERTAIN/REJECTED. Exact
labels, maximumBytesBilled and jobTimeoutMs are required, including lower values;
dryRun must explicitly be false. E2 still checks creator, full job identity, SQL,
SELECT/reference scope, unknown config, state and billing evidence. Optional
allowLargeResults omission retains its existing documented false default; unknown
settings/defaultDataset/destination/session/script configuration remain rejected.
This is semantic numeric equality, not dependence on JSON integer spelling.

E3 atomically appends the E1 observation plus the existing 0055 attestation, whose
policy_sha256 equals the immutable 0056 expected_sha256. This distinguishes exact
E3 checks from older E2 runtime-only attestations without changing their contract
or forging a successful QueryRecovery/ExecutionPermit. Its result is the existing
non-executing RecoveryAttestation, internal bookkeeping only. No raw SQL, error
text, credentials, tokens or table identifiers are added to public preparation.
The internal recovery view is not a public HTTP response/status DTO.

A private lookup-validation helper was extracted from E2 without changing its
public API or behavior, avoiding duplicate error/retry logic and transport calls.
No legacy provider execution/recovery/result path is invoked. The existing SDK
private full-metadata boundary and exact timestamp normalization remain covered by
real synthetic QueryJob hydration. Missing metadata can make valid historical jobs
uncertain; automatic destination inference, incomplete creator/statistics and
unknown future defaults are never guessed away.

## Minimum privileges for review - none applied

Use a dedicated observer with `bigquery.jobs.get` for the exact job project.
The creator exception can require jobs.create; E3 recommends an independent
observer with explicit lookup authority, not granting submission to recover jobs.
Do not give this component jobs.create, cancel/update, jobs.list/listAll or table
result/data-reading privileges merely for this path. Query text in job metadata
is itself sensitive. Effective IAM and authenticated transport remain unverified.
[Official jobs.get permissions and regional scope](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/get).

PostgreSQL preparation needs existing SELECT on auth/session/workspace, attempts,
logical drafts, governance/reservations, models/sources, publications/receipts and
compiler bindings, plus SELECT/INSERT on 0056. Recovery additionally needs existing
E1 journal and 0055 attestation SELECT/INSERT. FOR UPDATE and FOR SHARE require
UPDATE privilege on at least one column of each locked table; review narrow column
privileges on auth/control metadata and immutable records rather than broad grants.
[PostgreSQL SELECT/locking privileges](https://www.postgresql.org/docs/current/sql-select.html).
No API may expose the trusted controller's SQL capability. Existing immutable
triggers constrain mutations, but this is not RLS or protection from schema owners.
SECURITY INVOKER guards need referenced SELECT rights; migration-owner installation
of triggers is separate from runtime calls. No SECURITY DEFINER bypass or role/grant
script is added. Auth writers' existing D5 revocation-write rights remain required.

Provider Forbidden errors are classified as UNCERTAIN with a safe bounded reason.
SQL permission failure is tested by an actual PostgreSQL 42501 fault within the
transaction, not by changing grants/roles. The assertion checks rollback before
policy insert and after journal/attestation insert, then same-identity retry.
These tests prove fail-closed handling, not deployed restricted-role acceptance.

## Remaining P0/P1 release blockers

- P0: Both trusted registries remain empty; no client, credentials, runtime factory
  or activation is wired. Initial reviewed policy provenance is required; pinning
  current expectations is not proof of a historical submitter or send. There is
  no analytical query submission, result delivery, cancellation or settlement bridge.
- P0: Effective IAM, TLS/endpoint/credential provenance, restricted DB roles and
  cross-tenant controller isolation need independent acceptance. Matching SDK
  configuration and fake REST payloads do not prove actual provider authentication.
- P0: Indefinite D5 holds remain. Physical-table ACLs/immutability and independently
  verified cleanup/release remain open; PostgreSQL cannot prevent cloud-side deletion.
- P1: Missing/expired provider history, backoff/escalation, operational scheduling,
  SDK upgrades/private _properties compatibility and real default/destination fields
  require review. No automatic resubmission, acknowledgement or retry schedule exists.
- P1: Registry configuration must remain trusted immutable startup state, with a new
  policy revision for any reload. No hot-reload/mutation API is included; cross-process
  configuration rollout and undetectable privileged change-and-restore between reads
  are not claimed solved by local comparisons. No lock spans provider IO.
- P1: Existing terminal diagnostic conflicts remain held for independent review.
  Metadata checking, billing-counter presence and cancellation observation are not
  accounting settlement, a cancellation RPC, physical stop proof or retention release.

LIVE analytical BigQuery remains NO-GO. No 414F, endpoints, AWS, UI, deployment,
permissions application, application-schema migration or pipeline change is included.

## Exact PowerShell verification

Run from the existing feature worktree, with the populated interpreter. No
packages are installed. Only the already-authorized local DATABASE_URL is copied
from the established configuration into the test process; its value is not printed.
The runner rejects non-loopback PostgreSQL, blocks external SDK/socket access,
sets RUN_DB_TESTS=1 and disables processing/Exact overrides. Existing fixtures apply
migrations exclusively to UUID temporary schemas and drop only their own schemas.
No role, grant, application/public-schema migration or cloud call is made. The
AUTH_SECRET below is public synthetic test data.

```powershell
Set-Location 'D:\Project\datarise-warehouse-ux'
$env:RUN_DB_TESTS='1'
$env:APP_ENV='development'
$env:AUTH_MODE='session'
$env:AUTH_SECRET='synthetic-dispatch-session-test-secret-414d5'
$python='D:\Project\ai-data-workspace\.venv\Scripts\python.exe'
$bootstrap="import os,runpy; from dotenv import dotenv_values; os.environ.setdefault('DATABASE_URL',dotenv_values(r'D:\Project\ai-data-workspace\.env')['DATABASE_URL']); runpy.run_path('tools/task20_local_tests.py',run_name='__main__')"
$tests=@(& $python -c "import json,pathlib; a=json.loads(pathlib.Path('docs/warehouse_sql_send_recovery_414e1_validation.json').read_text())['validation']['runs'][-1]['modules']; b=json.loads(pathlib.Path('docs/warehouse_sql_recovery_attestation_414e2_validation.json').read_text())['validation']['runs'][-1]['modules']; print('\n'.join(k.replace('.','/')+'.py' for k in dict.fromkeys([*a,*b])))")
$tests+='tests/test_warehouse_sql_provider_policy_414e3.py'
& $python -c $bootstrap @tests -q --tb=short -ra -p no:cacheprovider --evidence-dir "$env:TEMP/414e3-final-events" --junitxml="$env:TEMP/414e3-final.xml"
```

The focused command, using the same environment and bootstrap:

```powershell
& $python -c $bootstrap tests/test_warehouse_sql_provider_policy_414e3.py -q --tb=short -ra -p no:cacheprovider --evidence-dir "$env:TEMP/414e3-focused-events" --junitxml="$env:TEMP/414e3-focused.xml"
```

The initial smoke used that module's
`::test_authenticated_exact_policy_idempotency_and_checked_sdk_metadata`, without
--evidence-dir, and `--junitxml="$env:TEMP/414e3-smoke.xml"`.
It passed 1 case in 8.35 seconds. The complete focused module passed 51 cases in
178.47 seconds, zero failures/errors/skips. Final combined results follow below.

## Exact source and migration inventory

- Modified `app/warehouse/query_recovery_attestation.py`: private internal validator
  composition, retaining E2 public lookup/error semantics and one GET per lookup.
- Added `app/warehouse/query_provider_policy.py`: frozen server configuration and
  exact comparison layered on independent E2 evidence validation.
- Added `app/services/warehouse_sql_provider_policy_service.py`: actual-session
  preparation, durable immutable expectation, drift checks before/after IO and
  existing token/revision-fenced journal/attestation transaction.
- Added `migrations/0056_warehouse_query_provider_policies.sql`: expected-policy
  table and immutability/identity guards; no earlier migration changes or grants.
- Added `tests/test_warehouse_sql_provider_policy_414e3.py`: 51 new PG/fake SDK cases.
- Added this design and `warehouse_sql_provider_policy_414e3_validation.json`:
  commands, evidence, source hashes, inventory and scope limitations.

No original D5/E1/E2 report, pipeline, public route, AWS/UI/deployment file is edited.
Future HTTP integration would require session/CSRF/error/rate-limit review; no such
interface or public policy serialization is included here.

Timeout verification checks requested configuration, not a hard physical deadline:
jobTimeoutMs is a provider stop attempt, and the transport's 10-second timeout is
not an OS kill or a bound on credential-refresh internals. E2's 256 KiB detached
metadata cap is not a pre-allocation process memory limit. These inherited runtime
limitations are not changed by exact policy comparison. No credential/SDK client
is constructed by this phase; only synthetic QueryJob objects are hydrated in tests.

Any future consumer requiring E3 evidence must join the attestation to the same
attempt's immutable expected_sha256; a VERIFIED_METADATA string alone also exists
on older E2 observations and is insufficient. This phase does not wire such evidence
into settlement, result access or execution. Introducing that bridge is a separate
review, not permission inferred from this checkpoint.

Identity is the complete exact jobReference triple, corroborated by creator,
server labels, SQL and pinned table evidence. The API describes top-level id as
opaque; it is not parsed into an alternative identity. selfLink is neither followed
nor used to redirect lookup. Missing/extra/wrong fields in jobReference fail the
existing exact comparison rather than falling back to SDK defaults or opaque IDs.
[Job resource identity/configuration contract](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/Job).

## Final local verification - 2026-11-11 (Asia/Calcutta)

**414E3 checkpoint PASS. LIVE analytical BigQuery execution remains NO-GO.**

The exact combined command above actually passed **288 tests, 0 failed, 0 errors,
0 skipped**, in **806.31 seconds**. This is one successful final-worktree selection,
including all 237 prior cases (D5 33, E1 31, E2 50, plus 123 other prior cases) and
**51 new E3 cases**. All prior module counts match the committed verification
inventories. No production/test source changed after the source freeze and final
run. No full-repository or live provider/deployed-role acceptance is claimed.

Focused E3: **51 / 0 / 0 / 0**, 178.47 seconds. Initial smoke: **1 / 0 / 0 / 0**,
8.35 seconds, before final client-scope/test additions. These overlap the combined
run and are not added to its distinct total. No test failure or skip occurred in
these E3 runs. The exact commands, per-module counts, timestamps, source/XML hashes
and selected case inventory location are in the adjacent validation JSON.

JUnit: `$env:TEMP/414e3-final.xml`; incremental reports and actual collected IDs:
`$env:TEMP/414e3-final-events`. Focused evidence uses the corresponding
`414e3-focused.xml` and `414e3-focused-events` paths. Both selections require
RUN_DB_TESTS=1, and every new case uses real isolated PostgreSQL metadata/sessions.
Real synthetic QueryJob hydration verifies the SDK boundary without constructing
a credentialed SDK client or accessing the network.

The tests demonstrate policy preparation serialization using pg_blocking_pids,
concurrent GET completion with exactly one successful journal revision, policy
replacement during blocked GET, lease expiry/reclaim fencing, original-session
requirements, revocation before preparation and during lookup, immutable original
receipts/holds, same-identity retry, denied provider and SQL privileges, and atomic
rollback of journal/attestation. The exact-policy cases explicitly exercise lower
billing/time caps, extra labels and omitted dryRun that E2's bound/subset checks
permit, and require E3-specific rejection reasons. No assertion was weakened and
no timeout was increased to obtain a pass.

0056 was applied only by UUID-schema fixtures, after 0053-0055. No application or
public-schema migration, grants/roles, dependency installation, AWS/GCP SDK call,
cloud credential construction/use, execution/cancellation/result/settlement call,
UI/infrastructure change or processing activation occurred. Documentation reference
pages were read; no authenticated cloud API/control-plane access was performed.
AST, Git/scoped whitespace and scoped heuristic secret screening passed. The index,
branch and HEAD remain unchanged, with exactly the seven inventoried phase files.
Original D5/E1/E2 reports and migrations remain intact. Nothing was staged, committed
or pushed. STOP at E3 for review; this is no authorization for 414F or live execution.
