# Phase 5C.1: safe logical delivery archive

## Discovery and design

The project had no archive field or endpoint. `upload_requests` represents logical arrivals; `physical_files` is canonical shared source storage. Dataset-version-file associations, processing attempts, approved rule pins, DQ runs/issues, Gold runs/artifacts/catalogs depend on historical source/association identities. Existing foreign keys and staging-object cleanup do not provide a safe delivery deletion lifecycle.

The existing unique `(dataset_version_id, file_id)` association can be referenced by multiple logical uploads. Archive leaves that association untouched, including when another active upload still uses it.

This phase updates only a logical upload request. It never deletes a physical file, S3 object, association, rule policy, processing attempt, DQ record, or Gold record. Prior uncommitted Phase 5C changes were preserved.

## Persistence and API

Migration `0012_delivery_archive.sql` adds nullable `archived_at TIMESTAMPTZ` and `archived_by BIGINT REFERENCES users(user_id)`, with a constraint requiring both fields to be set together. The principal type matches authoritative PostgreSQL user identities. Applied to the development database for live testing; other environments must apply migrations through 0012 before running this code.

`POST /files/uploads/{upload_id}/archive` accepts `workspace_id` and `dataset_id`. Existing ownership validation verifies the principal, workspace/dataset membership, and the logical delivery's scope. Only completed uploads can be archived. The response contains upload/workspace/dataset identities and stable archive time/principal. Repeated successful requests return the same audit state.

## Processing safety

Archive, Bronze start, and continuation share a nonblocking PostgreSQL upload lifecycle advisory lock, covering the interval before an association or attempt exists. Archive also acquires the existing association lock without invoking recovery, and checks active attempts/status. Busy processing returns HTTP 409. Archive does not cancel or rewrite attempts. Archived requests cannot start Bronze or continue Silver/Gold, including direct request retries. Legacy association continuation resolves only active logical uploads.

The existing pending-processing coordinator excludes archived requests at query time and eligibility recheck. Successful independent deliveries remain unaffected. Existing dataset/association recovery semantics remain in place.

## Active views and UI

The centralized dataset delivery query excludes archived requests. Files, Processing, Overview, Rules, DQ, Analytics readiness, dataset cards, and pending summaries consume this active read model.

Files keeps Add Delivery as its primary action. Each delivery has a secondary More disclosure with View details, View Processing, and Remove Delivery or Archive Delivery. Pending/awaiting/ready deliveries use Remove. Completed Silver/Gold or terminal deliveries use Archive. Running processing disables the action, and the backend also enforces the restriction.

Confirmation displays filename, dataset, consequences, retained canonical bytes/history, Cancel, and a destructive final confirmation button. The existing shared focus hook keeps keyboard focus inside, restores focus on cancel, and supports Escape when idle. Submission is guarded against double clicks, with retryable errors and no local optimistic removal. Success refreshes the authoritative active list. Subsequent navigation/refresh reads fresh scoped summaries.

An archived list, restore, bulk deletion, cancellation, retention, garbage collection, and physical deletion are deliberately omitted. Historical context remains available through the owned processing-context endpoint and retained database records; no new audit subsystem was introduced.

## Tests and validation

- New PostgreSQL archive tests cover unprocessed removal, repeat idempotency, wrong principal/workspace/dataset, persistent reads, pending exclusion, processing rejection, awaiting/ready removal, completed history, and two logical requests sharing one physical source.
- New frontend lightweight suite covers Remove/Archive policy, running-state blocking, accessible confirmation, mobile layout structure, scoped API payloads, mismatched responses, and safe conflict messages.
- Existing bridge test mocks were extended for the new lifecycle boundary; existing Phase 5C assertions were updated from unavailable archival to the actual secondary actions.
- Focused archive/execution/bridge run: 42 passed, one existing Starlette/httpx deprecation warning.
- Frontend lint and production build passed. All eight lightweight suites passed.
- `git diff --check` passed. Existing Phase 5C work remains uncommitted and was not reverted.
- Final full backend regression: **138 passed**, no skips, one existing Starlette/httpx deprecation warning, in 300.27 seconds. `uv` is unavailable; tests use the existing `.venv/Scripts/python.exe` with `RUN_DB_TESTS=1` and isolated schemas.

## Live integration evidence

Development workspace 10, Customers UX Check dataset 12:

1. Accepted `customers-march.csv` as upload 44 / physical file 33 without processing.
2. Removed it using the accessible confirmation at mobile 390x844. The dialog fit within the viewport, Cancel had initial focus, and document width matched viewport width.
3. Refreshed: March was absent from active Files and Processing. Pending was 0; completed January/February were unchanged.
4. Created dataset 13, Phase 5C.1 Shared Source Check, and accepted the original February CSV as upload 45. Backend reused physical file 32 already referenced by upload 43 in dataset 12.
5. Captured upload 43's history, then archived it through the completed-delivery confirmation.
6. Refresh showed only January in dataset 12. Upload 44 and upload 43 have persistent archive timestamps/principals; upload 45 remains active.
7. Before/after checks retained association count 1, attempts 2, DQ run 1, Gold run 1, artifact 1, and pinned Rule Version 1. The canonical source object still exists with 30 bytes.
8. Upload 45 remained eligible in dataset 13 and successfully reused Bronze source metadata, reaching explicit AWAITING_RULES. No new business rule answer was invented or approved.

Screenshots: `docs/screenshots/phase5c1/remove-confirmation-mobile.png`, `archive-confirmation.png`, `pending-removed.png`, `archived-active-files.png`, and `shared-source-survives.png`.

## Limitations

- No archived browser view or restore endpoint; history remains in PostgreSQL and the owned processing-context endpoint.
- Existing browser inspection/deduplication cache is retained. Selecting the same bytes again in that browser/dataset may still show the existing duplicate notice even after archival; this does not reactivate archived backend deliveries. Another dataset can accept the same canonical bytes, as verified live.
- Interrupted attempts still marked PROCESSING require existing recovery before archival; this phase does not implement cancellation.
- Archive does not retract published data or redesign incremental APPEND/UPSERT/SNAPSHOT behavior.
- Development identity remains temporary; full production authentication is outside this phase.

## Phase 5C.1 changed files

- `migrations/0012_delivery_archive.sql`
- `app/services/delivery_lifecycle_service.py`
- `app/api/files.py`
- `app/db/processing_repository.py`
- `app/services/processing_context_service.py`
- `app/services/processing_service.py`
- `app/services/delivery_execution_service.py`
- `app/services/dataset_processing_service.py`
- `frontend/src/pages/Dataset/Files/DatasetFilesPage.tsx`
- `frontend/src/features/files/deliveryArchive.ts`
- `frontend/src/features/files/components/ArchiveDeliveryDialog.tsx`
- `frontend/src/features/files/components/DeliveryMoreMenu.tsx`
- `frontend/src/services/api/deliveries.ts`
- `frontend/src/services/api/rules.ts`
- `tests/test_delivery_archive.py`
- `tests/test_rule_approval_bridge.py`
- `frontend/tests/delivery-archive.cjs`
- `frontend/tests/ux-consistency.cjs`
- This report and the five screenshots listed above.

Ready for Phase 5C.1 approval with the listed limitations. No commit. No Phase 6 implementation.
