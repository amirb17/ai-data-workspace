# 414F0 - DRAFT warehouse PostgreSQL runtime-role matrix

**Status: DRAFT / NOT APPROVED / NO-GO for implementation or production release.**

Prepared on 2026-11-11 (Asia/Calcutta) from `feature/warehouse-model-ux` at
`a9fb738`. The owner confirmed no warehouse-specific PostgreSQL role matrix is
approved and limited this phase to review/documentation. R1-R8, T1, X1 and O1 are
**logical proposals, not PostgreSQL role names or provisioning instructions**.
No roles, grants/revokes, migrations, runtime source, tests, IAM or credentials change.

## 1. Verified architecture and authority

Task 20 is a separate processing security domain: `datarise_authority_owner`
(NOLOGIN), `datarise_api`, `datarise_worker`, `datarise_verifier`, `datarise_dispatch`.
`deployment/task21g_6_provision_runtime_roles.sql` validates restricted attributes
and prohibits inter-role membership. `deployment/task20_runtime_privileges.sql`
manages processing tables and controlled SECURITY DEFINER functions. **Do not
reuse, extend, rename or inherit these identities for warehouse work.** Task 20's
owner is not an approved warehouse owner.

The five `datarise_warehouse_*` names in
`tests/test_warehouse_pg_runtime_role_inventory_414f0b.py` are exploratory, as
`docs/warehouse_pg_runtime_role_inventory_414f0b.md` explicitly states. The B4
four-controller map is incomplete and `DRAFT_NOT_GRANTED`. Neither is an approved
role/grant policy.

Warehouse services currently use the general `app/db/database.py::DATABASE_URL`
and `repository_transaction`, not role-aware connection factories. Nesting is
rejected. Creating multiple roles alone would not isolate components. Credentials
co-resident in a compromised process have union authority despite NOINHERIT.
Process placement and connection routing require approval.

Ownership is the verified session/current `users.owner_key` matching workspace,
dataset membership and approved versions. Actor/created_by IDs are provenance;
a caller-built governance object is not authentication. D5/E3 check original
session and stored compilation. Public result-readiness independently rechecks
these and returns no rows or execution permission. Service roles do not establish
browser-tenant isolation: existing application checks remain essential; no RLS
or DB-session-to-browser-principal guarantee is claimed.

Analytical send/cancel boundaries are simulation/recovery foundations, not approved
live execution endpoints. Accounting **already exists** in
`warehouse_sql_attested_accounting_service.py`. Physical materialization/publication
uses `ProviderService`/`ReliabilityService`, distinct from analytical SELECT jobs.
Cleanup is inspection only and always denies physical deletion.

## 2. Proposed responsibilities - approval required

LOGIN below means a future dedicated nonprivileged service connection, not a login
per browser user. No provisioning is authorized. More than five responsibilities
are proposed because sender, independent attester and accountant carry different
trust, and the deny-only reader cannot run the current result metadata resolver.

