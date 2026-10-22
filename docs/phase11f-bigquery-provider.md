# Phase 11F — bounded BigQuery provider

Implementation checkpoint: `5ea2e1a24ccebcab72933ac17d7bef57ef538406`.
Development implementation, 2026-10-22. No staging, commit, push or deployment.
Normal application configuration remains warehouse/provider disabled. No consumer
uses this adapter. Real BigQuery connectivity has been verified against the development
project, while live mutation acceptance remains deferred because the linked billing
account is intentionally inactive.

## 1. Scope and files

New provider modules in `app/warehouse/providers/`:

- `base.py`: independent opt-in policy, backend bindings, token states, safe errors.
- `types.py`: exact scalar conversions and conservative numeric bounds.
- `planning.py`: accepted evidence to existing SCD1/repair planners and expected result.
- `physical.py`: deterministic typed schemas and physical member reconstruction.
- `bigquery.py`: explicit ADC client, provisioning, load/jobs, transactional candidates,
  token lookup, independent reconciliation and finalized-input cleanup.
- `__init__.py`: inert package boundary.

`app/services/warehouse_provider_service.py` resolves owned approved metadata,
registered evidence, published candidate ancestry and 11E writer leases before
execution. It returns a successful 11E reconciliation ID eligible for a separate
existing publication call. No API route exposes physical identifiers or SQL.

New tests: `tests/bigquery_fake.py`, `tests/test_warehouse_bigquery.py`,
`tests/test_warehouse_provider_postgres.py`, `tests/test_warehouse_bigquery_live.py`.
Dependency changes: `pyproject.toml`, `uv.lock`. This document is the report.
No existing implementation, test, frontend, migration or consumer is modified.

## 2. Authentication, project and region

The SDK is installed as `google-cloud-bigquery` 3.46.2, constrained to `>=3.46,<4`.
The lock resolves supported auth dependencies; the existing Python 3.13 environment
is used. Client construction is explicit, after feature-policy and ownership checks.
It uses Application Default Credentials; no credential JSON is read, copied into the
repository, printed, persisted in PostgreSQL, or sent to AI/frontend code.

Both provider and registered resource locations must be **asia-south1**. Dataset,
table and job checks preserve that region and reject alternatives. There is no
US/EU fallback. Provider execution/provisioning is development-only in this phase.

Google Cloud CLI is installed and Application Default Credentials are configured
locally for development. The authorized development project is `datarise-511817`,
with BigQuery API access verified and execution restricted to `asia-south1`.

A bounded real BigQuery `SELECT 1` job succeeded through the Python BigQuery client
using ADC. The job executed in `asia-south1`, returned `1`, and reported zero bytes
processed and zero bytes billed.

The project is linked to billing account `018708-1F3001-ED02AE`, but that billing
account is currently closed/inactive (`open: false`) and project billing reports
`billingEnabled: false`. No credential JSON or long-lived service-account key is
used or stored in the repository.

Prerequisites for full live mutation acceptance:
1. Use the existing authorized development project `datarise-511817`. BigQuery API
   and ADC connectivity are already verified. Full DML/mutation acceptance additionally
   requires an active billing account.
2. Google Cloud CLI and local ADC are already configured. The ADC quota project is
   `datarise-511817`.
3. Grant the developer the narrow development provisioning/job/data permissions
   below on the controlled test resources. Do not provide credentials in chat.
4. Explicitly opt in using `BIGQUERY_ENABLED=true`,
   `BIGQUERY_LOCATION=asia-south1`, and `RUN_BIGQUERY_INTEGRATION=1`, then run the
   live test module. These flags are not written into the normal environment file.

