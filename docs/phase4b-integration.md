# Phase 4B frontend integration

**Current status:** The CORS blocker recorded below has been resolved. Actual browser uploads,
refresh persistence, canonical reuse, schema gates and scope isolation passed the
[post-CORS retest](phase4b-cors-retest.md). Phase 4B is ready for approval within its prototype scope.

## Environment

Copy `frontend/.env.example` to `frontend/.env.local` if needed. The default is
`VITE_API_BASE_URL=http://localhost:8000`. Restart Vite after changing it.
Run the existing backend with its development identity enabled (`APP_ENV=development`,
`ENABLE_DEV_IDENTITY=true`, and the centralized `DEV_USER_KEY` configuration).
This uses a real PostgreSQL user; it is temporary development identity, not authentication.

The backend already permits the Vite development origin. Direct S3 uploads also
require **bucket CORS**. The October 5 live check received HTTP 403 for the S3
preflight with no `Access-Control-Allow-Origin`, although the server-side PUT and
completion succeeded. A bucket administrator must allow the actual Vite origin
(`http://localhost:5173`, and `http://127.0.0.1:5173` if used), method `PUT`, and
header `Content-Type`. This phase does not change AWS bucket settings or credentials.
For example, the relevant bucket CORS rule is:

```json
[
  {
    "AllowedOrigins": ["http://localhost:5173", "http://127.0.0.1:5173"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["Content-Type"],
    "MaxAgeSeconds": 300
  }
]
```

## Identity and migration

Startup `/me` gates the application. The app header displays the backend development user instead of a mock user/administrator role. Workspaces and datasets use backend
create/list/get APIs and PostgreSQL IDs. Backend `ACTIVE`/`ARCHIVED` are entity
lifecycle states, not processing readiness. Missing processing metrics display
an em dash rather than fabricated counts.

Contracts, completed file metadata, and batches remain browser-local prototypes.
Their keys are:

`datarise-backend-{encoded API base}-user-{userId}-workspace-{workspaceId}-dataset-{datasetId}-{contract|files|batches}`

Old `datarise-workspaces` and `datarise-workspace-...` keys are retained separately.
They are not automatically mapped to backend IDs and never seed real entities.
No old mock contract, file, or batch is attached merely because IDs happen to match.
The legacy acceptance helper rejects use in the active backend namespace.

## Acceptance and lineage

CSV inspection, SHA-256 duplicate UX checks, and contract comparison occur before
any network upload. Saving an initial contract returns to review using the same
selected file. Only MATCH/WARNING acceptance initiates an upload.

The upload workflow captures user/workspace/dataset and proceeds through
INITIATING → UPLOADING → COMPLETING → SUCCESS (or FAILED). PUT uses the original
File bytes and `Content-Type: text/csv`. Signed URLs remain transient and are
discarded after successful PUT. Storage paths are excluded from mapped metadata.

Backend completion owns file ID, name, size, hash and UPLOADED status. The local
Files cache has one row per physical file per dataset. Each logical upload request
creates one batch, identified by `upload-{upload_request_id}`, pointing to the
physical file ID. Canonical reuse can therefore yield one physical file and more
than one logical batch. Batch outcome counts remain null and processing never starts.
Batch source names preserve the selected logical delivery filename even if the
canonical physical filename differs.

PUT failures retry the same session while its signed URL remains valid. Completion
failures skip PUT and retry the same request ID. Local persistence failures after
completion reconcile the cached result without another network upload. File
persistence precedes batch persistence; partial local writes are repaired on retry.
Repeated clicks are ignored while busy. Leaving the route aborts/invalidate callbacks
and never assigns the upload to the newly selected scope.

## Validation performed

- Frontend lint and production build.
- Existing contracts, batches and content-duplicate test scripts.
- `node tests/backend-integration.cjs`: mappings, request bodies/MIME, state transitions,
  schema gates, retry identity, physical reuse, logical batch uniqueness, local write
  failures, route disposal, StrictMode replay, and workspace/dataset/user/API isolation.
- Live browser created workspace #8 and dataset #9, inspected the smoke fixture,
  saved a contract, and preserved backend entities on refresh. The S3 CORS failure
  left the browser Files list empty and created no frontend batch.
  A refreshed browser retained the contract and blocked `phase4b-breaking.csv`
  because its required `id` column was missing. A browser proof screenshot is
  available in `docs/phase4b-browser-check.png`.