| Logical proposal | Responsibility/source | LOGIN proposal | Separation and prohibited authority |
| --- | --- | --- | --- |
| R1 authenticated query-control API | Attempts/drafts, admission, pins, D5 checkpoint, E1 intent, E3 policy/envelope. D5 currently claims the reservation here. | Yes, dedicated control service if approved. | No provider credentials, attestation, usage settlement, publication CAS or cleanup. Claim placement is Q1. |
| R2 model-control API | Owned FACT/DIMENSION draft/version/configuration/source/relationship management and approval. | Yes if isolated from R1; co-location requires explicit approval. | No physical publisher, analytical evidence/ledger authority or immutable-history deletion. |
| R3 analytical dispatch/cancel controller | Original request/job identity, possible-send journal marker, cancellation intention/one-way boundary. Future IO separately gated. | Yes only for an approved controller. | No attestation, accounting, results, model/publication mutation or physical delete. |
| R4 independent recovery attester | CAS lease/revision, GET-only policy/metadata verification, journal observation plus attestation after ambiguous send/cancel. | Yes; separate from sender/accountant. | No send, cancel, results, settlement, model/publication mutation or hold release. |
| R5 accounting settler | Exact policy-bound terminal evidence to one idempotent usage record and reservation finalization. | Yes; separate trusted controller. | No provider credential, fabricated evidence, admission/dispatch/results or hold release. |
| R6 result authorization/schema gateway | Original-session settled-job/schema checks before/after result IO; readiness delegates here conceptually. | Yes if separated as metadata gateway; routing is not implemented. | No logical metadata writes/claims/accounting. UPDATE-requiring locks are an unresolved grant blocker. |
| R7 warehouse publisher/materializer | Accepted manifests, execution intents, writer fence/run, reconciliation, head/checkpoint CAS and serving receipt. | Yes; separate from query/API/reader/cleanup. | No analytical ledger/evidence mutation, Sydney approval issuance or protected-table deletion. Physical/metadata split is Q4. |
| R8 retention inspector | Per-resource serialization; inspect head, permanent query holds and retirement fence, return reasons. | Only if deployed as an operational service. | No fence INSERT, DELETE/TRUNCATE, hold release or cloud deletion. |
| T1 bounded result-data transport | One-job bounded first page and decoder, server scope plus pre/post gateway checks. | **No PostgreSQL login needed in proposed split.** | No protected metadata, arbitrary caller job IDs, submission or mutation authority. |
| X1 physical cleanup executor | Reserved future responsibility, not implemented/authorized. | No login/privileges now. | No deletion/release/fence authority without independently verified termination, retention and cloud fencing. |
| O1 warehouse migration/owner | Own reviewed objects and install migrations/guards. | NOLOGIN owner recommended; separate controlled migration login. | Runtime identities cannot own/inherit/SET ROLE to O1. No Task 20 owner reuse. |

These are proposed duty boundaries, **not a claim that eight production roles are
mandatory or deployable today**. Approve process boundaries and any justified
co-location before actual names, factories or positive grants.

## 3. Relations and per-role operations

S=SELECT on necessary columns; I=existing insertion; U=specified mutable columns;
D=the identified draft-source removal. Unlisted operations are proposed denied.
L=observed row-lock prerequisite, **not a grant or harmless UPDATE**. Functions,
triggers and schema privileges add transitive requirements.

| Group | Exact relations |
| --- | --- |
| A auth/ownership | `users`, `auth_sessions`, `workspaces`, `datasets` |
| M modeling | `warehouse_models`, `warehouse_model_versions`, `warehouse_model_sources`, `warehouse_model_configurations`, `warehouse_model_relationships` |
| C contracts | `dataset_versions`, `dataset_load_policies`, `approved_rule_policies` |
| P publication | `warehouse_resources`, `warehouse_publications`, `warehouse_publication_heads`, `warehouse_serving_materializations` |
| Q query identity | `warehouse_query_attempt_registry`, `warehouse_query_attempt_logical_drafts`, `warehouse_query_attempt_publication_pins`, `warehouse_query_attempt_reservation_links` |
| E recovery evidence | `warehouse_query_dispatch_checkpoints`, `warehouse_query_dispatch_revocations`, `warehouse_query_send_intents`, `warehouse_query_send_journal`, `warehouse_query_provider_policies`, `warehouse_query_recovery_attestations`, `warehouse_query_request_envelopes`, `warehouse_query_cancel_intents`, `warehouse_query_cancel_attempt_boundaries` |
| G accounting | `query_governance_reservations`, `query_governance_usage` |
| W materialization | `warehouse_operations`, `warehouse_runs`, `warehouse_accepted_manifests`, `warehouse_accepted_events`, `warehouse_outbox`, `warehouse_execution_intents`, `warehouse_writer_leases`, `warehouse_run_journal`, `warehouse_reconciliations`, `warehouse_checkpoints` |
| H retention | `warehouse_query_publication_retirement_fences` |

### R1 - query control: observed requirements, not a raw-SQL grant specification

- S: A/M/C/P/Q/G; E checkpoint, revocations, intent, journal, policy, envelope;
  H for publication dependency inspection.
- I: four Q tables; `query_governance_reservations`;
  `warehouse_query_dispatch_checkpoints`, `warehouse_query_send_intents`, initial
  journal CREATED, `warehouse_query_provider_policies`, request envelopes.
- U: reservations `(status, execution_started_at)` for D5 claim;
  `(status, finalized_at)` only if the existing ACTIVE-release path is assigned
  here. These are not general finalization rights.
- Conditional trigger I: revocation notices if this role also has approved auth/
  modeling writer responsibilities. Do not grant direct authority automatically.
- L: users/sessions/workspaces/datasets, model/version/source, resource/head/receipt,
  Q rows, reservation and intent through current helpers.
