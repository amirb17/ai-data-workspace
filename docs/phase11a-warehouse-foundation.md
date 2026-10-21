# Phase 11A — Warehouse control-plane foundation

Implementation review and approved development migration gate, 2026-10-21. Baseline HEAD: `20e96a5c0fe21c65428c354e2d6ffbff3d4ce047` (`docs: define phase 11 warehouse architecture`). Backend metadata only; the application has no warehouse routes, clients, jobs or consumers. Migration 0029 is now applied and verified in the local development application schema `ai_data_workspace.public`; warehouse functionality remains OFF.

## 1. Pre-existing changes

All 239 unrelated initial porcelain status entries were excluded from this work. They include the modified Phase 8A HTML fixture, deleted historical tests/CSV fixtures, untracked historical reports, screenshots and tooling. The exact initial path/status inventory is in the local generated `docs/phase11a-validation.json`, intentionally left unstaged. No deleted test was restored or historical tool/report changed, and none of those unrelated changes is included in the scoped Phase 11A commit.

## 2. Migration choice

Inspected the actual migration tree and recent Git history. Root forward migrations run from 0001 through 0027; `migrations/pending/0028_ownership_adoption_events.sql` and its rollback reserve 0028. The next safe identifier is **0029**, used by `migrations/0029_warehouse_foundation.sql`.

The new migration is transactional and additive. It creates only warehouse tables, indexes, trigger functions and triggers. It does not alter existing columns, business rows, pointers, existing user-defined functions or user-defined triggers. Its foreign keys create PostgreSQL-managed referential-integrity triggers on referenced parents, as expected. It works both before and after pending 0028; it does not apply or depend on 0028. Existing root-glob migration fixtures include 0029 automatically.

An optional empty-only rollback is kept outside the forward migration glob at `migrations/rollback/0029_warehouse_foundation.sql`. It acquires exclusive locks, refuses rollback if any warehouse row exists, and drops only these new objects. Retaining metadata and disabling the flag is the normal rollback strategy. A second forward application fails rather than silently masking an inconsistent partial installation.

## 3. Tables created

| Table | Responsibility |
|---|---|
| `warehouse_models` | Stable workspace-scoped logical object and key namespace. |
| `warehouse_model_versions` | Immutable version/configuration/key contract with approval provenance. |
| `warehouse_model_sources` | Explicit version-to-dataset mappings; one PRIMARY and optional DEPENDENCY sources. |
| `warehouse_resources` | Backend-only physical provider binding, environment and generation. |
| `warehouse_operations` | Immutable intended operation, approved version/resource/environment pins and initiator. |
| `warehouse_runs` | One attempt per row, status/times/failure code and historical actor. |

There are no source-state/run-source vectors, checkpoints, outbox, accepted-change manifests, publication pointers or execution tables. Source configuration mappings are not falsely presented as exact execution source pins. Later phases can add those vectors with foreign keys and immutable pin validation.

## 4. Constraints, indexes and triggers

- `BIGSERIAL` metadata IDs, positive versions/generations/attempts, `TIMESTAMPTZ` audit timestamps and user FKs follow repository conventions.
- Workspace/model/version composite FKs prevent resource and operation bindings from crossing model/version/workspace scope.
- Unique `(workspace_id, normalized_name)` prevents duplicate logical names; archive retains the identity/name. A UUID key namespace belongs to the stable model and never changes with owner/version/resource generation.
- Unique `(warehouse_model_id, version_number)` plus a partial unique index permits at most one APPROVED version. Approval replaces the prior approval in one transaction under a model lock; no current-version pointer is introduced.
- Source mappings are unique per version/dataset; a partial index permits one PRIMARY, with any explicitly declared DEPENDENCY mappings. Dataset FK and trigger enforce same-workspace, same-current-owner, active dataset membership at registration.
- Resources are unique per model/provider/environment/generation. The full validated `(provider, physical_reference)` is globally unique, preventing a physical table from being registered under another tenant, generation or environment.
- Operations use a composite FK to the exact model/version/resource/environment. Runs are unique per operation/attempt; a partial unique index allows one PENDING/RUNNING attempt.
- `warehouse_guard_history` protects logical identity, actor fields, version configuration, approved source history, resource bindings, operation pins and terminal runs. `warehouse_guard_sources` controls draft-only source insertion/deletion. `warehouse_guard_insert` enforces initial states and retry sequencing. `warehouse_no_truncate` protects all six tables against ordinary TRUNCATE.
- Explicit source-dataset, model-operation and operation-run-history indexes support the expected metadata queries, in addition to primary/unique indexes.

