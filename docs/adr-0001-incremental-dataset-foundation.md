# ADR 0001: Incremental dataset application foundation

Status: Phase 6A foundation, Phase 6B APPEND, Phase 6C UPSERT and Phase 6D SNAPSHOT implemented. Cumulative Gold remains a future phase.

The sections below record the Phase 6A baseline and the approved direction. References to a "future engine" or "no execution" describe that baseline. Phase 6B executes APPEND through immutable delivery snapshots, validated candidate objects and the existing atomic publication boundary; see [Phase 6B implementation and validation](phase6b-append-execution.md). Phase 6C extends the same coordinator with UPSERT, update lineage and event ordering; see [Phase 6C implementation](phase6c-upsert-execution.md). Phase 6D adds explicit snapshot coverage, activity lineage and conservative effective-time ordering; see [Phase 6D implementation](phase6d-snapshot-execution.md). The Phase 6D request supersedes the baseline suggestion to block partial snapshots: they may update present keys but never deactivate missing ones.

## Problem and verified current behavior

Workspace is ownership/orchestration/analytics scope. Dataset is the processing boundary.
`upload_requests.upload_id` is a logical delivery; `physical_files.file_id` and its unique SHA-256 identify reusable immutable bytes. Durable upload completion is already idempotent.

Bronze and profiles are physical-file scoped (`bronze/file_id={id}/data.parquet`). The schema fingerprint sorts normalized names/types, ignoring column order/case. A dataset version is unique per dataset/fingerprint. Rule approval creates immutable snapshots; each dataset-version-file association pins its rule version.

The association is UNIQUE(dataset_version_id,file_id), not unique per delivery. Silver/quarantine/Gold paths are version/file/rule scoped:
`silver|quarantine|gold/...dataset_version_id=.../file_id=.../rule_version=.../`.
DQ runs link the association, rule version and successful attempt. Gold links its DQ run/attempt and catalog. Bronze ingestion/source metadata, DQ metadata and Gold attempt metadata are operational columns, not business content.

Two accepted uploads of identical bytes in the same dataset remain independent upload rows, but reuse the association and its processing outputs. The existing processing page projects this shared execution context. None of these artifacts is a cumulative dataset state. October records with changed September business keys get separate physical/Silver/Gold output; they do not update September records. UNIQUE business validation rejects duplicates when configured; it is not a merge engine. There is no `drop_duplicates()` merge semantics.

No existing table safely represents logical delivery application. Three new control-plane tables are therefore necessary; existing processing remains unchanged in 6A.

## Invariants

1. Raw bytes remain immutable and shareable. Deduplication of bytes never deduplicates logical arrivals.
2. Each application pins upload, dataset/schema, association, successful DQ output, approved rule version and immutable load policy.
3. Successful applications and published state metadata cannot be updated or deleted.
4. One ordinary application per logical upload; retries return its identity/results. A new physical-file reference is not a retry key.
5. State is immutable/versioned. Publication of its pointer and application success commits in one PostgreSQL transaction.
6. Explicit configured business keys define matching; no inferred keys, blind duplicate dropping or implicit last-write-wins.
7. Uncomputed metrics are NULL, never invented zeroes.
8. Archived deliveries cannot start preparation/publication. Historical application/state remain; archive is not retraction.
9. Schema/policy changes cannot silently reinterpret existing state/history.
10. Workspace/dataset/principal context is validated for every new API.

## Schema contract, rules and load policy

Schema inspection contract currently lives in browser storage. Approved business validation rules already have backend snapshots. Load semantics are a third concern, represented by `dataset_load_policies`: numbered per dataset, bound to an authoritative profiled dataset version/fingerprint, exact column names/types, ordered business keys, load strategy, schema evolution policy, optional event-time column, normalization version and conservative conflict/missing-record policies.

There is no automatic import/backfill or default APPEND for existing datasets. Frontend browser settings can be reviewed as suggestions, then explicitly confirmed/saved against a selected backend schema. Only the backend policy is authoritative for future incremental execution. Local contract changes still affect browser inspection, not backend policy or existing rule approval.