- Sequences: attempt `attempt_id`, reservation `reservation_id` only.
- No usage/attestation INSERT. Admission/claim/release share columns but are
  different commands; Q1 must settle claim placement.
- Evidence: attempt/draft repositories; governance reserve/claim/release; D5;
  E1 preparation; E3 policy/envelope pinning.

### R2 - model control

- S: A/M/C and owned resource metadata required by current checks.
- I: models, versions, sources, configurations and relationships in M.
- U: models `(status, archived_at)`; versions `(status, reviewed_by, reviewed_at)`
  for approval/archive. No arbitrary configuration or identity updates.
- D: `warehouse_model_sources` **only for DRAFT removal**, through current service
  and guard. No broad history/model DELETE.
- Trigger I: dispatch revocations for relevant model/version changes. Its
  common SQL also needs S on query checkpoints/logical drafts/pins and model
  sources, even when a different OR branch would not match at runtime.
- L: workspace/model/version/dataset through owned-scope/source checks.
- Sequences: model, version, mapping, relationship IDs.
- Resource registration is backend-only, not a browser route. R2/R7/registrar
  ownership is unresolved; no resource INSERT or Sydney approval sequence is
  proposed for R2 by default.

### R3 - dispatch/cancellation

- S: A/M/C/P/Q/G/H and E original scope/policy/envelope/revocation/journal.
- I: journal lease/SEND_BOUNDARY; cancel intents and cancel-attempt boundaries.
- U/D: no data mutation/deletion proposed; L: attempt/reservation/intent and
  live session/source/publication context used to prepare possible send.
- No SERIAL sequence; revisions/generations are CAS-derived.
- Envelope must already be pinned. No new token/request on uncertain retry.
- Shared journal INSERT does **not** distinguish sender and observer event types
  by DB role. Reviewed command-level restrictions are needed; a table grant alone
  cannot be represented as enforcing this split.

### R4 - recovery attestation

- S: Q, reservations; E checkpoint/revocation/intent/journal/policy/envelope/cancel
  markers; original pin/serving receipt. Usage may be inspected, never modified.
- I: journal lease/OBSERVATION plus recovery attestations only.
- U/D: none as data operations. L: attempt/reservation/intent via existing helpers.
  No SERIAL sequence; no auth writes.
- Historical lookup can continue after revocation as trusted bookkeeping, not
  customer result access or authorization. Original policy/job/lease/pins hold.
- Evidence: E1/E2/E3 snapshot and attestation, pinned submission and cancel recovery.

### R5 - accounting

- S: Q registry/pins/links; E checkpoint/revocations/intent/journal/attestation/
  policy/envelope; G; `workspaces` for repository owner checks.
- I: usage only after exact terminal evidence, once per reservation.
- U: reservations `(status, finalized_at)` through complete/fail. No budget,
  identity, expiry, original-token or compiled-intent changes.
- L: attempt/reservation/intent. Sequence: usage `usage_id` only.
- Guards enforce legal state/usage consistency, not a role-specific settlement
  API. The same column privileges can reach ACTIVE release; don't describe raw
  status UPDATE as accounting-only authority.
- Existing current-owner checks may block settlement after ownership transfer,
  although historical observation can continue. Decide that responsibility rather
  than bypassing `_require_owner` (Q5).

### R6 - result gateway, not the deny-only transport

- S: users/sessions/workspaces; Q; G; E checkpoint/revocation/intent/journal/
  attestation/policy/envelope; M/resources for server result-schema derivation.
  No blanket SELECT on every source artifact or auth secret is proposed.
- I/U/D: none as logical data operations. L: auth/session/workspace and original
  attempt/reservation/intent through reused helpers. No sequences.
- Check owner/original session/revocation before and after external IO, settled
  successful job, exact policy-bound evidence/ledger/envelope and approved schema.
- Current `SELECT *` auth helpers read more than a minimal projection; narrowed
  projections/command boundaries need later review.
- **Blocker:** deny-all protected-table acceptance cannot run this service.
  Do not silently broaden reader grants to solve it. Approve gateway vs T1 split,
  or explicitly choose and test another model before migrations.

### R7 - warehouse publication/materialization

