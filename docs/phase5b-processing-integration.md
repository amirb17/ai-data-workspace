# Phase 5B: real Silver and Gold integration

## Existing implementation discovered

The file API already exposed association-based Silver and Gold routes. The processing service already owned stage attempts, DQ persistence, failure transitions, Gold publication recovery, artifacts, and semantic catalog persistence. The Silver processor reads physical Bronze Parquet, applies common cleaning and existing transformations/validators, and writes valid and quarantine Parquet. Gold reads the exact successful Silver output, removes DQ technical columns, writes the base output, builds deterministic dataset-level marts, and publishes their semantic catalog.

Previously, those two stage routes had no principal/ownership dependency and returned private storage metadata. Silver used the current mutable active rule rows/counter. Gold already checked successful Silver and publication completeness, but its check/create sequence did not atomically exclude another worker. Metadata writes committed separately, which could leave partial publication records. The frontend stopped at rule readiness and displayed local unavailable outcomes.

## Authoritative entry and exact lifecycle

The frontend calls POST /files/uploads/{upload_id}/continue with workspace_id and dataset_id. It supplies no storage location, local batch identity, or arbitrary association. The service resolves a completed owned upload, owned workspace/dataset, exact schema-matched association, and applied approved policy.

The original /process endpoint remains Bronze-only. After Bronze and approval/reuse, continuation uses the existing stages:

1. READY_FOR_SILVER or SILVER_FAILED: execute Silver with the pinned approved snapshot.
2. SILVER_PROCESSING: persist a visible active attempt while storage work runs.
3. Successful Silver: atomically commit DQ run, issues, successful attempt, and READY_FOR_GOLD.
4. READY_FOR_GOLD or GOLD_FAILED: use the exact successful DQ run and execute Gold.
5. GOLD_PROCESSING: persist a visible active attempt while publication runs.
6. Successful Gold: atomically commit run/artifacts/catalog, successful attempt, and SUCCESS.

Stage failure atomically records FAILED on its attempt and SILVER_FAILED/GOLD_FAILED on the association. The shared physical file is unaffected. Retry skips successful Silver and recovers Gold alone when appropriate. No second processing engine or workspace orchestration was added.

The existing association-based Silver/Gold URLs remain available, now ownership-checked and resolved through a completed owned upload. Their response is the safe processing context rather than the previous private stage result. The legacy rule PATCH endpoint now returns 409 after ownership validation: it previously mutated policy and regressed finalized delivery status. Versioned editing and explicit historical replay remain unavailable.

## Approved policy and historical safety

Migration 0010 adds approved_rule_policies keyed by dataset version and rule version. Approval inserts the active configurations into a snapshot using insert-on-conflict-do-nothing. Runtime execution reads that snapshot; it never overwrites it. Later mutable drafts do not replace historical delivery pins or their applied configurations.

The migration was applied to the development database. Only currently approved versions can be safely backfilled from legacy mutable rows. An older pin without a reconstructable snapshot fails closed; no newer active version is substituted. New approvals persist snapshots in the existing approval transaction.

## Idempotency, concurrency, and recovery

A PostgreSQL session advisory lock spans one association's entire continuation, including storage I/O. Individual stage services use the same guard; nested stage calls share the already-held lock. Concurrent requests are rejected before execution. The existing physical-file active-attempt unique index remains as an additional conservative guard across contexts sharing physical content.

Silver reuses the successful DQ run for the applied version. Gold reuses the matching successful DQ publication only when artifacts and semantic catalog satisfy the existing completeness check. Successful contexts do not regress on repeated continuation or Bronze. Legacy incomplete Gold metadata is recovered into the same run. New publication metadata commits atomically, avoiding reusable partial success records.

If a worker stops, its session lock is released by PostgreSQL. An explicit recovery request that obtains the lock atomically marks the abandoned active attempt CRASHED and restores the appropriate retry state. It resumes from persisted successful stages without Bronze or reupload. A still-running worker retains the lock, so a recovery click cannot interrupt it.

## Public status and frontend

The existing GET processing-context endpoint supplies stages, applied/current rule versions, latest attempt, times, safe stage error summary, input/valid/rejected/output counts, and quarantine issue totals. Counts of applied rules refer to the pinned snapshot when available. Private paths, infrastructure errors, stack traces, credentials, and download URLs are excluded.

Processing renders backend lifecycle status and Bronze/Rules/Silver/Gold stages. Continue, Retry, and interrupted-worker recovery actions are gated by that state. Polling continues while an action or stage is active and recovers after refresh. Requests are cancelled on scope changes; stale workspace/dataset/upload state is not rendered.

Source inspection rows/columns remain physical metadata. Outcome metrics come from the backend, even when local batch fields contain values. Missing metrics display an em dash; a real zero is displayed only when persisted by processing. Quarantine is summary-only. SUCCESS with rejected rows includes a warning message without inventing a backend SUCCESS_WITH_WARNINGS status.

## Validation and live integration

