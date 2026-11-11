# Task C - security approval checkpoint and safe SDK compatibility fix

**BLOCKED / live BigQuery NO-GO.** This checkpoint does not complete a production
runtime or approve the 414F0 draft. Task A/B contracts, gates and UI are unchanged.
The task explicitly requires stopping affected implementation when security
authority is unresolved. No role/grant migration, DSN routing, credential factory,
sender, cancellation binding or live-provider smoke is authorized by this record.

## Verified blockers

- `app/db/database.py` has one DATABASE_URL and one borrowed-transaction ContextVar.
  Task A's workflow calls claim, send, independent observation, accounting and result
  authorization in the API process. Multiple co-resident DSNs/SDK clients would
  carry union authority; assigning them role-shaped names is not isolation.
- D5/E1/E3 and result authorization use FOR SHARE/UPDATE on identity/control rows.
  Row locks require real UPDATE privileges. Raw reservation status updates and
  shared journal INSERT rights cannot enforce sender/attester/accountant commands.
  SECURITY DEFINER command routines would need reviewed ownership, narrow inputs,
  principal provenance, search_path and per-command ACLs, not an automatic conversion.
- 0053's SECURITY INVOKER revocation trigger reads checkpoint/draft/pin/model-source
  relations and inserts notices for auth/model/publication writers. Too little
  access breaks logout/revocation/publication; broad grants undermine separation.
  Task 20 roles are a different security domain and remain unchanged.
- 0057 hardens only its selected 14 invoker functions, not the complete dependency
  closure. Its source explicitly refuses public/system schemas. Schema migration,
  named/column/default/PUBLIC ACLs, owner/membership and trigger closure still need
  reviewed acceptance. Existing 0046 Sydney DEFINER is not a general warehouse owner.
- The canonical job request has no destinationTable; the existing recovery verifier
  rejects that configuration field. The result IAM preflight requires a separate
  reader with destination-table access and rejects using the query creator. These
  facts are incompatible with assuming shareable anonymous results.