- S: A/M/C/P/W; `dataset_state_versions`, `delivery_applications`,
  `delivery_manifests` for immutable source publication/lineage. Disabled Sydney
  paths additionally depend on approval and Gold job/input/run/receipt/intent
  evidence (0045-0047 and `sydney_gold_eligibility_service`). That profile's complete
  closure is still a separate review; no Sydney grants are proposed here.
- I: W manifests/events/outbox, operations, execution intents, runs, writer leases,
  journal, reconciliations, checkpoints; P publications/heads/serving receipts.
- U: runs `(status, started_at, completed_at, failure_code)`; writer leases
  `(operation_id, warehouse_run_id, fence, worker, expires_at)`; heads
  `(publication_id, revision)`; checkpoints `(position, version, publication_id)`.
- Resource registration/generation and operation creation ownership require Q4;
  do not grant all resource UPDATE or Sydney approval INSERT.
- Trigger I: dispatch revocations on relevant publication-head advances. Guards
  read source, reconciliation/run and query dependency evidence transitively.
  Also S: query checkpoints/logical drafts/pins and model sources required by
  the common revocation-notice SQL, not just the publication tables.
- L: model/source/resource/operation/run/lease/head/checkpoint per existing code.
  No accepted-source/publication/receipt/history DELETE.
- Sequences: operation, run, journal, reconciliation, publication, serving IDs;
  resource ID only if registrar responsibility is approved.
- Current ProviderService combines target mutation and PostgreSQL publication.
  Separate physical-writer and metadata-publisher credentials/proof handoff need
  review; one shared role is not independent provider verification (Q4).

### R8 / T1 / X1

R8 S: publications/resources/heads/H plus query pins/links/reservations/checkpoints
read by the dependency helper. L: publication/resource FOR SHARE. Explicit helper
EXECUTE is below. No logical I/U/D or sequences. Even retired metadata with no
current head returns `physical_deletion_authorized=False`. T1 needs no PostgreSQL
login or permissions in the proposed split. X1 gets **none**: there is no approved
hold-release/physical-deletion implementation, so no DELETE, fence INSERT, TRUNCATE,
ownership or cloud deletion authority can be derived from existing inspection.

## 4. Sequences, functions, triggers and schemas

Resolve actual owned sequence identity with `pg_get_serial_sequence`; don't guess
names or use a wildcard. ID writers would need USAGE only, not UPDATE/setval;
SELECT only if a separately reviewed callsite needs it:

| Owning table.column | Proposed writer |
| --- | --- |
| `warehouse_query_attempt_registry.attempt_id` | R1 |
| `query_governance_reservations.reservation_id` | R1 |
| `query_governance_usage.usage_id` | R5 |
| `warehouse_models.warehouse_model_id`, `warehouse_model_versions.warehouse_model_version_id`, `warehouse_model_sources.source_mapping_id`, `warehouse_model_relationships.relationship_id` | R2 |
| `warehouse_operations.operation_id`, `warehouse_runs.warehouse_run_id`, `warehouse_run_journal.journal_id`, `warehouse_reconciliations.reconciliation_id`, `warehouse_publications.publication_id`, `warehouse_serving_materializations.serving_materialization_id` | R7, subject to operation-creation approval |
| `warehouse_resources.resource_id` | Unresolved registrar, not automatically R2/R7 |
| `warehouse_sydney_approvals.approval_id` | Approval issuer/migration procedure only; no runtime assignment |

Query pins/drafts/links/checkpoints/intent/journal/attestation/envelope/cancel tables
have no independent SERIAL allocation. Evidence JSON lists exact source declarations;
actual schema-qualified sequence OIDs/owners need catalog acceptance later.

All runtime proposals: database CONNECT and necessary trusted-schema USAGE only.
No schema CREATE, ownership, TRIGGER, REFERENCES-for-new-FKs, ALTER, DROP, TRUNCATE,
role administration, superuser, replication or BYPASSRLS. No cross-role membership
or SET ROLE path, including intermediate/predefined roles and membership options.
NOINHERIT alone does not prevent SET ROLE. Database TEMP and PUBLIC/schema defaults
need database-wide review; revoking a direct role grant cannot remove inherited/
PUBLIC access. Do not change global defaults as a side effect of this draft.