Keys must exist in the pinned profiled schema. UPSERT and V1 SNAPSHOT require at least one key; APPEND may have no key. Composite ordering is explicit. Key value NULL is rejected by identity normalization; the future engine must quarantine invalid key rows even if an unrelated DQ rule allows NULL. Event time must be a compatible date/time column. Reserved operational column prefixes are excluded/rejected as business schema.

Policy rows are immutable. Identical save retries reuse the current version. Changes require expected current version and explicit prospective confirmation. Existing applications retain earlier pins. After a trusted state exists, changing its policy is blocked until a future explicit migration/reprocessing design exists. Existing processing alone does not create trusted incremental state; changing the future load policy does not rewrite legacy processing history.

STRICT/ALLOW_ADDITIVE remains explicit and preserves browser inspection behavior. ALLOW_ADDITIVE does not authorize silent state-schema mixing. Application preparation requires exact pinned names/types/fingerprint, even where legacy fingerprint considers case-only changes equivalent. Breaking versions and additive state evolution both need an explicit migration/new boundary; no automatic migration is implemented.

## Row identity and outcomes

Version 1 uses SHA-256 of canonical UTF-8 JSON containing normalization version, hash purpose and named tagged scalar values. Key columns preserve configured order; content columns are sorted so input ordering does not change content identity. Only explicitly declared business columns participate; operational metadata never does.

NULL and float NaN normalize to a null tag; null keys fail. Strings preserve exact case/whitespace (cleaning belongs to the pinned Silver policy). Integers/floats/Decimals normalize to exact decimal text without ambient Decimal rounding; numeric 1/1.0 agree, negative zero agrees with zero, infinities/Decimal NaN fail. Boolean is distinct from number. Dates use ISO calendar values; timestamps require explicit timezone and normalize to UTC microseconds. Naive timestamps/unsupported types fail. A future engine adapter must convert Pandas/NumPy missing/scalar types to these explicit engine-independent values, without guessing timezones or losing integer/decimal precision. Hash collisions must be defended by comparing canonical keys/content before applying a conflicting match.

Future outcomes are INSERTED (new accepted state row), UPDATED (existing key, changed content), UNCHANGED (existing key, identical content), DUPLICATE (repeated identical event/content excluded by configured semantics), REJECTED (invalid/ambiguous incoming record), DEACTIVATED (active source key absent from validated full snapshot). An incoming row gets one final outcome; deactivation refers to prior state records. Count invariants must be validated by the future executor; 6A does not manufacture operation counts.

## Logical application and idempotency

`delivery_applications` UNIQUE(upload_request_id) is deliberately stronger than (upload,policy): clicking with a different policy cannot reapply a successful delivery. It pins dataset/schema/association, approved rule version, successful DQ run and policy ID (whose immutable snapshot includes mode/keys). Status PREPARED/RUNNING/SUCCESS/FAILED, ingestion/start/end times, safe failure code, nullable metrics and resulting state ID form the lifecycle.

`POST .../incremental/applications/prepare` only reserves a PREPARED record after approved successful replayable Silver; no worker, merge or state object is created. Legacy successful Silver may be shared by separate applications. Future 6B must materialize/pin immutable delivery input manifests under application/delivery identity before merge execution; current version/file/rule paths alone are not a sufficient per-delivery audit boundary. Explicit replay under another policy must become a separately authorized operation with its own identity; it is unavailable now.

## Current state and atomic publication

`dataset_state_versions` contains immutable candidate identity/context, previous state, numbered dataset state version, internal immutable manifest key/checksum, validated row count and STAGED/VALIDATED/PUBLISHED lifecycle. `datasets.current_state_id` points only to an owned published version. New datasets/existing datasets have NULL head until a real engine publishes; one CSV or legacy Gold row count is never substituted.