- Full backend suite: 124 passed with RUN_DB_TESTS=1, including real PostgreSQL tests and the final exact-association guard. uv is unavailable; the existing project virtualenv was used. One existing Starlette/httpx deprecation warning remains.
- Frontend lint and production build passed. All six lightweight suites passed: contracts, ingestion-batches, file-content-duplicates, backend-integration, rule-approval, and processing.
- git diff --check passed. Git status and runtime diff were reviewed; no commit was made.

The new lifecycle fixture uses PostgreSQL plus the real Silver/Gold processors and an in-memory S3 client. It verifies actual Parquet valid/rejected/output rows, deterministic marts and catalog, same-schema second delivery, idempotency, stage and metadata failures, partial Gold recovery, interrupted-worker rollback/recovery, concurrent rejection, historical policy execution, ownership, and safe read responses.

Live development workspace 8 / dataset 9:

| Delivery | Applied policy | Result | Input | Valid | Rejected | Output |
| --- | --- | --- | --- | --- | --- | --- |
| Upload 32 / original approval | Version 1 | SUCCESS | 1 | 1 | 0 | 1 |
| Upload 37 / approved-rule reuse | Version 1 | SUCCESS | 2 | 2 | 0 | 2 |

Both were continued through the browser. Refresh recovered SUCCESS with no execution action. Published Silver and Gold Parquet row counts were read back and matched the API/UI. Two further continuation requests per upload left DQ-run, Gold-run, and attempt counts unchanged. Workspace/dataset mismatch requests returned 403.

Switching to workspace 8 / dataset 10 showed its own upload 34 as READY_TO_PROCESS, despite sharing the original physical file; it did not inherit upload 32's execution status. Workspace 7 / dataset 8 showed its own empty delivery state. Mobile verification found no document overflow (content and client widths both 375 pixels at a 390-pixel browser override). The override was reset. Screenshot proofs are phase5b-processing-desktop.png and phase5b-processing-mobile.png.

Live batches contained no rejected rows. Nonzero quarantine and failure injection were verified in isolated PostgreSQL/storage tests, without modifying live policies or intentionally breaking live storage.

## Limitations and domain distinctions

- Execution remains synchronous HTTP processing; large-volume workers and scheduling are not introduced.
- Existing Gold requires at least one valid Silver row. An all-rejected delivery currently fails Gold rather than publishing an empty successful dataset.
- Delivery listing remains the scoped localStorage presentation prototype. Processing status and outcomes are backend-authoritative.
- Backend execution remains association-scoped: identical-content logical uploads can share a physical-file/version association. This phase preserves that existing distinction.
- Rules UI still reviews current dataset-version answers; immutable policy snapshots now govern execution. Versioned historical policy inspection/editing and replay need a later explicit feature.
- Older policies that cannot be reconstructed safely require an explicit recovery/review decision. No historical rules were invented.
- Live infrastructure failure injection was not performed. Retry and interruption cases were tested in isolated schemas with the real processors and controlled storage.

## Files changed

- app/api/files.py
- app/db/dataset_repository.py
- app/db/processing_repository.py
- app/processing/silver_processor.py
- app/services/delivery_execution_service.py
- app/services/processing_context_service.py
- app/services/processing_service.py
- migrations/0010_rule_policy_snapshots.sql
- frontend/src/features/ingestion/components/DeliveryRuleBridge.tsx
- frontend/src/features/ingestion/components/IngestionBatchCard.tsx
- frontend/src/features/ingestion/components/ProcessingDetails.tsx
- frontend/src/features/ingestion/processingState.ts
- frontend/src/features/ingestion/useProcessingContext.ts
- frontend/src/pages/Dataset/Processing/DatasetProcessingPage.tsx
- frontend/src/services/api/rules.ts
- frontend/tests/processing.cjs
- frontend/tests/rule-approval.cjs
- tests/test_delivery_execution.py
- tests/test_approved_rule_reuse.py
- tests/test_processing_rule_gate.py
- tests/test_rule_approval_bridge.py
- docs/phase5b-processing-integration.md
- docs/phase5b-processing-desktop.png
- docs/phase5b-processing-mobile.png

## Reviewer browser checks

1. Open workspace 8 / dataset 9 Processing: uploads 32 and 37 are successful, with policy 1 and real metrics.
2. Refresh: stage states, counts, and applied policy persist; no Continue action appears for success.
3. Upload a different-content same-schema CSV, prepare Bronze, confirm approved-rule reuse, then Continue Processing through Silver/Gold.
4. Upload 38 remains AWAITING_RULES; its changed schema must not execute without explicit approval.
5. Check a dataset with rejected rows: view the quarantine count/issue summary without row editing.
6. Switch to workspace 8 / dataset 10 and workspace 7 / dataset 8: verify only their own deliveries/state.
7. In a disposable test environment, exercise Silver/Gold failure and interrupted-worker recovery; concurrent recovery must not interrupt a live worker.
8. Review at mobile width: actions, metrics, stages, and error text remain usable.

Phase 5B is ready for approval within this scope. No commit or Phase 6 work was performed.