**Lock blocker:** FOR UPDATE/FOR SHARE requires UPDATE privilege on at least one
column of each locked table. That is actual UPDATE authority, not a harmless lock
permission. Immutable guards reject data mutation on some tables; mutable auth,
workspace/resource/head tables need separate analysis. Do not choose an arbitrary
identity column as a supposedly safe workaround. Approve a constrained command
boundary, deliberate safe column design or reviewed lock/read composition first
(Q2), preserving authorization, transaction and advisory-lock semantics.

Direct helper requirement: R8 calls
`warehouse_query_publication_dependency_present(bigint,bigint)`; query pin/link/
retirement consumers also reach it through guards/inspection. SECURITY INVOKER
means underlying SELECT rights are needed as well as EXECUTE. Current code uses
hash, clock and advisory-lock builtins; this isn't justification for blanket
EXECUTE on a schema. Advisory locks serialize cooperating code, not malicious SQL.

Guard families needing complete transitive review (declarations/lines in JSON):

- 0029/0030/0048 relationships: model/version/source/resource/run history, source
  scope, modeling and relationship insertion: R2/R7 and related row-lock readers.
- 0031/0034: reliability insert/evidence/mutation/immutability/progress and serving
  guards: R7, source readers and reconciliation/run dependencies.
- 0032: reservation identity/state, usage, no-delete/no-truncate and deferred
  completion consistency: R1/R5 plus reservation-locking services.
- 0048 query registry, 0049 pins, 0050 links, 0051 retirement/link checks and 0052
  draft guard: R1, append and dependency consumers.
- 0053: auth/session/workspace/dataset/model/version/resource/head changes append
  dispatch revocation notices. Existing non-warehouse writers need a reviewed
  way to append notices; do not break logout/account disable/publication commits
  through missing trigger dependencies. All referenced SQL relations need
  permission even if an OR branch produces no rows: the common notice reads
  query checkpoints, logical drafts, query pins and model sources, and inserts
  dispatch revocations. Apply this closure to every triggering writer.
- 0054: initial event, lease/journal/intent guards: R1/R3/R4.
- 0055/0056/0058/0059/0060: attestation/policy/envelope/cancel guards: R1/R3/R4.
  Preserve exact revision/token and immutable request/receipt identity.

Migration-time trigger installation needs TRIGGER/EXECUTE privileges. Firing a
trigger isn't a reason to grant every runtime direct EXECUTE on every trigger
routine. INVOKER bodies use invoking-role underlying rights; helper calls need
reviewed EXECUTE. Do not convert guards to DEFINER merely to make tests pass.

0057 hardens 14 selected query functions: **not all warehouse/governance routines**.
Its list excludes the 0052 logical-draft guard. 0058-0060 have their own PUBLIC
revokes/trusted paths. Older source definitions can leave default PUBLIC function
EXECUTE or unset paths; this is a source-visible audit item, **not proof of live
ACL exploitability**. Inspect actual owners, signatures, memberships, column ACLs,
defaults and effective privileges. Table revocation doesn't remove column grants;
PUBLIC revocation doesn't remove named/inherited access. Missing objects fail
acceptance; they are not evidence of denied privileges.

0046's `warehouse_sydney_resource_registration_guard` is an existing DEFINER
with a pinned trusted path and restricted approval lookup. It is outside 0057's
INVOKER-only set. Review actual owner/body/dependencies/ACL/path and no ownership
inheritance separately, without granting approval issuance to the publisher.
Processing/Batch DEFINER routines in 0043/0044 stay in their separate domain.
No new DEFINER design or ownership change is approved here.

## 5. Comparison and security risks

| Earlier artifact | Actual standing | Proposed difference |
| --- | --- | --- |
| `datarise_warehouse_api` candidate | Name only | R1 query control, R2 model control and R6 result gateway have different access; approve process/credential boundaries before names. |
| `datarise_warehouse_query_worker` candidate | Name only | R3 sender, R4 observer and R5 accountant should not automatically share submission, evidence creation and ledger authority. |
| `datarise_warehouse_result_reader` candidate | Protected-table deny-only tests | T1 may be PG-free; current result resolver needs R6 metadata/locks. Gate is not positive gateway acceptance. |
| `datarise_warehouse_publisher` candidate | Name only | R7 needs source/reliability/serving closure, not analytical dispatch. Registrar and physical/metadata split unresolved. |
| `datarise_warehouse_cleanup` candidate | Name only | R8 inspection exists; X1 release/deletion does not. Expiry/settlement never implies delete authority. |
| B4 authenticated_checkpoint/provider_policy_prepare | Nine-control SELECT/INSERT subset | R1 additionally has admission/claim UPDATE, sequences, auth/source/model reads, locks and triggered writes. |
| B4 recovery_observer | Logical SELECT/INSERT subset | R4 needs full identity/policy/receipt/envelope/lock closure and event-specific authorization. |
| B4 retention_inspector | Three logical read tables | Actual helper needs pins/links/reservation/checkpoint plus publication/resource/head and FOR SHARE; still no deletion. |