Google documents that temporary query results are restricted to the creator and
cannot be shared: [write query results](https://docs.cloud.google.com/bigquery/docs/writing-results),
[cached/anonymous datasets](https://docs.cloud.google.com/bigquery/docs/cached-results).
This is a documented provider constraint, not a live IAM test. Whether a particular
hydrated job supplies additional/default configuration fields also needs real
provider evidence; none of the current copied-request fakes proves that fidelity.
Do not broaden the verifier allowlist until those fields and destination identity
are independently pinned under a reviewed policy version.

## Smallest boundaries proposed for approval, not provisioning

Seven independent PostgreSQL duty profiles are a lower bound if query and model
control are explicitly approved to share an authenticated API trust domain. Keeping
R1/R2 separate makes eight. This is an alternative to review, not a chosen matrix:

| Duty | Allowed responsibility | Forbidden union |
| --- | --- | --- |
| Authenticated control | Owned drafts/admission/pins/current D5 claim; model control only if approved | Cloud send, attestation, ledger, publication, cleanup |
| Submit/cancel controller | Original immutable send/cancel markers and one-time provider IO | Attestation, settlement, results, publication |
| Independent attester | GET-only original-job metadata, revision/token-fenced observations | Send/cancel/results/ledger |
| Accountant | Exact attested terminal evidence to one usage record | Provider credentials, invented evidence, release |
| Result authorization gateway | Original-session/schema/evidence pre/post checks | Logical metadata mutation, submission, settlement |
| Publisher/materializer | Existing accepted-source physical/publication protocol | Analytical journal/ledger and protected deletion |
| Retention inspector | Existing dependency/retirement inspection | Hold release, fences/deletion execution |

Result data transport should have no PostgreSQL login; its cloud principal and
placement depend on the destination decision below. A warehouse-only NOLOGIN
owner and controlled migration login are separate from all runtime profiles.
No Task 20 identity, membership or ownership is reused. Do not implement SET ROLE,
shared borrowed connections, inherited owners or broad admin grants as a shortcut.
Runtime principals must not own protected objects or be members of owner/runtime/
predefined escalation roles; NOINHERIT alone is insufficient.

## Precise approval decisions (Q1-Q8)

1. **Q1:** preserve D5's atomic API claim, or approve a command/handoff with equivalent
   transaction semantics. Do not silently move it to make grants easier.
2. **Q2:** choose reviewed constrained commands with verified principal provenance
   and a dedicated owner, versus deliberately broader raw-DML trusted-process rights.
   The latter cannot honestly claim command-level least privilege. Neither selected.
3. **Q3:** approve gateway/data-transport placement AND result destination policy.
   A named result destination needs pinned identity, table creation/write authority,
   independent reader ACLs, storage-cost caps and retention/cleanup review plus a
   versioned request/attestation contract. Keeping anonymous results would require
   a creator-scoped result proxy and an explicit exception to the current dedicated
   reader IAM design. **Do not share submitter credentials to make tests pass.**
   Status quo: no live results or query runtime until one design is approved.
4. **Q4:** approve resource/operation registrar and physical-writer vs metadata-
   publisher authority; do not include disabled Sydney authority by default.
5. **Q5:** approve settlement after ownership transfer and permanently missing jobs.
   Existing owner checks remain; no historical-owner bypass or fabricated billing.
6. **Q6:** approve actual role names, direct LOGIN/TLS/credential custody, independent
   processes and factories/connection budgets. Reuse a borrowed connection only
   within the same approved principal/transaction; cross-duty borrowing must fail.
7. **Q7:** no cleanup execution until independent provider termination, hold release,
   physical ACL/retention and serialized deletion protocol are separately approved.
8. **Q8:** jointly approve auth/processing trigger-write closure or narrow reviewed
   revocation commands without broadening/reusing Task 20 roles. No live changes now.

No positive ACL SQL or roles are prepared from an unapproved draft: guessing grants
would select these unresolved alternatives implicitly. Approval must identify exact
commands/relations/columns/sequences/functions and invoker trigger closure before
an additive, repeatable, preflighted migration and separate-login acceptance can be
written. Rollback must preserve immutable evidence/holds and never restore PUBLIC.
Existing restricted-login safety tests are not positive role acceptance; no developer
connection is substituted. Real credentials and production grants remain external.

## Safe implemented changes

`BigQuerySdkResultsConnection` adapts an explicitly supplied installed SDK Client
to the existing GET-only result protocol. Inspection confirmed SDK connection
`api_request` has no retry parameter, while Client._call_api consumes retry=None
before invoking it. The bridge checks project, current Mumbai policy, effective
Google endpoint (including mTLS selection), exact query-result path, parameters,
100-row cap, 1-second query wait and 10-second HTTP timeout. It exposes no client
factory, credentials, SQL submission, cancellation, pagination or authorization.
The caller owns/closes the client; ReadOnlyBigQueryFirstPage still sanitizes errors
and F5 still validates rows/schema and original-session authorization.

Tests exercise the actual installed Client and REST serialization with anonymous
credentials and a fake HTTP session: no SDK method is mocked to pretend it accepts
retry, and no live token/ADC/network is accessed. One SDK request is proved, not an
end-to-end guarantee about a future authorized HTTP session. SDK/auth-session lower
layer retries/credential-refresh resends must be explicitly reviewed before any
sender is bound. Token fencing cannot terminate an already-started physical query.

The existing opt-in restricted-login attribute probe also now checks
`rolreplication`; its original `not any(flags[1:])` denies that escalation flag
alongside superuser/role/database creation/BYPASSRLS. This strengthens a universal
restricted-login condition without assigning warehouse grants. These login tests
remain gated and are not positive restricted-role acceptance in this phase.

The unchanged `get_query_runtime()` always raises 503; no configuration can enable
this bridge through the public API. `_POLICIES`/lookup runtime maps remain empty by
default. A production-capable all-duty factory is deliberately **not implemented**.
No manually constructed permit or new enable flag substitutes for approval.

## Provider policy and IAM prerequisites

- Sender: reviewed jobs.create on the billed project and source tables.getData;
  current compiled SQL, exact job ID/labels/table scope, maxBytesBilled and timeout
  must be derived from immutable SQL evidence. No admin or table-delete role.
- Attester: jobs.get for the original project/location/job; no results/submission/
  cancellation credentials. Metadata alone never settles missing billing.
- Cancel: jobs.update for another principal's job; creator exception differs.
  Request acceptance is not terminal proof and cancelled jobs can incur costs.
- Independent result reader: jobs.get plus destination tables.getData, conditional
  on the approved destination design. Do not infer access to anonymous tables.
- Accountant/gateway: no cloud mutation credential. Publisher permissions remain
  a separate review. No physical deletion rights are proposed.

References: [jobs.get](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/get),
[jobs.getQueryResults](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/getQueryResults),
[jobs.cancel](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/cancel),
[run jobs](https://docs.cloud.google.com/bigquery/docs/running-jobs).
Dry runs can report lower/unknown estimates; never admit those as exact bounds.
BigQuery attempts to stop jobs at jobTimeoutMs; it is not guaranteed physical
termination. [Job configuration](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/Job),
[dry-run limitations](https://docs.cloud.google.com/bigquery/docs/running-queries#dry-run_query).

Gated live smoke code/credential bootstrap is deferred with production wiring:
there is no approved principal/destination/profile to validate yet. A future
separately reviewed smoke must default off, require explicit reviewed credentials
and exact scope, never fall back to ADC/developer credentials, start GET-only, and
keep submission/cancellation/billing/retention acceptance separately authorized.
No live smoke was run or fabricated here.

## Verification and inventory

Final offline: **55 passed, 0 failed/errors/skipped**, 0.82 s (17 new SDK cases,
11 existing read-only transport, 17 IAM inventory and 10 restricted-login gate).
Final PostgreSQL regression: **101 passed, 0 failed/errors/skipped**, 361.49 s.
Normal distinct coverage: **156 passes (17 new + 139 prior)**, no failures/errors/
skips. The separately gated restricted selection passed 10 gate cases (already
counted above) and **skipped 2 actual-login cases** with its explicit gate off;
these are not verified logins. No cloud smoke was run. The 101-case SQL selection
uses real disposable schemas with fake providers; schema-owner inventory does not
prove restricted-role enforcement. No frontend/full-repository regression is claimed.
Initial new fake-session tests failed because the fake lacked SDK close/mTLS
attributes; the fixture was corrected and the same assertions passed. No production
security assertion was weakened. Real restricted-login and cloud acceptance were
not attempted; no skip is being represented as a positive login result.

Changed: `app/warehouse/query_bigquery_readonly_transport.py` and
`tests/test_warehouse_restricted_login_acceptance_414f0b.py` (replication-flag denial).
Added: `tests/test_warehouse_bigquery_sdk_results_task_c.py`, this checkpoint and
`docs/warehouse_runtime_task_c_validation.json`. No migration, role/grant, routing,
Task 20, frontend or historical report changed. Exact commands, module counts,
JUnit hashes, source hashes and static checks are in the validation JSON.

```powershell
Set-Location 'D:\Project\datarise-warehouse-ux'
$env:APP_ENV='development'
$env:AUTH_MODE='session'
$env:AUTH_SECRET='synthetic-dispatch-session-test-secret-414d5'
$python='D:\Project\ai-data-workspace\.venv\Scripts\python.exe'
& $python tools/task20_local_tests.py --offline tests/test_warehouse_bigquery_sdk_results_task_c.py tests/test_warehouse_bigquery_readonly_transport_414f5.py tests/test_warehouse_query_result_iam_preflight_414f0c.py tests/test_warehouse_restricted_login_gate_414f0b.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/warehouse-task-c-offline-acceptance.xml"
$env:RUN_RESTRICTED_DB_ACCEPTANCE='0'
& $python tools/task20_local_tests.py --offline tests/test_warehouse_restricted_login_gate_414f0b.py tests/test_warehouse_restricted_login_acceptance_414f0b.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/warehouse-task-c-restricted-gate.xml"
# PostgreSQL: existing loopback configuration; no URL/credential output.
$env:RUN_DB_TESTS='1'
$bootstrap="import os,runpy; from dotenv import dotenv_values; os.environ.setdefault('DATABASE_URL',dotenv_values(r'D:\Project\ai-data-workspace\.env')['DATABASE_URL']); runpy.run_path('tools/task20_local_tests.py',run_name='__main__')"
& $python -c $bootstrap tests/test_warehouse_query_workflow_api.py tests/test_warehouse_pg_runtime_role_inventory_414f0b.py tests/test_warehouse_sql_provider_policy_414e3.py tests/test_warehouse_attested_accounting_414f3.py tests/test_warehouse_cancel_takeover_recovery_414f4.py tests/test_warehouse_bigquery_readonly_full_path_414f5.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/warehouse-task-c-postgres.xml"
```

Only established UUID-schema fixtures perform SQL setup/cleanup. No production or
application schema, role, IAM, credentials, infrastructure or cloud API was changed.
The source-reviewed role matrix remains DRAFT. **Stop for security decisions; no
deployment, runtime activation, cleanup or next phase is authorized by this work.**
