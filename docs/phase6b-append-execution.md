# Phase 6B: APPEND execution

Dataset remains the processing boundary; workspace remains ownership/orchestration scope. Phase 6A policies, application identities, typed hashes, immutable state metadata and atomic publication are reused. No default policy is inferred for existing datasets. No UPSERT/SNAPSHOT execution, cumulative Gold rebuild, workspace orchestration, new dependencies or commit.

## Delivery input and pins

Migration 0014 adds immutable `delivery_manifests`, one version-1 READY_TO_APPLY manifest per logical application, and nullable incremental rejection / dataset analytics freshness fields. Migration 0013 remains the inherited foundation. Both are applied to development PostgreSQL; no historical data/policies are backfilled.

After approved Silver succeeds, the application pins upload, dataset, schema version, physical file association, DQ run, rule version and immutable load policy. The executor copies the successful Silver business payload to a unique delivery/application prefix. The private manifest contains original Silver byte SHA-256, copied Parquet SHA-256, effective schema, counts, timestamps, policy snapshot, normalization version and lineage IDs. Conditional S3 creation, readback and PostgreSQL update/delete guards protect the registered snapshot. Retries consume that snapshot even if a legacy Silver path is subsequently replaced.

The existing Silver path is a bare internal key in DQ metadata, rather than invariably an S3 URI. The adapter supports the configured bucket URI and the exact owned legacy Silver key, rejecting a different namespace/file/rule. Legacy processing remains association/file scoped; independent logical uploads now have independent application/snapshot/state lineage even when their physical bytes and successful processing stages are reused.

## APPEND semantics

- Explicit unkeyed APPEND inserts every valid independent delivery row. Retrying one upload is idempotent; identical bytes accepted as a different delivery may append again. Cross-delivery business deduplication is unavailable without an explicit key.
- Explicit keyed APPEND inserts absent keys. Existing key with identical canonical business content is a duplicate; changed content is rejected, never updated. Identical incoming repeats contribute one insertion and remaining duplicates; conflicting incoming content rejects every row for that key.
- Invalid/null keys, unsafe large floating keys and invalid/naive configured event timestamps are rejected conservatively. Valid late event timestamps can append; ingestion order never becomes last-write-wins.
- SHA-256 matching is defended by canonical key/content comparison. Operational metadata does not affect business identity. Previous state rows are retained exactly, with original lineage.
- Silver input = valid + Silver quarantine. Valid Silver rows = inserted + duplicates + incremental rejections. Updated/unchanged/deactivated stay NULL for APPEND. Incremental conflicts are separate from Silver quarantine, with private row-index/reason outcomes.

## State publication and recovery

Pandas creates a full immutable Parquet state candidate under a unique application/candidate prefix, plus outcome and manifest objects. Validation reads referenced objects, verifies checksums, private prefix, application/dataset/schema/policy/upload ownership, effective business schema, row count, complete lineage and outcome accounting. Existing trusted state is also checksummed/readable before calculation; incompatible policy/schema requires an explicit migration.

Dataset and upload lifecycle PostgreSQL advisory locks span calculation, object I/O and commit. Nested calls reuse the same in-process lock scope, not a second coordinator. Contending callers fail safely and retry; no browser-owned work list or stale head is trusted. The existing publication transaction atomically marks the candidate PUBLISHED, moves the dataset pointer, marks application SUCCESS with metrics and marks dataset-state analytics STALE. Head comparison rejects stale candidates. Failures preserve the old head; retries reuse the same application/pins and successful Silver. Response loss after commit returns durable success on retry. A successful archived application remains readable/idempotent; archived unapplied deliveries cannot publish. Archive does not retract records.

## Lifecycle and user interface

Configured APPEND continues Silver -> Dataset Update -> delivery Gold using the existing dataset-level pending action. READY_TO_APPLY is eligible; blocked unsupported/incompatible policies require Contract review; application failures have a contextual Dataset Update retry. Successful legacy file stages do not hide a pending application for another logical arrival. Unconfigured legacy datasets retain their existing behavior.

