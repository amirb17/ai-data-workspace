# Phase 4 ingestion API discovery — integration stopped

The request explicitly requires stopping when backend-specific identities are unavailable in the frontend. No runtime implementation was changed.

## Existing API

Routes are registered directly by app/main.py with no /api prefix.

POST /files/initiate (app/api/files.py, FileInitiateRequest):
- user_id: required integer
- workspace_id, dataset_id: optional integers; provide both for scoped ingestion
- file_name: required string
- file_size: required integer
- content_type: optional string

Response fields: message, user_id, file_name, file_size, content_type, object_key, presigned_url, expires_in (900 seconds). Initiate does not persist an upload request/session.

The presigned URL permits a single S3 PUT. Signing includes Bucket and Key; Content-Type is not bound by the current signer. Retain returned object_key and URL only in transient service state; never display/log/persist them. The UI must not construct destinations.

POST /files/complete (FileCompleteRequest):
- user_id: required integer
- workspace_id, dataset_id: optional integers, with the same scoped context
- object_key: required string, supplied by initiate
- expected_file_size: required integer

Response:
- message, is_duplicate
- file: file_id, file_name, file_size, file_hash, storage_path, status
- upload_request: upload_id, user_id, file_id, workspace_id, dataset_id, status, created_at

Do not store or render storage_path. Physical-file status is UPLOADED, distinct from the prototype READY_TO_PROCESS status. Browser row/column counts are inspection metadata, not backend processing results.

## Existing behavior

app/services/file_service.py validates paired workspace/dataset context using the database dataset ownership lookup. It validates the staging object's user prefix, obtains HEAD metadata, compares object size, hashes actual S3 bytes with SHA-256, reuses a global canonical physical file by hash or promotes a new one to immutable Raw, then creates a logical upload request. app/db/file_repository.py also resolves physical-file hash insertion races. Frontend duplicate checks remain scoped to workspace + dataset; backend physical reuse is global.

No dataset_version_id is required for upload. Dataset versions and dataset-version-file links are resolved by later processing, which must not be invoked in Phase 4.

## Blocking identity mismatch

Frontend workspace/dataset records come from mocks and localStorage; newly created records use Date.now() identities. They are not database identities and have no explicit backend mapping. DatasetListItem has no backend identity/provenance fields. The frontend also has no established backend user_id. Numeric mock IDs cannot safely be assumed to represent real backend objects.

Do not fabricate user_id, send local route IDs as database IDs, infer identity from names, or omit workspace/dataset context to bypass ownership validation.

Existing endpoints available for an explicitly agreed identity integration are GET /workspaces/{workspace_id}, GET /workspaces/{workspace_id}/datasets, POST /workspaces, and POST /workspaces/{workspace_id}/datasets. Creation must return authoritative identities; the local contract/file/batch scoping transition must be defined before wiring uploads.

## Completion retry gap

Completion deletes the staging object before creating the logical upload request. Initiate returns no persisted session identity. create_upload_request performs an unconditional INSERT. Therefore physical hash deduplication does not make logical completion idempotent. A lost completion response cannot safely be retried as though an upload session were persisted; a later call may encounter a missing staging object, and starting a new upload can create another logical request.

A minimal, agreed recovery/idempotency mechanism is needed before promising safe completion retries. Do not redesign canonical file handling or introduce processing/batch tables as a workaround.

## CORS and practical limits

app/main.py currently has no CORS middleware. No frontend API base URL or proxy convention was found. Once integration is unblocked, use VITE_API_BASE_URL and explicitly configured allowed development origins, not wildcard credential CORS. S3 bucket CORS must independently allow the selected development origin and PUT; deployment configuration was not inspected or changed.

Current path is single PUT, not multipart. Browser inspection/hash materializes file content in memory; no scalable huge-file path is implemented.

## Validation

Frontend lint and production build passed. The requested uv run pytest could not start because uv is unavailable on PATH; the existing .venv Python ran pytest after execution approval: 41 passed, with one existing Starlette/httpx deprecation warning. No real initiate/PUT/complete was attempted without legitimate identity mapping. No secrets or presigned URLs were inspected or exposed.

## Required next decision

Define how the frontend receives the real backend user/workspace/dataset identities and transitions local prototype scope. Then address or explicitly constrain uncertain-completion recovery. Phase 4 is not ready for approval; Phase 5 has not started.