P0 source-backed findings:

1. No approved matrix or role-aware connection routing. Candidate/test names are
   not architecture authority; don't provision them yet.
2. Raw reservation status/finalized_at UPDATE and shared journal INSERT cannot
   enforce all proposed commands by role. Legal state transitions alone are not
   role authorization. Decide constrained commands vs explicitly broader trusted
   process boundaries before positive grants.
3. Current result service needs UPDATE-requiring locks; deny-only reader evidence
   cannot establish it runs. Approve gateway/transport separation or a different
   tested policy, not a quiet reader grant expansion.
4. Revocation trigger dependencies cross auth, modeling and publication. Too-narrow
   rights can break revocation; too-broad ones can permit fabricated evidence or
   mutable authority changes.
5. Object ownership, superuser, membership and dangerous executable functions can
   defeat role separation. Triggers aren't RLS or proof of a browser/provider
   principal; schema owners remain privileged.
6. Permanent 0053 holds survive settlement/revocation/crash. No independently
   justified release/cloud deletion protocol or X1 authority exists.

## 6. Decisions required before implementation

- Q1: Keep authenticated D5's ACTIVE -> EXECUTING claim in R1, or introduce an
  approved handoff? Moving it changes the transaction/concurrency boundary; don't
  relocate code merely to fit a desired role matrix.
- Q2: Which safe command/lock interfaces constrain UPDATE on mutable auth/control
  tables? Who owns any routine and authenticates principal/workspace provenance
  without trusting caller SQL or constructed permission objects?
- Q3: Approve R6/T1 split and original-session continuity. Which process has DB
  metadata, cloud destination data access, or both? Combined simulated code is not
  a credential-isolated runtime.
- Q4: Who creates resources, operations and execution intents: R2, R7 or a registrar?
  Are physical writer and metadata publisher separated? Which disabled Sydney/Gold
  dependencies are included in the first positive grant profile?
- Q5: Who reconciles/settles after ownership transfer, contradictory terminality
  or permanently absent provider jobs? Do not suppress current-owner checks.
- Q6: Choose actual names, LOGIN/TLS/secret custody, per-process factories, connection
  budgets, owners and explicitly forbidden membership/SET ROLE paths.
- Q7: Approve independently verified retirement-fence/hold-release and physical
  table ACL/concurrency protocol before proposing cleanup execution privileges.
- Q8: How can current auth/ingestion/processing identities trigger warehouse
  revocations without receiving unrelated warehouse authority? Joint review is
  needed; Task 20 roles stay unchanged now.

## 7. Approval and later acceptance checklist

All unchecked. **Design approval is not deployment authorization.**

- [ ] Product/security owners approve responsibilities and Q1-Q8 decisions.
- [ ] DB/security owners approve exact per-table/column/sequence/function matrix,
  prohibited operations and each trigger's transitive dependency closure.
- [ ] Credential/process placement enforces the agreed boundaries; a shared general
  DSN or co-resident credentials aren't claimed as independent identities.
- [ ] Migration preflight validates actual schema, owners, overloads, signatures,
  owned sequence OIDs and both 0048 files. Static SQL matches are not executable
  grant specifications; discovery is ordered SQL, not assumed Alembic/role setup.
- [ ] Repeatability validates/converges exact approved rights, refuses unsafe
  existing roles/ownership, and fails transactionally for missing/ambiguous objects.
  Reapplication and partial-failure rollback are proved in disposable schemas.
- [ ] Rollback preserves immutable evidence/holds and Task 20 ACLs, never restoring
  PUBLIC authority or deleting audit history to undo security changes.
- [ ] Positive/negative tests exercise actual controller calls, FOR SHARE/UPDATE,
  sequences, helper functions, deferred guards, revocation writers, CAS/concurrency,
  missing configuration and cross-role/tenant attempts.
