# Phase 6D: SNAPSHOT execution

Dataset remains the processing boundary. This phase extends `apply_incremental` and `IncrementalArtifacts`; no second coordinator, worker, upload system, workspace orchestration or cumulative Gold execution is introduced.

## Changed-file inventory

Backend and migration:
- app/api/incremental.py
- app/db/incremental_repository.py
- app/processing/upsert_engine.py
- app/processing/snapshot_engine.py (new)
- app/schemas/incremental.py
- app/services/append_application_service.py
- app/services/incremental_application_service.py
- app/services/incremental_policy_service.py
- app/services/snapshot_context_service.py (new)
- app/storage/incremental_artifacts.py
- migrations/0016_snapshot_execution.sql (new)

Frontend:
- frontend/src/features/datasets/contracts/components/DatasetContractForm.tsx
- frontend/src/features/datasets/contracts/components/IncrementalPolicy.tsx
- frontend/src/features/datasets/contracts/validation.ts
- frontend/src/features/ingestion/components/ApplicationMetrics.tsx
- frontend/src/features/ingestion/components/DeliveryRuleBridge.tsx
- frontend/src/features/ingestion/components/IngestionBatchCard.tsx
- frontend/src/features/ingestion/components/ProcessingDetails.tsx
- frontend/src/features/ingestion/components/ProcessingResult.tsx
- frontend/src/features/ingestion/components/SnapshotDelivery.tsx (new)
- frontend/src/pages/Dataset/Files/DatasetFilesPage.tsx
- frontend/src/services/api/incremental.ts
- frontend/src/services/api/rules.ts

Tests and evidence:
- tests/test_incremental_foundation.py
- tests/test_snapshot_execution.py (new)
- frontend/tests/contracts.cjs
- frontend/tests/snapshot.cjs (new)
- docs/adr-0001-incremental-dataset-foundation.md
- docs/phase6d-snapshot-execution.md (new)
- docs/phase6d-live-results.json (new)
- tools/phase6d_live_check.py (new; pre-existing tools preserved)
- screenshots/phase6d/snapshot-overview-desktop.png (new)

## Policy and delivery declarations

Migration `0016_snapshot_execution.sql` adds immutable policy coverage, immutable `snapshot_delivery_contexts`, pinned application coverage/time/kind, and nullable snapshot metrics. It was applied to development `ai_data_workspace` only after explicit human approval on 2026-10-07. Earlier migrations are unchanged. Legacy design-only SNAPSHOT policies receive conservative PARTIAL coverage, never inferred COMPLETE. No applications or trusted states are backfilled.

SNAPSHOT policy requires authoritative schema, ordered explicit keys and explicitly selected COMPLETE/PARTIAL coverage. COMPLETE authorizes a delivery to declare complete or downgrade to partial; PARTIAL cannot authorize complete. Every delivery requires `POST .../incremental/deliveries/{upload_id}/snapshot-context`, with `coverage`, explicit zoned `effective_at` or null, and NORMAL/CORRECTION/BACKFILL kind. Identical retries return the saved declaration. Changed declarations are rejected; application preparation pins it, and database triggers protect both. Archived or foreign-context deliveries are rejected. Business-time event policies require an explicit effective timestamp. Corrections/backfills also require business time.

Coverage is never guessed from filename, counts, source names or dates. Policy changes after publication remain blocked. Declaration fields cannot change the dataset's business keys/schema or approved rule version.

## Engine and activity

The pure adapter reuses UPSERT's typed identity, canonical collision comparisons, grouping and event ordering. New keys insert; existing active keys with identical content are unchanged; changed active records update. Identical incoming repeats perform one semantic operation plus duplicates. Conflicting repeated keys all reject. No first/last row rule exists.

Historical keys remain in every candidate. Operational `_datarise_` fields preserve activity, deactivation/reactivation upload/application/policy/rule/timestamp, original insertion and latest content update. These are excluded from business schema/hashes. Reactivation is an exclusive outcome: changed content is applied, but counted as REACTIVATED rather than double-counting UPDATED. Identical reactivation preserves the previous content-update timestamp. Earlier immutable state artifacts retain every earlier lifecycle event; the current row stores the latest deactivation and reactivation.

Absence deactivation is allowed only for COMPLETE NORMAL deliveries at a safe new boundary. PARTIAL, corrections, backfills, stale/equal-time deliveries, and any Silver/incremental rejected row withhold deactivation. Ambiguous keys cannot disappear because rejected rows were filtered out. Explicit complete empty input can deactivate all active records; current upload inspection still rejects header-only CSVs, so no empty-snapshot browser control is added.

