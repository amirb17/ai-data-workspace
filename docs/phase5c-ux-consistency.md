# Phase 5C UX consistency review

## Scope and readiness

Phase 5C is ready for UX review with the documented delivery-removal limitation below. No commit was made and no Phase 6 work was started. Existing upload, schema matching, rule approval, processing execution, identities, and scoped routes were preserved.

## Changes

- Replaced prominent mock Home/workspace/dataset summaries with authoritative workspace identities and dataset delivery reads.
- Kept one compact dataset header, named workspace/dataset breadcrumbs, scoped routes, and clear primary actions.
- Added shared icon-and-text status badges, scoped delivery-summary reads, dataset readiness cards, and pure next-action guidance.
- Files now reads backend logical deliveries, keyed by upload request identity. Browser inspection metadata only supplements missing profiling counts and is explicitly labelled. File bytes come from the backend physical-file record.
- Add Delivery shows actual workflow steps, preserves the selected CSV during initial contract setup, explains compatibility, and directs a completed upload to Processing.
- Contract defaults to a summary with explicit editing; browser-only persistence remains clearly labelled.
- Rules defaults to approval/reuse status. A review form appears only for an explicitly selected delivery awaiting decisions. Answer saves remain separate from final approval.
- Dataset overview separates the latest delivery from the last completed processing result.
- Data Quality shows available processing outcomes and honest unavailable/detail placeholders; Analytics reports actual output readiness without fabricated charts.
- Added purposeful empty/loading/error/missing-resource guidance, responsive wrapping, touch targets, and shared dialog focus containment/restoration.

## Safe removal discovery and blocker

Inspected file/workspace APIs, upload/physical-file/processing repositories, migrations through 0011, and schema references. There is no safe logical delivery archive/removal API, archival field, or retention/reference-count lifecycle. Existing S3 deletion is staging cleanup. Processing associations, attempts, rule lineage, DQ results, and Gold catalog records must remain intact; physical objects may be shared by deduplication.

Following the request's explicit fallback, no enabled Remove/Archive action was added. Details explain its unavailability. No local-only hiding, direct S3 deletion, cascade deletion, or fabricated count update was implemented. Removal/processed archival/shared-source-removal scenarios could not be executed and are not claimed as passing. A dedicated backend lifecycle/API is required before those actions can be enabled.

## Backend change

Only the existing delivery read model changed: select physical file size and expose `size_bytes`. Added an assertion to the existing PostgreSQL dataset-processing test. No migration, new endpoint, processing logic, archive state, or schema redesign was introduced.

## Validation

- Frontend ESLint: passed.
- Production TypeScript/Vite build: passed.
- All seven lightweight frontend suites passed: backend-integration, contracts, file-content-duplicates, ingestion-batches, processing, rule-approval, ux-consistency.
- Backend virtualenv pytest with `RUN_DB_TESTS=1`: 133 passed, no skips; one existing Starlette/httpx deprecation warning. `uv` was unavailable, so the existing `.venv/Scripts/python.exe` was used.
- Non-DB default pytest also passed: 83 passed, 50 skipped.
- Git diff reviewed for scope, ownership, stale reads, domain identities, rule decision gating, duplicate delivery rendering, route changes, and fabricated outcomes. No new dependencies.

The new frontend suite covers shared statuses; authoritative/empty/loading/error workspace states; dataset guidance; backend Files without browser metadata; two independent deliveries sharing a physical file; contract setup/summary/edit; approval/reuse versus review; actual upload steps; duplicate notice; quality/readiness; missing-resource states; unavailable archival; and workspace ownership validation. Existing suites retain blocked schema, persistence, duplicate/idempotency, scope, upload retry, and processing coverage.

## Live browser walkthrough

Created workspace **Phase 5C UX Check** (10) and dataset **Customers UX Check** (12) through the UI. Used synthetic January and February CSVs with `id,name`, one row each. The user explicitly approved only the synthetic `id is required` answer.

1. Created a workspace and dataset; real names, empty state, and primary Add Delivery action appeared.
2. Inspected January CSV, configured its initial contract, and retained the selected CSV without uploading again.
3. Uploaded through the real initiate/S3 PUT/complete workflow: upload request 42, physical file 31, separate delivery association 18.
4. Files displayed real bytes, timestamp, ready status, and labelled inspection counts before profiling.
5. Dataset pending processing ran Bronze and stopped for explicit rule review.
6. Saved the authorized YES answer and separately finalized approval. Approved Rule Version 1 appeared without another form.
7. Continued processing: January completed with 1 valid, 0 quarantined, 1 published.
8. Data Quality showed the actual clean outcome; overview and Analytics reported readiness without fake charts.
9. Accepted compatible February CSV: upload request 43, physical file 32, its own processing identity.
10. Processed the new pending delivery. January remained successful; February reused Approved Rule Version 1 without questions and completed with 1 valid, 0 quarantined, 1 published.
11. Refreshed Files and Rules: both completed deliveries and approved-rule reuse persisted. Contract summary/edit retained the expected configuration; edit was cancelled without changes.
12. Selected the same February CSV again: already-added notice, no new delivery or Upload action.
13. Escape closed the dialog and restored focus to Add Delivery.
14. Switched to workspace 9/dataset 11 and workspace 8/dataset 10: only their own filenames and completed/warning/pending states appeared.
15. Tested mismatched workspace/dataset, missing dataset, missing workspace, and unknown app route: clear recovery states and no previous dataset content.
16. Checked mobile 390x844, tablet 820x1024, desktop 1280x900: Files/Processing had matching viewport and document widths, with no horizontal overflow. The mobile dialog fit within the viewport. Temporary viewport overrides were reset.