Contract explains keyed/unkeyed APPEND and freezes policy changes after publication. Overview reads the authoritative head count and latest applied filename/added/duplicate/quarantine/conflict counts. Processing cards display APPEND insertions/duplicates/conflicts; details show load mode, Dataset Update, application result count and NULL non-applicable metrics. The per-delivery "Current Dataset" count is the count at that application's publication; Overview is the current head. Existing Gold remains delivery-level output and may include rows excluded from cumulative APPEND. Overview/Analytics clearly state that cumulative dataset analytics await a future rebuild.

## Validation

- Full backend suite: 176 passed (493.79 seconds), one existing Starlette/httpx deprecation warning. `uv` is unavailable; used the existing `.venv/Scripts/python.exe -m pytest -q` with `RUN_DB_TESTS=1` and isolated PostgreSQL schemas.
- Final focused APPEND rerun: 14 passed after candidate validation and the post-commit response interruption refinements. One additional late-event-time check passed separately (15 APPEND cases total). Tests use real PostgreSQL/Parquet and immutable in-memory S3; concurrency uses actual advisory locks.
- Frontend lint and production build pass. All nine existing lightweight CJS suites pass, including policy/result/null-count/stage/retry/scope checks. The installed npm launcher is broken; used the existing Program Files npm CLI through Node without installing anything.
- Live HTTP + private S3 integration in workspace 11, datasets 14/15, uploads 46–50: unkeyed 100 then 50 => 150; keyed 1/2/3 then 3/4/5 => 2 inserted, 1 duplicate, head 5; incoming id 6 with conflicting content => both rejected, head remains 5. Repeated application returns the same identity. See `phase6b-live-results.json` and `tools/phase6b_live_check.py`.
- Synthetic rule approvals were explicit: user approved id required, then specifically approved Silver ALLOW for the conflicting-key test. The executor did not invent business rule answers. Earlier deliveries retain their original rule pins.
- Browser refresh verified APPEND stages, 0 inserted / 2 conflicts / head 5, and separate rule-version lineage. Switching to the unkeyed dataset showed head 150 and its own deliveries/policy. Mobile Overview/Contract had no horizontal overflow with a 390px viewport override; policy change remained disabled after publication. The temporary viewport was reset. Evidence is under `docs/screenshots/phase6b`.
- Final `git diff --check` and whitespace checks on untracked source/documentation passed. No commit.

## V1 limits

Full input/current/candidate artifacts and a keyed lookup are materialized in memory. There is no established safe maximum, partition rewrite, streaming engine or queue. Long requests hold session advisory locks; distributed workers need durable leases/fencing later. State/source integrity failures require operator correction before retry. Orphan STAGED candidates/objects are retained; retention cleanup is deferred. Manifest v1 is immutable and one-per-application; explicit rule/policy replay needs a separately authorized future design. Decimal/large-number fidelity depends on existing Bronze/Silver parsing; APPEND cannot reconstruct precision already lost upstream. No automatic schema union, timezone guess, policy migration or Gold rebuild.

## Files in this phase

New: `app/processing/append_engine.py`, `app/services/append_application_service.py`, `app/storage/incremental_artifacts.py`, `migrations/0014_append_delivery_manifests.sql`, `tests/test_append_execution.py`, `tools/phase6b_live_check.py`, this note and live/browser evidence.

Updated foundation/integration: `app/api/incremental.py`, `app/db/incremental_repository.py`, `app/services/incremental_policy_service.py`, `app/services/dataset_processing_service.py`, `app/services/delivery_execution_service.py`, `app/services/delivery_lifecycle_service.py`, `app/services/processing_context_service.py`, `tests/test_incremental_foundation.py`, ADR 0001.

Frontend: `frontend/src/services/api/{incremental,rules}.ts`, `frontend/src/features/datasets/datasetGuidance.ts`, `frontend/src/features/datasets/contracts/components/IncrementalPolicy.tsx`, ingestion components `DeliveryRuleBridge`, `IngestionBatchCard`, `ProcessingResult`, `ProcessingStages`, `processingPresentation.ts`, `processingState.ts`, `frontend/src/pages/Dataset/{Analytics/DatasetAnalyticsPage,Overview/DatasetOverviewPage}.tsx`, lightweight tests `incremental.cjs`, `processing.cjs`, `ux-consistency.cjs`.

Other uncommitted Phase 6A foundation files were preserved. No backend rewrite or existing route changes.