## Time, corrections and backfills

Snapshot effective time always comes from explicit delivery metadata, never arrival time. A configured event column also applies UPSERT's per-key ordering and must not exceed the declared snapshot effective time. Older snapshot boundaries cannot insert/update/reactivate/deactivate current records, even previously absent keys. A stale delivery retains a new immutable audit result and the previous trusted boundary. Equal-time same content is unchanged; changed/new/reactivated states conflict unless explicitly declared CORRECTION. Corrections at the current period may update equal-event-time values, but never rewind older periods or deactivate unrelated records. Backfills obey ordering and never deactivate by absence.

The greatest accepted declared effective time is carried in the published application, including partial snapshots. Dataset locks and compare-and-swap publication prevent a stale candidate from overwriting a newer head. Without business time, application order is the explicit V1 semantics; business-time late-arrival protection cannot be claimed. Mixing timed and untimed state requires migration and is rejected. No historical-period rewriting, snapshot timestamp inference, watermark, replay manager or scheduler is implemented.

## Validation and publication

Before publication: verify manifest/object/outcome checksums, readability, exact effective business schema, unique normalized keys, policy/context pins, ledger coverage/ownership, active/inactive reconciliation, lifecycle lineage, no lost historical keys, authorized missing-key deactivation, original insertion preservation and non-regressing effective times. Validate transition again on the deserialized artifact. Publication reuses dataset/upload locks, immutable candidates and the existing PostgreSQL transaction for state/head/application success and analytics STALE. Pre-commit failure preserves the previous head; response loss returns durable success on retry. Shared Silver/Gold output does not collapse logical application identities. Archive is not retraction.

## Frontend

Contract displays coverage, keys, timing and policy lock; no coverage default is silently confirmed. Processing provides an explicit immutable declaration form, normal/correction/backfill choices and timestamp validation. Dataset-level processing continues to resolve eligible deliveries on the backend. Results show deactivated/reactivated, active/inactive, stale/conflict counts, partial safety and older-snapshot explanations. Files details retain filename and add context/outcomes. Overview uses authoritative head activity counts/boundary. Gold remains delivery-level; analytics are marked stale. No storage paths enter public responses.

## Validation evidence and limits

`tests/test_snapshot_execution.py` covers lifecycle, changed reactivation, incoming duplicates/conflicts, partial/rejected/empty safety, event/effective times, corrections/backfills, lineage tampering, declaration/policy locks, API ownership, idempotency, pre/post-write rollback, response loss, concurrency and immutable history. Existing tests use disposable PostgreSQL schemas and in-memory S3; the live check uses approved synthetic datasets and real private artifacts. See `phase6d-live-results.json` and `tools/phase6d_live_check.py` (the tool never applies migrations).

Live workspace 13: Customers SNAPSHOT dataset 18 and Snapshot Event Ordering dataset 19; deliveries 57–66. Complete sequence: 3 inserts; then 1 insert/1 update/1 unchanged/1 deactivation; then 1 reactivation. Partial delivery preserves 4 active records. Conflicts preserve all records. September backfill cannot replace October state; an explicit same-period correction updates one record; the newer complete snapshot updates one and deactivates one. Retry reuses the application. Delivery 66 was explicitly declared PARTIAL and processed through the browser dataset-level action: 2 unchanged, 4 active, 0 deactivated; refresh and Files details confirmed persistence. Ten historical S3 state snapshots were checksum/read/schema/activity/lineage verified. Desktop and 390px mobile views were checked, with no horizontal overflow in Processing.

Final validation: 218 backend tests passed in the existing virtualenv (uv unavailable), with one existing Starlette/httpx deprecation warning. Frontend lint, production build, and all 10 lightweight CJS suites passed. The installed npm CLI was invoked through Node because the roaming npm launcher points to a missing module. Git diff --check passed. No commit was created.

Pandas holds full current/delivery/candidate frames and key dictionaries in memory. No workload maximum, streaming/partition optimization, distributed queue or cumulative Gold is claimed. Session locks require live PostgreSQL connections; competing requests fail recoverably and re-evaluate on retry. Failed staging objects are retained for future cleanup. Authentication remains the existing temporary development principal. The existing browser upload prototype still blocks repeat byte-identical uploads within its local dataset cache; independent same-byte logical deliveries are supported by the backend, but an explicit browser redelivery flow is not added here. Legacy design-only PREPARED SNAPSHOT applications without declarations are not silently repinned/backfilled; they require explicit remediation or a new logical delivery. No Phase 6E work, hard deletion or commit.