Future storage prefix follows current partition-label conventions:
`silver/dataset_id={dataset}/dataset_version_id={schema}/state/application_id={application}/candidate-{unique_token}/manifest.json`.
Manifest references immutable partition objects, checksums/schema/normalization/context and lineage. No object is written by 6A. Storage paths stay internal. Candidate objects must never overwrite current/historical objects.

The internal `publish_validated_state` boundary is not exposed by an API. Its future caller must write candidate objects, verify every referenced object/checksum/schema/count, then mark candidate VALIDATED. It holds dataset and upload lifecycle advisory locks through COMMIT, locks metadata, compares previous head and schema/policy, then atomically publishes state/head/application success. Synthetic DB fixtures validate rollback/CAS only, not S3 existence or row merges. Production invocation without the future object validator is forbidden.

Crash before commit leaves previous head authoritative. Crash after commit returns the same successful application. Failed application retries reuse pinned Silver/rules/policy, not upload/Bronze/Silver without cause. Recovery must distinguish invalid candidate, state conflict and application failure; failure codes exist, but no application executor/retry endpoint runs in 6A. A stale candidate must be superseded/recomputed against latest head rather than forced onto it. Multiple candidates may retain one application identity, each with a distinct immutable manifest key; publication selects its latest validated candidate and cannot regress the head. Immutable staged orphan cleanup needs a later retention design; no automatic deletion now.

Dataset advisory locks (same namespace as existing dataset processing), ownership, unique IDs, pinned-context foreign keys, row locks and head comparison protect concurrency. Existing per-file locks alone are insufficient. Future long-running workers must hold the dataset coordinator or use an equivalent durable lease/fencing protocol across calculation and publication; session locks require a live connection and are not a queue. Gold failure after publication retries Gold only and cannot regress state/application success.

## Proposed execution semantics for 6B–6D (not executed)

APPEND retains previous state and adds valid new records without updates. Same logical delivery retry is idempotent regardless of key. With explicit event/business key, same key+content is DUPLICATE; changed content under that key is REJECTED (no update in APPEND). Without a key, independently accepted identical bytes may legitimately append identical rows; physical dedup is not business-event dedup. Duplicate incoming keys: identical repeats can be duplicates; conflicting contents are all rejected, never first/last wins.

UPSERT: absent key INSERTED, same key/same normalized content UNCHANGED, same key/changed content UPDATED. The explicit Phase 6C request supersedes the earlier identical-repeat proposal: identical incoming repeats perform one semantic operation and count remaining inputs as DUPLICATE. Conflicting repeated keys are all REJECTED. Preserve previous state for rejected/stale rows. Without event ordering, latest applied delivery wins; with a configured typed event column, older input is STALE, equal-time changed content is REJECTED, and only newer changed content updates. No priority from row order. Current-state update is not SCD2; immutable delivery/application/state lineage remains.

SNAPSHOT applies keyed INSERTED/UPDATED/UNCHANGED, then missing previously active keys become DEACTIVATED via MARK_INACTIVE only for explicitly complete deliveries, never hard deletion. Reappearing inactive keys have the exclusive REACTIVATED outcome, including changed content; identical reappearance does not invent an update. Partial/unknown snapshots preserve missing records. Any Silver or incremental rejection withholds all absence deactivation. Empty input still requires an explicit complete declaration. Corrections/backfills preserve missing records. Explicit effective timestamps protect current state from older snapshots and equal-time conflicts; equal-time corrections are explicit exceptions. No business timestamp or completeness is inferred.

## Event time, backfill and partition direction

Ingestion time is persisted delivery receipt time; it is not business event order. Optional event-time column is explicitly configured; timezone and equal-time conflict semantics need future confirmation. No current timestamp-based last-write-wins. Late arrivals/backfills are explicit new logical delivery applications with the same lineage guarantees, never hidden data edits. Future executor determines affected keys/old+new partitions, validates configured business-time conflicts, then publishes state; do not assume upload order establishes newest business truth.