Dataset membership is checked when a source is registered and revalidated when versions are read/approved or resources/operations are registered or attempted. No trigger is added to the existing datasets table to change source pipeline behavior. If another existing service moves or archives a mapped dataset, further use fails closed until a new valid model version is configured. Direct SQL callers are trusted backend code; application authorization is enforced by service entry points, not PostgreSQL row-level security.

## 5. Logical model schema

`warehouse_model_id`, `workspace_id`, `display_name`, `normalized_name`, `key_namespace`, `status`, `created_at`, `created_by`, `archived_at`.

Names use trimmed printable text with Unicode NFKC/casefold/whitespace normalization for uniqueness. Normal input cannot supply physical IDs, owner or actor fields. `created_by` comes from the authenticated actor and never authorizes access. The namespace is a generated UUID **metadata identity**, not a warehouse surrogate key or key-generation implementation.

## 6. Version lifecycle

`warehouse_model_version_id`, workspace/model scope, `version_number`, `configuration_version=1`, `key_algorithm_version=1`, `key_strategy=DETERMINISTIC`, `key_storage_type=STRING`, status, creation actor/time and review actor/time.

Models and versions start DRAFT. A version requires a PRIMARY source before approval. Approval records the current reviewer and atomically archives the previous approved version. Model lifecycle is DRAFT → APPROVED → ARCHIVED (or DRAFT → ARCHIVED). Version lifecycle is DRAFT → APPROVED → ARCHIVED (or DRAFT → ARCHIVED). Archived identities remain readable to their current owner.

Version configuration/identity is immutable even while DRAFT; create a new numbered version for configuration changes. Source mappings may be explicitly added/removed only during DRAFT. The current contract intentionally has no arbitrary JSON model configuration, FACT/DIMENSION/SCD fields, grain guessing or incomplete execution semantics. Approval in 11A means approval of the metadata/source contract, not readiness to execute a warehouse model. Future execution needs additional reviewed typed configuration.

Version numbers are allocated under the model row lock with an `expected_version` concurrency check. Racing callers cannot both create the next version or silently overwrite approval. No source or Gold current pointer is involved.

## 7. Source mapping schema

`source_mapping_id`, workspace/model/version scope, `source_dataset_id`, `purpose` (PRIMARY or DEPENDENCY), `created_at`.

The mapping is deliberately a separate relation instead of a permanent one-model/one-dataset assumption. V1 requires exactly one PRIMARY at approval, while allowing additional same-workspace dependencies. Service checks use the current dataset/workspace owner and ACTIVE source status. No source data is read or changed. A fresh version receives its own explicit mappings; they are not silently copied from an old approval.

## 8. Provider resource schema

`resource_id`, workspace/model/version scope, `provider`, `environment`, `location`, positive `generation`, `physical_reference`, status, creation actor/time.

Only planned provider BIGQUERY is accepted. Environments are `development` and `production`, following `APP_ENV`. Production location is locked to `asia-south1`. Development location is explicit metadata. Resources start REGISTERED; they are not provisioned, verified in the cloud or marked usable for execution. A binding is immutable; archive rather than overwrite.