Screenshots are in `docs/screenshots/phase5c/`: upload-completed, first-result, independent-results, mobile-files, mobile-processing, and files-completed.

## Limitations and later work

- Safe logical delivery archive/removal remains blocked by the missing backend API/lifecycle.
- Dataset contracts remain a browser-scoped prototype; no backend contract redesign in this phase.
- Early duplicate detection uses the existing scoped browser prototype. Backend canonical physical deduplication remains authoritative; no new cross-browser logical-delivery deduplication policy was invented.
- Detailed quarantine review, replay/editing, analytics exploration, complete attempt-history UI, workspace orchestration, and production authentication remain later work.
- Dataset cards read each dataset's authoritative summary independently; large-list pagination/batched summaries remain future scalability work.
- Browser evidence covers the synthetic workflow and the specified responsive sizes, not every device/browser combination.

## Changed-file manifest

- `app/db/processing_repository.py`
- `app/services/dataset_processing_service.py`
- `docs/phase5c-ux-consistency.md`
- `docs/screenshots/phase5c/files-completed.png`
- `docs/screenshots/phase5c/first-result.png`
- `docs/screenshots/phase5c/independent-results.png`
- `docs/screenshots/phase5c/mobile-files.png`
- `docs/screenshots/phase5c/mobile-processing.png`
- `docs/screenshots/phase5c/upload-completed.png`
- `frontend/src/App.tsx`
- `frontend/src/components/layout/AppShell.tsx`
- `frontend/src/components/ui/ApiFeedback.tsx`
- `frontend/src/components/ui/Button.tsx`
- `frontend/src/components/ui/NotFoundPage.tsx`
- `frontend/src/components/ui/StatusBadge.tsx`
- `frontend/src/components/ui/useDialogFocus.ts`
- `frontend/src/features/datasets/components/CreateDatasetDialog.tsx`
- `frontend/src/features/datasets/components/DatasetList.tsx`
- `frontend/src/features/datasets/components/DatasetReadiness.tsx`
- `frontend/src/features/datasets/components/DatasetStatusBadge.tsx`
- `frontend/src/features/datasets/components/DatasetsToolbar.tsx`
- `frontend/src/features/datasets/contracts/components/DatasetContractColumnRow.tsx`
- `frontend/src/features/datasets/contracts/components/DatasetContractForm.tsx`
- `frontend/src/features/datasets/contracts/components/DatasetContractSummary.tsx`
- `frontend/src/features/datasets/datasetGuidance.ts`
- `frontend/src/features/datasets/useDatasetSummary.ts`
- `frontend/src/features/files/components/DatasetCompatibility.tsx`
- `frontend/src/features/files/components/DeliverySteps.tsx`
- `frontend/src/features/files/components/DuplicateFileNotice.tsx`
- `frontend/src/features/files/components/FileStatusBadge.tsx`
- `frontend/src/features/files/components/UploadFileDialog.tsx`
- `frontend/src/features/files/uploadStep.ts`
- `frontend/src/features/ingestion/components/RuleReviewForm.tsx`
- `frontend/src/features/ingestion/processingPresentation.ts`
- `frontend/src/features/workspaces/components/CreateWorkspaceDialog.tsx`
- `frontend/src/features/workspaces/components/WorkspaceCard.tsx`
- `frontend/src/pages/Analytics/AnalyticsPage.tsx`
- `frontend/src/pages/DataQuality/DataQualityPage.tsx`
- `frontend/src/pages/Dataset/Analytics/DatasetAnalyticsPage.tsx`
- `frontend/src/pages/Dataset/Contract/DatasetContractPage.tsx`
- `frontend/src/pages/Dataset/DataQuality/DatasetDataQualityPage.tsx`
- `frontend/src/pages/Dataset/DatasetDetailPage.tsx`
- `frontend/src/pages/Dataset/Files/DatasetFilesPage.tsx`
- `frontend/src/pages/Dataset/History/DatasetHistoryPage.tsx`
- `frontend/src/pages/Dataset/Overview/DatasetOverviewPage.tsx`
- `frontend/src/pages/Dataset/Rules/DatasetRulesPage.tsx`
- `frontend/src/pages/Home/HomePage.tsx`
- `frontend/src/pages/Processing/ProcessingPage.tsx`
- `frontend/src/pages/Workspace/Analytics/WorkspaceAnalyticsPage.tsx`
- `frontend/src/pages/Workspace/DataQuality/WorkspaceDataQualityPage.tsx`
- `frontend/src/pages/Workspace/Datasets/WorkspaceDatasetsPage.tsx`
- `frontend/src/pages/Workspace/Overview/WorkspaceOverviewPage.tsx`
- `frontend/src/pages/Workspace/Processing/WorkspaceProcessingPage.tsx`
- `frontend/src/pages/Workspace/WorkspaceDetailPage.tsx`
- `frontend/src/pages/Workspaces/WorkspacesPage.tsx`
- `frontend/src/services/api/rules.ts`
- `frontend/tests/ux-consistency.cjs`
- `tests/test_dataset_pending_processing.py`
