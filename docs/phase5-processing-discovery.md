# Phase 5 processing discovery

## Decision

Runtime integration is blocked by the existing rule-approval prerequisite.
The Phase 5 request explicitly says to stop and report a blocking domain/contract
mismatch. No runtime, schema, API, or frontend processing changes were made.
No real processing requests were submitted during discovery.

Bronze is not a complete pipeline. A new upload ends at `AWAITING_RULES`.
Backend rule suggestions must have resolved answers before finalization enables
Silver, and Silver additionally requires at least one active backend rule.
The frontend-local dataset contract is not those answers. Automatically answering
questions, skipping validation, or claiming success after Bronze would change
business semantics. Full rule management is explicitly outside Phase 5.

## Existing APIs and identifiers

All routes below are under `/files` in `app/api/files.py`:

| Method / route | Service | Meaning |
| --- | --- | --- |
| POST `/uploads/{upload_id}/process` | `start_processing` | Upload request ID starts Bronze only |
| GET `/dataset-version-files/{id}/business-rules/suggestions` | `get_business_rule_questions` | Questions derived from Bronze profiles |
| POST `/dataset-version-files/{id}/business-rules` | `submit_business_rule_answers` | Persist answers and effective rules for the dataset version |
| POST `/dataset-version-files/{id}/business-rules/finalize` | `finalize_business_rules` | Enable Silver only after resolving all questions |
| PATCH `/dataset-version-files/{id}/business-rules` | `update_business_rule_answer` | Edit an existing answer and version changed rules |
| POST `/dataset-version-files/{id}/silver/process` | `run_silver_processing` | Execute/reuse Silver for the association |
| POST `/dataset-version-files/{id}/gold/process` | `run_gold_processing` | Execute/reuse Gold for the association |

There is no existing combined Bronze/Silver/Gold trigger or dedicated processing
status/read endpoint in these routes. Each stage is synchronous and separately
invoked; no automatic chaining happens after Bronze or Silver.

The local batch's authoritative `uploadRequestId` can supply `upload_id`.
Local batch ID is not a backend execution identity. The upload resolves
`workspace_id`, `dataset_id`, and canonical `file_id` from PostgreSQL.
Bronze then resolves `dataset_version_id` and `dataset_version_file_id`.
Silver and Gold operate on that association ID, not the local batch ID.

## Dataset versions and delivery lineage

`dataset_service.resolve_dataset_version_by_id` checks dataset/workspace
membership, looks up `(dataset_id, schema_hash)`, and reuses the version or
creates the next numbered version. Version creation locks the dataset row;
database uniqueness protects dataset/schema and dataset/version number.
The pre-lock schema lookup still merits concurrent same-schema retry handling.

Schema fingerprinting normalizes column names and inferred types, sorts by
column name, and hashes the canonical schema. Column order is ignored.
Data-inferred dtype changes can therefore create different versions even when
the frontend accepts a CSV under the same local contract.

`assign_physical_file_to_dataset_version` reuses the unique
`(dataset_version_id, file_id)` association. Two logical uploads of identical
physical content in the same dataset/version share that association and its
Silver/Gold execution history. Processing attempts do not store upload request
identity. Delivery-specific execution lineage is therefore indirect, not a
one-to-one persisted batch/attempt mapping.

## Stage behavior

1. **Bronze:** `start_processing` reads canonical Raw through
   `run_bronze_stage`, parses CSV, profiles columns, fingerprints schema, writes
   Bronze Parquet, saves profiles/row and column totals, resolves the association,
   completes the physical-file attempt, and sets `AWAITING_RULES`.
   Raw is read, not moved or rewritten. A stored schema hash skips Bronze work.
2. **Rule approval:** suggestions come from physical profiles, but answers and
   active rules belong to a dataset version. Missing or `NOT_SURE` answers prevent
   finalization. Finalization sets `READY_FOR_SILVER`; effective rule changes
   increment the dataset version's `rule_version`.
3. **Silver:** requires a permitted association lifecycle and active backend
   rules. Applies cleaning, type/date transformations and validation, splits valid
   versus rejected rows, writes Silver/quarantine outputs, saves the DQ run and
   issue counts, completes the attempt, and sets `READY_FOR_GOLD`.
4. **Gold:** consumes the association's latest successful DQ/Silver output,
   publishes a Gold base and deterministic profile-driven marts, persists
   artifacts/semantic catalog and lineage, completes the attempt, and sets
   `SUCCESS`. Empty Silver data is rejected by the Gold processor.