`physical_reference` is tightly bounded JSONB with exactly `reference_version=1`, `project`, `dataset`, `table`. Pydantic and SQL both validate structure/types/identifier syntax; unknown keys, credentials, missing values, NULL identifiers and unsupported reference versions are rejected. Project/dataset/table values are **backend-only registration data**. No endpoint accepts this contract, and service read/list/registration responses omit the physical reference. Environment must match the enabled process's APP_ENV for registration and operation creation/attempts; development cannot be bound as production.

`WarehouseProvider` contains only identity and `supports_execution=False`. It has no cloud client, loader, SQL or credential interface. DuckDB is not registered as a remote provider.

## 9. Operation/run schema

`warehouse_operations` pins workspace, model/version, resource generation via resource ID, environment, operation type, reason, creation actor/time. Types: INITIAL_LOAD, INCREMENTAL, REPLAY, BACKFILL, REBUILD. These are vocabulary, not implementations; no source vectors, time ranges or source acceptance are invented.

`warehouse_runs` stores operation/workspace, attempt number, status, creation actor/time, start/completion times and bounded failure code. All configuration/resource pins come from its immutable operation. A retry creates a new run for the same operation after a FAILED predecessor; one open attempt is allowed. A successful operation cannot be retried as though it failed; an explicit new operation is a separate intent.

## 10. Ownership and authorization

Every service call checks the current workspace owner while holding a PostgreSQL share lock. Mutations also lock the logical model, serializing version edits/approvals. Guessed model/version/resource/operation/run IDs are resolved within their authorized parent scope. Unrelated workspace ownership produces 403; a foreign ID within an otherwise authorized scope produces 404. No physical reference appears in errors/results.

Owner transfer does not rewrite `created_by`/`reviewed_by`. The new current owner can read/manage metadata; the creator loses access once no longer owner. Models do not duplicate mutable owner identity. Source membership is revalidated after transfer using current dataset ownership.

Existing workspace metadata APIs allow current-owner reads of archived workspaces. Warehouse reads preserve that policy; new warehouse mutations require ACTIVE workspace and nonarchived model. No existing workspace or dataset archival policy is changed.

## 11. Immutability and attempt lifecycle

Runs transition PENDING → RUNNING → SUCCEEDED or FAILED. Timestamps and failure fields must match status; direct terminal creation, skipped transitions, terminal rewrites/deletes and TRUNCATE are rejected. A started timestamp cannot change once recorded. Terminal attempts remain unchanged when another attempt is added.

`record_transition` is an internal metadata primitive for future worker integration and tests. It does not execute or verify a real warehouse job; a synthetic SUCCEEDED test row is only a lifecycle test. The same applies to provider registration: metadata is not proof of a real physical resource. No worker or public mutation endpoint exists in 11A.

## 12. Feature flag

`app/warehouse/config.py` follows existing environment-reading conventions without changing shared configuration/startup code. `WAREHOUSE_ENABLED` defaults to `false`. All warehouse service entry points reject disabled access before opening a database transaction. While disabled, no provider/environment/credentials are required. Enabling metadata accepts planned provider `WAREHOUSE_PROVIDER=bigquery` and APP_ENV development/production; unsupported settings fail closed.

**The delivered feature remains OFF.** No environment file is edited. Setting the flag true enables only these internal metadata services; it still cannot execute jobs because there is no execution implementation or API registration. No application startup path imports or depends on these modules.

## 13. Files changed

New files only:

- `migrations/0029_warehouse_foundation.sql`
- `migrations/rollback/0029_warehouse_foundation.sql`
- `app/warehouse/{__init__,config,provider,access}.py`
- `app/schemas/warehouse.py`
- `app/db/warehouse_repository.py`
- `app/services/warehouse_model_service.py`
- `app/services/warehouse_resource_service.py`
- `app/services/warehouse_run_service.py`
- `tests/test_warehouse_foundation.py`
- `docs/phase11a-warehouse-foundation.md`

Generated validation evidence (`docs/phase11a-validation.json` and task-local `tmp/phase11a-*` records) is intentionally left unstaged. The scoped commit contains the 13 source, migration, test and documentation files above; no generated inventory or historical evidence is included.