ADC guidance: [Google authentication setup](https://docs.cloud.google.com/docs/authentication/provide-credentials-adc).
Production design remains AWS workload identity → Workload Identity Federation →
direct access or narrow service-account impersonation. No federation resources or
long-lived service-account-key workflow are implemented here.
[Federation guidance](https://docs.cloud.google.com/iam/docs/workload-identity-federation).

## 3. IAM and audit boundaries

Separate identities in production:

| Identity | Narrow permission boundary |
|---|---|
| Provisioner | Project-level `bigquery.datasets.create` where required; configuration/table creation on the approved workspace datasets. |
| Loader | Job creation (`roles/bigquery.jobUser`) on the execution project; table get/getData/create/updateData/delete on its owned staging dataset; get on its serving reservation. Dataset-scoped `roles/bigquery.dataEditor` is a starting predefined staging role; prefer a custom role for the exact operations. |
| Future query identity | Governed job creation and published serving reads only; no staging access or loader writes. Not implemented. |

No runtime recommendation uses project-wide BigQuery Admin or project-wide data
editing. The local developer identity successfully authenticated through ADC and ran
a bounded BigQuery query. The exact effective IAM role set has not been audited and
no production IAM conclusion is inferred from the developer account. IAM-negative
testing and least-privilege runtime acceptance remain deferred with full live mutation
testing. Public dataset access entries are rejected.

Generated SQL contains only validated backend identifiers and query parameters.
Labels contain bounded environment/component/model/generation/operation and hashes,
never email, filenames or business values. Raw exception messages and SQL are not
logged or returned by provider errors. BigQuery audit logs can still contain resource
and SQL metadata; restrict their operational access.
[IAM reference](https://docs.cloud.google.com/bigquery/docs/access-control).

## 4. Physical resource and visibility model

Backend metadata derives two datasets per development workspace: `dr_dev_w{workspace}`
(serving) and `dr_dev_stage_w{workspace}` (controlled staging). Model/version/generation
table families use `m{model}_v{version}_g{generation}`; operation/token scope also pins
the registered resource ID. Existing 11A physical references must exactly match the
derived project, serving dataset and family table before execution.

Explicit provisioning creates/verifies both datasets, a typed **empty serving
reservation table**, and a resource-generation journal in staging. A registered
11A resource remains metadata, not automatic proof of cloud readiness; the provider
verifies actual objects before writing. Provisioning is never a startup side effect.
Unexpected existing labels, schema, location, clustering, partitions or expiration
fail closed. No silent ALTER or schema autodetection is performed.

Each application has an immutable input table and candidate table, named by its
existing 11E application token within the exact family. Candidate tables contain
complete histories for the bounded affected member set, rather than rewriting a
shared current dimension. They are staging/private objects. The serving reservation
is never mutated by 11F, and no current view or analytics catalog is installed.

Publication eligibility is not physical serving promotion. PostgreSQL publication
selects a candidate revision; the explicit provider bridge reads only that published
ancestry, newest affected member first. Unpublished candidates cannot become prior
state. Lineage reads are bounded to 100 publications and 1,000 rows per candidate;
larger histories require separately designed compaction. A future reviewed 11G
serving/catalog adapter must resolve the exact published revision overlay. Reading
one candidate alone as a complete dimension would be incorrect.

Thus lost/late workers can at most create their immutable, private intent candidate;
they cannot overwrite shared serving rows. 11E fences and publication CAS remain
authoritative for public progress. No distributed PostgreSQL/BigQuery transaction
is claimed.

## 5. Semantic plans and schemas

Accepted manifests are checksum/identity verified. Model/version/configuration and
source dataset/version/policy/rule pins must match. Only registered manifests in the
owned PRIMARY mapping enter the bridge. Source acceptance is not re-decided in SQL.
One member cannot have multiple events within a manifest without explicit accepted
intra-manifest ordering; this ambiguous case fails closed.

The compiler calls the actual `plan_scd1`, `plan_repair` and `apply_plan` functions.
Source CORRECTION/BACKFILL authority is pinned, and corrections use the exact prior
state digest. Literal planner patches are validated by `apply_plan` and materialized
as the exact final affected-member candidate. BigQuery has no independent SCD or
late-repair MERGE algorithm. No arbitrary caller-assembled changed plan is accepted.

| Logical field | Physical representation |
|---|---|
| Member/version SK | Exact V1 SHA-256 STRING; SCD1 version SK stays NULL. |
| Business keys and declared attributes | Deterministic `c0`, `c1`, … columns, with a schema-hashed mapping to approved names. |
| SCD2 valid_from/to | DATE or absolute TIMESTAMP matching the approved effective-time domain; NULL end is open. |
| Current flag | BOOL, checked against half-open intervals by the existing invariants. |
| Observation/order/event evidence | Typed observed time, order domain/position, event UUID and event digest; not guessed business history. |
| Tracked hash | Existing 11C exact hash, verified from read-back attributes. |

Independent physical reconstruction checks deterministic member/version identities,
unique versions, stable keys, tracked hashes, typed attributes, no overlap, positive
half-open bounds and at most one open/current version. Reconciliation also checks
the redundant physical effective-time column against observation metadata.

SCD1 covers insert, forward update, unchanged retry and pinned correction. SCD2 covers
initial history, forward close/insert, unchanged observations, nonhistorical updates,
same-boundary correction and historical splitting. January/June keys remain unchanged
when an accepted March row splits January → March → June. Replay retains exact state
and keys while recording a distinct intent/token/provenance.

## 6. Numeric/time fidelity and unsupported model roles

| DataRise type | BigQuery type / admission |
|---|---|
| STRING | STRING, exact Unicode code points and NULL semantics. |
| BOOLEAN | BOOL, no integer/bool conflation. |
| INTEGER | INT64; reject outside signed 64-bit range. |
| DECIMAL | BIGNUMERIC physical column because V1 source schema does not declare precision/scale. |
| DATE | DATE, never conflated with datetime. |
| TIMESTAMP | Aware instant normalized to UTC; never DATETIME or naive wall time. |
| FLOAT | Unsupported for exact physical modeled roles; fail closed. |

Logical DataRise identities allow Decimal magnitudes/precision much wider than
BigQuery: `canonical_value` permits up to 4,096 Decimal coefficient digits and an
absolute exponent up to 4,096. NUMERIC is sufficient for at most 29 integer and nine fractional digits.
The conservative BIGNUMERIC envelope is at most 38 integer and 38 fractional digits,
76 total; the partially available 77th digit is deliberately excluded. Everything
outside this envelope fails before load, without float conversion or rounding.
Insignificant trailing zeros use the existing canonical scalar rules, independent
of ambient Decimal precision. Tests exercise maximum INT64, Decimal boundaries,
timezone-equivalent instants, Unicode and NULL.
[Type limits](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/data-types).

V1 FACT configuration models identity/grain and optional event time, but no measures
or dimension-role mappings. Physical FACT execution fails closed rather than invent
a useful fact table. An approved future model extension must define those roles and
bind physical dimension references to 11D's as-of results. No current-member join,
NULL-success reference or inferred fake SCD2 version is introduced here. Existing
11D inference/reference/rebinding tests remain authoritative and unchanged.

No inferred-member physical persistence path is needed by this dimension-only
adapter; provisional handles cannot be passed as authoritative member states.
Likewise, source DEACTIVATED events fail closed because the V1 physical dimension
contract has no active-state field. Add an approved explicit active-state mapping
before enabling such models. Partial SNAPSHOT absence never becomes invented history.

## 7. Journal, transaction, recovery and reconciliation

The provider journal stores the application token, immutable intent hash, plan hash,
scope hash and exact manifest/checksum vector. The existing 11E application token is
the logical effect identity; deterministic BigQuery load/apply job IDs are separate
submission identities. Job labels bind the intent/plan too. Token reuse with different
evidence is inconsistent, not a new application.

Provision permanent tables outside transactions. Load the bounded typed input using
an explicit schema, CREATE_NEVER, WRITE_EMPTY, zero bad rows and no ignored unknowns.
Independently read/validate staging. Then one BigQuery DML transaction asserts absent
token and empty candidate, inserts candidate rows, and inserts journal evidence.
Both become visible together. A controlled statement failure leaves neither mutation.
No permanent DDL is placed inside the transaction.
[Transactions](https://docs.cloud.google.com/bigquery/docs/transactions).

Lookup returns NOT_APPLIED only when journal and deterministic application job are
absent; running jobs are IN_PROGRESS. Matching journal is APPLIED. Conflicting,
duplicate or missing evidence after a terminal job is INCONSISTENT. Submission errors,
timeouts and lost responses are uncertain. Never blindly retry MERGE/script execution.
The recovery worker looks up the same token, reads the candidate, reconciles physical
state, and records a receipt under its new current 11E fence.

The bridge commits PLANNED before cloud writes. A provider-proven NOT_APPLIED receipt
is required before replanning prior uncertainty. Successful read-back creates the
existing typed Receipt, existing reconciliation and publication eligibility. Publication
is a separate existing 11E call with the same current fence/head/checkpoint CAS.

Physical reconciliation reconstructs native typed states and their existing state
digests; it does not trust loader row counts. It compares exact full affected histories
to the plan and produces the result checksum plus measured net inserted/updated
versions and interval-end changes. Source accepted-outcome counters retain their 11E
meaning and are derived from verified immutable manifests, distinct from net physical
version effects. A mismatch never produces a successful receipt/publication.

## 8. Cost, errors, telemetry, partitioning and retention

Central policy defaults: 1 GiB maximum estimated/billed bytes per query, 60 seconds,
1,000 physical candidate rows and 8 MiB encoded load. Values may be lowered; these
bounded development maxima cannot be silently raised. These are DataRise software
policies, not BigQuery pricing/free-tier promises.

Every read/reconciliation query is dry-run admitted with cache disabled and an
explicit billed-byte cap/location/standard SQL. Unknown, negative, LOWER_BOUND or
over-budget estimates fail closed. Before transactional DML, estimate every scanned
and DML statement separately and cap their combined estimate; script control-flow
dry runs alone are insufficient. LIMIT is never used as scan protection. Controlled
native staging tables have no externally managed masking/RLS policy; those are not
supported provider bindings. Read-back row count and encoded bytes are separately
bounded while consuming results, before accumulating the complete candidate.
[Cost admission](https://docs.cloud.google.com/bigquery/docs/best-practices-costs),
[script dry-run caveats](https://docs.cloud.google.com/bigquery/docs/multi-statement-queries).

Safe error codes cover authentication, permission, location, resource/schema drift,
rate/quota limits, job conflict, transient/permanent provider failure, inconsistent
target and cost limit. The adapter submits once with automatic API/job retries
disabled. Retry count is bounded at zero internally; a new explicit orchestration
attempt must recover via lookup. No unbounded retry loop or logical-conflict retry.

Telemetry captures job ID/type/location, operation/token/generation/model, times and
duration, processed/billed bytes and DML counts when returned, and retry count. The
bridge adds run/fence and physical reconciliation proof. These are returned to the
internal caller; a persistent general observability sink remains future work. Safe
provider errors carry only code and uncertainty, never arbitrary Google text.

Dimensions and the empty serving reservation are unpartitioned: this bounded member
history workload has no justified business-date partition pruning pattern. Candidate
tables have no clustering because each bounded candidate is read in full for proof;
the journal clusters by application token for its actual filtered lookup pattern.
There are no date-sharded tables. FACT event-date partitioning
and its pruning benchmark are not claimed because physical FACT roles are unavailable.

No auto-expiration can erase unresolved token/candidate evidence. `cleanup_input`
requires explicit finalized status, APPLIED lookup and successful reconciliation,
and deletes only the input table. Candidate and journal retention is indefinite until
an approved publication-aware compaction/retention policy exists. No age-only sweeper.
Live tests delete only freshly absent, randomly isolated labeled test datasets after
all assertions succeed; failures retain evidence and report retained resources.

Schema classification: all approved configuration changes require a reviewed model
version. Even additive physical changes are rejected during execution; create/provision
an explicit compatible version/generation. Key/type/role/region changes require new
approved bindings. There is no auto-detect/add/rename/retype operation.

## 9. Validation and final report

| Requested report item | Result |
|---|---|
| 1. Dirty tree | Baseline 240 unrelated entries and existing-file hashes captured; final comparison recorded below. |
| 2. Files | Seven new provider/service source files, four test/helper files, this document, two dependency files. |
| 3. Dependencies | google-cloud-bigquery 3.46.2 and resolved supported transitives; no GCS client, Beam, Spark or Storage Write API. |
| 4–5. Migration/development application | No new migration; nothing applied to public. |
| 6. Authentication | Local ADC configured and successfully used by the real Python BigQuery client. No credential JSON or long-lived service-account key used. Production WIF design retained. |
| 7–10. Project/region/resources/IAM | Real development project `datarise-511817` verified with BigQuery API and `asia-south1` execution. Billing account is linked but inactive (`open: false`, project `billingEnabled: false`). IAM breadth was not audited. |
| 11–12. Layout/isolation | Workspace serving reservation plus separate controlled staging, immutable private candidates. |
| 13–14. Schema/fidelity | Deterministic typed dimension schema; exact Decimal/INT64/Unicode/NULL/UTC/DATE admission and reconstruction. |
| 15–16. Partition/clustering | Unpartitioned/unclustered bounded candidates; journal token clustering; no physical FACT pruning claim. |
| 17–20. Token/jobs/visibility/transactions | Durable provider journal plus deterministic jobs; atomic private candidates; no consumer-visible promotion. |
| 21–23. SCD1/SCD2/late repair | Existing planners materialized and independently read back; forward/correction/retry/historical split tested locally. |
| 24–25. Inference/facts | No fabricated inferred versions; physical FACT and active-state roles fail closed pending approved model extension. |
| 26–27. Recovery/proof | Lost committed response recovered under a new 11E fence; physical corruption blocks publication. |
| 28–30. Errors/cost/telemetry | Safe codes, no blind retries, dry-run and billed-byte admission, bounded job/result telemetry. |
| 31. Drift | Region/schema/options/model/source/generation/workspace drift rejected. |
| 32–34. Live/bytes/cleanup | Real ADC connectivity passed using `SELECT 1` in `asia-south1`: result `1`, 0 bytes processed and 0 bytes billed. Live mutation suite was attempted but provisioning stopped before DML with `SCHEMA_MISMATCH` while the project remained billing-disabled. Full mutation/transaction/recovery acceptance is deferred. |
| 35. PostgreSQL | Fresh read-only 66-table baseline captured; final preservation result below. Disposable schemas only for metadata tests. |
| 36. Provider coverage | Manual cloud-free focused run: **67 passed, 7 skipped**, with real BigQuery integration disabled. |
| 37–38. Previous warehouse/backend | Full regression includes all prior 329 warehouse tests; final measured result below. |
| 39. Quality | Final diff/new-file whitespace/targeted-secret/hash/scope result below; no staging. |
| 40–41. Gold/consumers | Existing Gold, Metrics, Analytics, Ask, SQL and frontend unchanged. |
| 42. Limitations | Dimension-only, no active-state mapping, bounded candidate overlays, no live acceptance or production deployment. |
| 43. Ready for review | Local implementation and real BigQuery connectivity are ready for review. Full live mutation acceptance remains deferred until a billing-enabled development environment is intentionally available. |
| 44. Next phase | Do not claim full BigQuery mutation acceptance. Retain the deferred live gate and do not enable BigQuery consumers until the remaining provider acceptance is completed. No 11G work started. |

Test layers are separate: pure/SDK fake tests; real disposable PostgreSQL plus fake
cloud tests; three explicit opt-in real BigQuery cases. Default backend runs never
authenticate or incur cloud jobs. Live cases cover SCD1/SCD2 forward and correction,
historical splitting, committed-response loss/retry, and Decimal/Unicode/NULL/time
read-back. They emit safe resource/job/byte/cleanup evidence only when explicitly run.

No million-row cloud benchmark, TB-scale claim, IAM-negative success, live pruning
proof or actual BigQuery success is inferred from local fakes. No production rollout,
AWS worker, consumer migration, query-governance UI or external orchestration is added.

## 10. Final measured validation
| Focused cloud-free provider suites | **67 passed, 7 skipped**, from the manual provider-focused validation run. |
| Full backend | **446 passed, 6 skipped, 2 existing warnings**, 369.76 s. |
| Skip explanation | Three unchanged historical-fixture skips plus three explicitly opt-in live BigQuery cases. |
| Gate | Final result |
|---|---|
| Manual focused cloud-free provider run | **67 passed, 7 skipped**, with live BigQuery integration disabled. |
| Full backend | **446 passed, 6 skipped, 2 existing warnings**, 369.76 s. |
| Exact pass increase | 375 baseline + 67 provider/SDK cases + 4 PostgreSQL bridge cases = **446**. |
| Previous warehouse suites | **329 passed**: foundation 33, modeling 74, SCD 89, late-data 85, reliability 48. |
| Skip explanation | Three unchanged historical-fixture skips plus three explicitly opt-in live BigQuery cases. |
| Warning explanation | Existing Starlette/httpx and Authlib/httpx deprecations; no new dependency warning. |
| Development database | All **66 public table counts/fingerprints and catalog definitions unchanged** from the immediate 11F baseline; trusted source state, Gold pointers/freshness, ownership and warehouse metadata preserved. |
| Startup | With provider package installed, normal startup/health/OpenAPI succeeds while ADC lookup and cloud API requests are explicitly blocked. |
| Dependencies | Python **3.13.15**, BigQuery **3.46.2**, `uv lock --check` passes. |
| Frozen scope | Exactly **14 phase paths**; 13 source/test/dependency hashes frozen; documentation contains the measured report. |
| Quality | Targeted private-key/AWS/Google/token scan, new-file whitespace, `git diff --check`, status delta and all prior file hashes pass. |
| Unrelated work | All **240** original dirty entries and recorded file contents preserved; historical deleted tests remain deleted. |
| Git | HEAD and local origin/master remain `5ea2e1a24ccebcab72933ac17d7bef57ef538406`; index empty; no staging, commit or push. |
| Cloud | ADC, project `datarise-511817`, BigQuery API and `asia-south1` execution verified with a real bounded query. Full DML/SCD/transaction/recovery acceptance remains deferred because the linked billing account is inactive and project billing is disabled. |

The earlier exploratory full run was stopped before completion to incorporate the
read-back byte budget and remove unjustified candidate clustering. The results above
are from the corrected final frozen tree and supersede that incomplete run.

Local provider implementation and real BigQuery connectivity are ready for review
with the explicit model/scale limits above. This is a verified real-cloud connection,
but it is **not a claim of completed BigQuery mutation acceptance**.

Full SCD1/SCD2 DML, committed-response recovery, physical reconciliation and
transaction rollback remain deferred until a billing-enabled development environment
is intentionally available. No Phase 11G work or production deployment has started. No Phase 11G work
or production deployment has started. Final safe evidence is in
`tmp/phase11f-final-evidence.json` and `tmp/phase11f-quality.json`; these generated
records are not proposed implementation files.


Live BigQuery acceptance status

Project:
datarise-511817

Location:
asia-south1

Authentication:
Application Default Credentials

Connectivity:
Verified successfully using a bounded SELECT 1 job.

Observed:
- authentication succeeded
- project resolution succeeded
- asia-south1 execution succeeded
- query completed successfully
- 0 bytes processed
- 0 bytes billed

Mutation acceptance:
Deferred.

Reason:
The development project intentionally remains without an active billing
account to avoid the required INR 500 prepayment.

The live mutation suite was attempted while project billing was disabled.
Provisioning stopped before DML during dataset verification with
`SCHEMA_MISMATCH`. Because the linked billing account is inactive, full provider
mutation acceptance is intentionally deferred rather than weakening the provider
validation rules.

No production-readiness claim is made for those unexecuted live mutation
tests.

The provider remains disabled by default and normal tests remain cloud-free.