- Live API/S3 PUT completed logical uploads #30/#31 using physical file #22.
  Upload #31 reused canonical content. Repeated completion returned the same result.
  Reconciliation of those real responses yielded one file and two logical batches
  in a temporary test storage adapter, with repeat reconciliation adding nothing.

## Limitations and remaining browser checks

There is no backend list-files endpoint. Only completion results observed and
cached by this browser appear in Files; uploads performed elsewhere do not sync.
Contracts and batches are local, not shared with the backend or other browsers.
Clearing browser storage loses those prototypes, not backend entities/files.
Session retries survive only while the dialog is open. Closing, refreshing or
navigating away can leave an initiated or already-completed backend upload without
a local cache record. An expired PUT URL requires closing and choosing the file
again; this phase adds no session-refresh endpoint. An initiation response lost
after server persistence cannot be recovered by the frontend with the current API.
Browser-local writes are not a distributed transaction or cross-tab lock.

After bucket CORS is configured, repeat the smoke CSV in the browser and verify:
upload success → file UPLOADED → one batch READY_TO_PROCESS → refresh retains both.
Re-selecting identical bytes in the same dataset must show the early duplicate
notice. Upload the same bytes in a different dataset to exercise canonical reuse.
Use `frontend/tests/phase4b-breaking.csv` after the smoke contract marks `id` required:
Accept must be disabled and no initiate request should occur. Also verify unrelated
columns, additive WARNING, STRICT extras, complete retry, unavailable backend,
invalid ownership routes, workspace/dataset switching, and narrow mobile layouts.

The test workspace/dataset and durable uploads remain for inspection; no processing,
automatic cleanup, backend changes, Phase 5 work, or commits were performed.

## Files changed

- `docs/phase4b-browser-check.png`
- `docs/phase4b-integration.md`
- `frontend/.env.example`
- `frontend/src/App.tsx`
- `frontend/src/components/layout/AppShell.tsx`
- `frontend/src/components/layout/IdentityBootstrap.tsx`
- `frontend/src/components/ui/ApiFeedback.tsx`
- `frontend/src/features/datasets/components/CreateDatasetDialog.tsx`
- `frontend/src/features/datasets/components/DatasetList.tsx`
- `frontend/src/features/datasets/components/DatasetsToolbar.tsx`
- `frontend/src/features/datasets/contracts/storage.ts`
- `frontend/src/features/datasets/types.ts`
- `frontend/src/features/files/components/FileStatusBadge.tsx`
- `frontend/src/features/files/components/UploadFileDialog.tsx`
- `frontend/src/features/files/data/storage.ts`
- `frontend/src/features/files/types.ts`
- `frontend/src/features/files/uploadWorkflow.ts`
- `frontend/src/features/ingestion/components/IngestionBatchCard.tsx`
- `frontend/src/features/ingestion/data/storage.ts`
- `frontend/src/features/ingestion/reconcileCompletedUpload.ts`
- `frontend/src/features/ingestion/types.ts`
- `frontend/src/features/workspaces/components/CreateWorkspaceDialog.tsx`
- `frontend/src/features/workspaces/components/WorkspaceCard.tsx`
- `frontend/src/features/workspaces/types.ts`
- `frontend/src/pages/Dataset/DatasetDetailPage.tsx`
- `frontend/src/pages/Dataset/Files/DatasetFilesPage.tsx`
- `frontend/src/pages/Workspace/Datasets/WorkspaceDatasetsPage.tsx`
- `frontend/src/pages/Workspace/WorkspaceDetailPage.tsx`
- `frontend/src/pages/Workspaces/WorkspacesPage.tsx`
- `frontend/src/services/api/client.ts`
- `frontend/src/services/api/datasets.ts`
- `frontend/src/services/api/files.ts`
- `frontend/src/services/api/identity.ts`
- `frontend/src/services/api/useApiResource.ts`
- `frontend/src/services/api/workspaces.ts`
- `frontend/src/services/identityContext.ts`
- `frontend/src/services/localScope.ts`
- `frontend/tests/backend-integration.cjs`
- `frontend/tests/phase4b-breaking.csv`
- `frontend/tests/phase4b-smoke.csv`