The Phase 11 architecture document remains unchanged; the foundation uses its version/source/provider boundaries. The approved provider/location and metadata-only scope are recorded here. No frontend, dependency manifest, lockfile, application router, existing migration or existing pipeline file changes.

## 14. Focused tests

Run with `RUN_DB_TESTS=1` using `uv run --no-sync pytest tests/test_warehouse_foundation.py -q`. The no-sync option uses existing installed dependencies without package installation. Local PostgreSQL access requires execution outside the restricted socket sandbox; test code uses generated `test_warehouse_<uuid>` schemas and drops only those generated schemas in cleanup.

Coverage includes disabled-before-database behavior, strict configuration/provider contracts, no cloud/storage/execution imports/routes, names/version concurrency, draft mapping removal, approved identity protection, ownership transfer, foreign list/read/mutate/ID guessing, cross-workspace sources/resources, cross-model isolation, one approved version, environment separation, strict physical JSON, terminal run history/concurrent retry and migration preservation/rollback. Final result: **33 passed, no skips or failures**. Evidence paths are recorded in the validation JSON.

## 15. Backend regression

The final development/commit gate reran the available working-tree suite with `RUN_DB_TESTS=1`: `uv run --no-sync pytest -q --tb=short`. Result: **79 passed, 3 skipped**, including the final 33 warehouse tests. The focused warehouse run independently passed **33 tests**. No source code or migration changes were needed during this gate.

The skips are the existing ownership-compatibility and semantic-deadline modules (missing deleted historical PostgreSQL fixtures), and the Model summary PostgreSQL test whose profile fixture is absent. These pre-existing files were not restored or patched. This is the available working-tree regression, not a claim that the complete historical snapshot suite ran. Existing Authlib/Starlette httpx deprecation warnings do not affect results and no dependency was added to address them.

## 16. Migration verification

Integration tests apply the full forward schema and 0029 only inside disposable schemas, then also verify compatibility with pending 0028. On populated synthetic legacy tables they perform two empty rollback/reapply cycles and compare every existing row, column definition, constraint, index, user-defined trigger and pre-existing function. All created indexes must be valid/ready and the six expected tables present. Populated rollback is refused without losing metadata.

The initial verification did not connect to AWS or Google or apply migrations to public. The subsequent explicit user-approved development gate applied exactly the reviewed 0029 statements in a driver-owned transaction so pre-commit verification could veto the write; only the file's outer BEGIN/COMMIT orchestration was handled by the driver. The approved migration file itself was not edited. No production migration ledger is inferred.

### Approved development application

The target was confirmed as loopback PostgreSQL, database `ai_data_workspace`, schema `public`, `APP_ENV=development`, warehouse disabled. Before migration there were 49 application tables and none of the six warehouse tables. Migration 0028's `ownership_adoption_events` table was present with one row and remained unchanged.

During the migration transaction, SHARE locks froze existing business writes while all 49 existing tables were compared by row count and deterministic row fingerprint. Their columns, constraints, indexes, user-defined triggers, functions and sequence definitions were also compared before/after. All were unchanged. The resulting 55-table schema was checked again after commit.

| Warehouse table | Initial and post-smoke rows |
|---|---:|
| `warehouse_models` | 0 |
| `warehouse_model_versions` | 0 |
| `warehouse_model_sources` | 0 |
| `warehouse_resources` | 0 |
| `warehouse_operations` | 0 |
| `warehouse_runs` | 0 |

The new object inventory exactly matched a separately instantiated reference schema: 61 columns, 113 PostgreSQL constraints (including NOT NULL catalog entries), 23 indexes, 17 user-defined triggers, four functions and six sequences. All constraints were validated, all indexes valid/ready and all user triggers enabled. Verification left no synthetic warehouse rows in development.

Representative preserved counts: 15 workspaces, 33 datasets, 90 upload requests, 50 delivery applications, 50 dataset states, 15 cumulative dataset Gold runs, 65 delivery Gold runs and 144 Gold artifacts. The complete comparison also covers profile/semantic/relationship/metric/analytics/Ask/SQL tables and the remaining Gold metadata tables, not only these counts.