Relevant implementations: `app/services/processing_service.py`,
`app/services/business_rule_service.py`, `app/processing/bronze_processor.py`,
`app/processing/silver_processor.py`, `app/processing/gold_processor.py`,
`app/processing/gold_planner.py`, and `app/db/file_repository.py`.

## Rule/contract mismatch

Backend supported answers configure `DATA_TYPE` (DECIMAL/DATETIME), `UNIQUE`,
`NOT_NULL`, `ALLOW_NEGATIVE`, `VALID_DATETIME`, and `DUPLICATE_HANDLING`.
They are profile-generated questions, with dataset-version rule persistence.
Frontend contracts instead define accepted structure, required fields, keys,
schema policy, and future load strategy. They do not supply business decisions
such as negative-number handling or all suggested duplicate/date rules.

Even answering every question with a rule-disabling answer is not a solution:
finalization can permit `READY_FOR_SILVER` while the processor rejects an empty
active rule set. A rule-approval policy must be decided explicitly before the
requested new-dataset, single-action flow can reach Gold safely.

Recommended prerequisite: define a minimal explicit approval step using existing
backend suggestions/answers, or approve a precisely scoped bridge for supported
contract fields with explicit handling of unresolved questions. Do not infer
unanswered business rules or implement another contract system.

## Idempotency, concurrency, and recovery findings

- Bronze reuse is keyed by the physical file's stored schema hash. Both new and
  reused Bronze paths unconditionally set the association to `AWAITING_RULES`.
  Repeated starts can regress an already successful association. The schema hash
  is saved before all profiling/context work finishes, so partial Bronze failures
  also need recovery review before treating the hash alone as completion proof.
- Silver reuses a successful DQ run for the association/current rule version.
  Attempt numbers are physical-file scoped for Bronze and association scoped for
  Silver/Gold (an existing Silver comment incorrectly says file scoped).
- Gold reuses a successful publication only for the same association and exact
  source DQ run after checking expected artifacts and semantic catalog. An
  incomplete publication reuses its existing Gold run during recovery.
- PostgreSQL's partial unique index permits only one `PROCESSING` attempt per
  physical file, across datasets. Bronze and Gold also check active attempts.
  Silver relies on the database constraint. This is broader than a delivery
  lock, and collision handling needs a safe API response rather than an uncaught
  database error. Status updates outside stage try blocks can leave active
  attempts behind after infrastructure failure; no stale-attempt recovery was
  established by this review.
- Stage failures persist failed attempts; Silver/Gold set `SILVER_FAILED` or
  `GOLD_FAILED` and allow stage-specific retries without reupload. Bronze failure
  marks the physical file failed. Stage services do not form a complete safe
  upload-level resume orchestrator today.
- Existing processing/rule routes lack `get_current_user` ownership validation.
  `start_processing` checks context/file presence but not completed upload status
  or principal ownership. Membership checks do not replace ownership checks.
- Existing responses contain internal object locations and raw exception text.
  A future frontend integration needs an allowlisted public response and safe
  error mapping, not direct rendering of those payloads.

## Metrics and frontend state

Backend produces Bronze input row/column totals, Silver total/valid/rejected
counts and per-rule issue counts, and Gold output/artifact row counts.
Column-profile duplicate counts are not delivery-level duplicate row outcomes.
No authoritative updated-row or delivery duplicate-row metric was found.
Those must remain null/unavailable; none were synthesized during this review.

The current Processing page remains a local batch list without a Process action.
Its local batch storage is scoped by API/user/workspace/dataset. It does not
recover backend execution status on refresh. Files remain source metadata.

## Validation

Four focused checks in `tests/test_processing_rule_gate.py` protect unresolved
rule finalization, the Silver lifecycle gate before creating attempts, and the
no-active-rules rejection before publishing outputs. They mock persistence/S3.
They do not certify the requested upload-to-Gold integration.

Validation results: backend pytest passed 59 tests and skipped five optional
PostgreSQL tests requiring `RUN_DB_TESTS=1`, with one existing Starlette/httpx
deprecation warning. `uv` was unavailable; the existing `.venv` Python was used.
Frontend lint, production build, and all four lightweight suites (contracts,
ingestion batches, file-content duplicates, and backend integration) passed.
`git diff --check` passed. No live integration run was attempted while the rule
policy remains unresolved; these passes do not establish Phase 5 readiness.
