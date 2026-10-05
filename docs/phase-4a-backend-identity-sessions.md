# Phase 4A backend identity and upload sessions

Apply migrations/0007_identity_upload_sessions.sql after existing migrations before using the new APIs. Migration is supplied, not applied to the application database.

Enable a single development principal explicitly:

```
APP_ENV=development
ENABLE_DEV_IDENTITY=true
DEV_USER_KEY=<stable local developer key>
DEV_USER_NAME=<display name>
```

GET /me creates/reuses a real PostgreSQL users row and returns user_id, owner_key, display_name, identity_mode. This is temporary development identity, not authentication. All callers share this principal. Keep this mode on a trusted local development server; it is disabled outside explicitly enabled development. There is no client-selected user ID. Legacy owner strings are not automatically claimed or migrated.

GET/POST /workspaces, GET /workspaces/{id}, GET/POST /workspaces/{id}/datasets, and GET /workspaces/{id}/datasets/{id} return PostgreSQL IDs and enforce this principal's workspace ownership. Supplied owner fields are accepted for payload compatibility but do not grant access. Dataset versions remain unchanged and are resolved during future processing. Phase 4B must use returned IDs rather than prototype IDs; frontend prototype creation is intentionally unchanged in 4A.

POST /files/initiate accepts workspace_id, dataset_id, file_name, file_size and optional content_type. Optional legacy user_id must match /me. Returns upload_request_id, session_status=INITIATED and temporary presigned information. Signing is single PUT for 900 seconds. Presigned URLs and object keys must stay transient, never be logged or placed in browser persistence/UI.

POST /files/complete now accepts {"upload_request_id": <id>} only. This intentionally replaces arbitrary client object-key completion. The server uses stored context/key/size/type, locks the request row, verifies bytes, reuses/promotes canonical physical content, and commits the result to the same upload_requests row. Completed responses report session_status=COMPLETED while preserving upload_request.status=UPLOADED for existing processing compatibility. Retries return the saved result without re-reading removed staging or inserting a logical request. Failures store FAILED and retain staging for retry. Cleanup failure after completion does not undo success; S3 lifecycle management should remove orphan staging copies.

No presigned refresh endpoint is added: an expired unused URL needs a new initiated session. Completion can retry by ID when bytes already exist in staging. An uncertain initiate response can leave an unused session; initiate itself is not keyed for idempotency. There is no cross-resource transaction with S3, but deletion happens only after database completion commits. Physical registry writes remain deduplicated; retry after a registry insert/result failure reuses that file.

CORS is enabled only under APP_ENV=development, for http://localhost:5173 and http://127.0.0.1:5173, without credentials. S3 bucket CORS is independent and unchanged.

Validation: regular pytest uses mocked repositories/API identities; RUN_DB_TESTS=1 additionally exercises an isolated temporary PostgreSQL schema with the baseline and 0007, real repository writes/row locks, and mocked S3. The temporary schema is dropped afterward. Tests do not apply migrations to public/application tables.

Known limitations: development identity is not production auth; legacy data ownership needs an explicit future transition; API IDs remain backend-only until 4B. Session completion holds a DB row lock while verifying S3 bytes, appropriate for this single-PUT phase but not optimized for very large files. Existing processing/analytics APIs are not a complete authenticated surface. No processing, frontend upload, batches, or new dataset-version execution is added.

Validation outcome: all 60 backend tests, including the opt-in isolated PostgreSQL suite, passed (one existing deprecation warning) using the existing virtualenv (uv unavailable). PostgreSQL checks cover durable initiate, repeated/concurrent complete, canonical reuse, failure recovery, metadata mismatch and cleanup failure. No real S3 upload was executed.