Startup/import and OpenAPI generation passed with `WAREHOUSE_ENABLED=false`, no `google-cloud-bigquery` installation, and Google Cloud credential/project/dataset environment settings removed. A disabled service call was verified to stop before opening a transaction; trusted internal repository reads saw empty warehouse metadata. No provider execution, cloud resource creation, source regeneration or warehouse operation occurred.

## 17. Secret and diff checks

No repository secret-scanner configuration/tool was found. A targeted scan of all new source/migration/document files checks private-key blocks, common AWS/Google API key patterns, service-account private-key payloads and token patterns without printing any candidate value. Results and `git diff --check` are recorded in the validation JSON. Synthetic resource names and failure codes in tests are not real cloud identifiers or credentials.

Status and file-fingerprint comparisons preserve the unrelated initial changes. New-file whitespace is checked separately because ordinary Git diff does not include untracked content. The approved final commit gate stages only the 13 explicit paths, reviews the cached diff and leaves generated evidence and all historical changes unstaged.

## 18. Pipeline compatibility

No existing application source file was changed, no new route/startup hook registered, and no source-processing service imports the warehouse layer. Upload, Raw/Bronze/Silver/DQ, APPEND/UPSERT/SNAPSHOT, trusted-state publication, profiles, semantic understanding, relationships, metrics, analytics, Ask and SQL retain their code paths and pointers. Available backend regression and the schema preservation checks support that bounded compatibility claim. There was no live S3 or deployed application walkthrough in this phase.

## 19. Gold compatibility

`dataset_gold_runs`, `gold_runs`, `gold_artifacts`, `gold_artifact_models`, `gold_artifact_columns` and their existing constraints/indexes/user triggers/functions/rows remain unchanged. No consumer resolves warehouse models, and there is no warehouse current pointer/freshness or fallback. Database preservation checks include the existing Gold schema; current Gold artifacts were not read, rebuilt or modified.

## 20. Remaining limitations

No SCD/fact/dimension algorithms, deterministic surrogate-key hashing, warehouse API/management UI, provisioning, credentials, Google packages, worker, source manifests/outbox, checkpoints, reconciliation, publication, source scaling or consumer migration. No historical state is fabricated. Known-key inferred membership and missing-key default rejection remain approved future policies, not executable settings in 11A. Production asia-south1 is a metadata constraint only.

Exact source-state/application pins, backfill ranges, run reconciliation proof and future worker lifecycle/archive interaction need later phase contracts. Metadata services assume an authenticated application user object supplied by trusted backend code; there is no external API that independently authenticates warehouse callers. Physical resources remain registration-only, unverified against a real provider. Dataset membership drift fails closed instead of modifying existing dataset services. The complete historical deleted-fixture regression remains outside this working-tree validation.

## 21. Approval readiness

The Phase 11A code/design was approved, and the development migration, focused tests, available regression and preservation checks passed. The scoped Phase 11A commit completes this phase. Warehouse functionality stays disabled; no deployment, push, BigQuery provisioning or 11B work is included. The final scoped commit checks and commit identity are recorded in local generated validation evidence, left unstaged. The repository is ready for a separately requested 11B implementation, with the historical-fixture regression gaps above explicitly retained.

## 22. Recommended exact 11B scope

Add reviewed, typed FACT/DIMENSION configuration, explicit fact grain/business-event identity, ordered composite business keys, effective-time/timezone declarations, tracked/nonhistorical attribute metadata, missing-member/history-coverage policy and versioned deterministic STRING surrogate-key generation with canonicalization test vectors. Preserve stable model key namespaces and require new versions for semantic changes.

Keep 11B provider-neutral and local/test-only. Do not start SCD execution, BigQuery integration, loaders, accepted-change manifests, checkpoints/publication or consumer migration. Those belong to subsequent approved phases. No 11B work was started here.