- [ ] Real independent restricted logins prove session_user=current_user, TLS,
  safe flags including replication, no forbidden ownership/membership/column ACLs,
  schema/function access, and intended positive behavior. Neither developer login
  nor SET ROLE simulation is evidence of direct restricted-login acceptance.
- [ ] Opt-in tests require separate profile credentials with no fallback. Missing
  credentials remain explicit skips. The current two reader skips remain an
  external acceptance gap, not security PASS.
- [ ] Independent BigQuery IAM/source-retention acceptance occurs only under a
  separate approved scope. Offline inventories don't establish live permissions.
- [ ] Release authority approves rollout after evidence review. Query execution,
  cleanup and public result fetching stay disabled under their separate gates.

## 8. Evidence, validation and scope

`warehouse_runtime_role_matrix_414f0_draft_evidence.json` lists exact reviewed/
scanned source, migration, deployment, test and documentation files, review method,
SHA-256, definitions/sequences and bounded literal SQL operation candidates with
line references. Static matches can contain aliases/CTEs and miss dynamic or
transitive calls. They support navigation, **not dependency-closure approval**.

The earlier 20 inventory / 10 gate / 28 IAM / 36 frontend passes and two restricted
login skips are user-provided context, not rerun results. No pytest, frontend,
role, database or cloud acceptance is claimed in this documentation-only phase.
Checks validate references, JSON, hashes, whitespace and scoped secret patterns.
No runtime or security test was changed or weakened.

Only this DRAFT and its evidence JSON are selectively committed as requested.
No role, grant/revoke migration, Task 20 identity, API/frontend/query execution,
cloud resource, credential or previous design file is changed. **STOP for matrix
approval before implementing any warehouse security migrations.**

## 9. Additional source-confirmed acceptance gaps

The existing restricted-login test correctly requires a separate login and no
fallback, but its attribute query omits `rolreplication`; it doesn't establish
membership/ownership, function/sequence or column-level ACL safety. Its table
`has_table_privilege` checks and EXPLAIN DELETE denial do not substitute for
column SELECT/INSERT/UPDATE/REFERENCES checks. No existing test is changed in this
design phase. Extend approved per-profile acceptance later rather than reporting
the current reader gate as comprehensive least-privilege proof.

Governance tables are shared by SQL/ANALYTICS/ASK/METRIC and DUCKDB/BIGQUERY
consumers (`query_governance_repository.py`), not warehouse-only rows. A table or
column grant cannot constrain a role to original warehouse SQL/BIGQUERY attempts.
Current trusted warehouse helpers enforce that binding, but raw SQL authority
would bypass the Python checks. The future command/RLS/trusted-service decision
must preserve unrelated consumers and tenant ownership; no new such mechanism is
selected here.

The HTTP authentication dependency adds another real operation: `app/api/identity.py`
calls `app/auth/service.py::session`, which SELECTs users/sessions and conditionally
UPDATEs `auth_sessions.last_seen_at`. R1/R2/R6's warehouse method-level S/L profiles
do not include that data update. If authentication remains on the same DB login,
its effective profile also needs this separately approved auth operation; R6 would
not then be a strictly data-read-only login. Prefer an explicit auth-domain
connection boundary rather than silently expanding warehouse rights. Session
issuance/logout/password reset/security-event writes are auth responsibilities,
not automatic grants to query workers or readers. Resolve this with Q6/Q8.

Legacy 413F recovery remains a separate call chain:
`warehouse_sql_recovery_settlement_service` wraps a provider and calls
`governed_query_service.recover_governed_query`; the older BigQueryReadProvider
can fetch rows on successful recovery while the facade settles usage. Do not
route the proposed GET-only R4 through that combined legacy path or widen its
ledger/result permissions to make it work. R4/R5 proposals refer to the newer
policy-bound attestation and attested-accounting services. Any supported legacy
consumer needs its own explicit profile review, not silent API substitution.

Column minimization is also an implementation blocker, not an already-proven
entitlement. Current auth helpers SELECT user/session records with `*` (or `u.*`),
so they require more SELECT columns than the desired narrow warehouse projection
and can expose password/session-related metadata to that DB identity. S in this
draft identifies required relations/logical data, not permission to grant every
column. Review explicit projections or constrained commands before translating
profiles into column grants. This affects R1/R3/R6 through shared auth helpers,
not just the result gateway. No such refactoring or auth privilege change is made.