For meaningful event date, consider year/month partition manifests, rewriting only affected partitions and retaining immutable references to unchanged partitions. Without a meaningful date, prefer size/hash-key partitions; do not force calendar partitioning. No workload-size benchmark or safe maximum has been established. Existing V1 Pandas materializes entire Parquet artifacts in memory; hash utilities are engine-independent but the bulk scalar adapter/partition executor are future work. Expose manifest/apply/result contracts so Spark/Databricks/warehouse MERGE can replace Pandas execution without changing semantics. No Spark dependency is added.

## Gold impact (6E) and UX

Future published state identifies affected key/partition sets and downstream dataset Gold dependencies, with state/normalization/policy lineage. Rebuild only affected outputs when practical; maintain separate state publication and Gold freshness/retry. No workspace-wide rebuild or cross-dataset orchestration is introduced. Existing per-file Gold outputs are still per-file, not cumulative dataset Gold.

Contract UI explains the three modes, ordered keys, event time and explicit confirmation/version changes. Overview distinguishes backend policy, trusted current rows, latest applied delivery, prepared applications and last incremental change from legacy processing counts. Prepared applications excludes archived deliveries; it is not a count of all accepted pending uploads. Application metric component supports input/valid/inserted/updated/unchanged/duplicate/quarantined/deactivated/current rows, using em dash for NULL. No Process Incremental button or fake success/counts exists.

## Observability, security and migration

Public reads contain owned IDs/context, policy/version, timestamps, metrics and allowlisted failure codes; no manifest/S3 paths, credentials, raw row values or unrestricted AI access. Future executor logs workspace/dataset/upload/application/state/policy/attempt correlation IDs only; sensitive row/key values are not logged. New APIs reuse centralized development principal (temporary authentication remains unchanged).

Migration 0013 is additive, no old statuses/artifacts are changed or successful development data deleted. No default policy backfill. Foreign keys validate ownership and policy/rule/state lineage; insert trigger validates active logical delivery and successful matching DQ association; history triggers protect pins/success/published state. Unconfigured datasets require explicit setup before application preparation. Policies are separate from approved validation rules; no duplicated full backend contract editor.

## Validation and approval boundary

Architecture tests use isolated PostgreSQL schemas and real existing Silver/Gold processors with in-memory S3. They check logical reuse, explicit keys, deterministic typed identity, immutable policy/pins, unique application identity, archive/ownership/schema safety, locks and metadata-only atomic rollback/stale-head behavior. Frontend uses existing lightweight SSR/API harnesses.

Approve the conservative conflict/SNAPSHOT/normalization/schema-boundary semantics before 6B. Phase 6A readiness means persistence/configuration and safety foundations are reviewed and validated; it does not mean APPEND/UPSERT/SNAPSHOT execution or cumulative Gold is available.

Verification on 2026-10-06:
- `uv` unavailable; existing `.venv/Scripts/python.exe -m pytest -q` with `RUN_DB_TESTS=1`: 160 passed. Final foundation rerun after recovery/read-isolation refinements: 24 passed. Existing Starlette/httpx deprecation warning only.
- Frontend lint/build and all nine `tests/*.cjs` suites passed. The installed npm launcher points to a missing roaming npm CLI; commands used the existing `C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js` via Node, without installing dependencies.
- Migration 0013 applied to development PostgreSQL, including final candidate-recovery constraints on empty new metadata tables. Existing counts unchanged: 1 user, 7 workspaces, 9 datasets, 40 uploads, 24 physical files, 22 DQ runs, 15 Gold runs. No policies/state were inferred/backfilled.
- Owned API reads checked all six development-principal datasets; mismatched scope rejected. Policy save/preparation/publication tests used isolated schemas, not live business configuration.
- Live browser Contract and Overview checked desktop and 390px mobile viewport: explicit unconfigured backend policy despite local APPEND, no automatic strategy/schema selection, required UPSERT key, disabled unconfirmed save, no horizontal overflow, canceled unsaved draft. Temporary viewport reset. Evidence: `screenshots/phase6a/policy-mobile.png`, `screenshots/phase6a/overview-desktop.png`.
- Git whitespace checks passed; no commit and no Phase 6B execution.
