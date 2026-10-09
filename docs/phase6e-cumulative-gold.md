# Phase 6E: cumulative dataset Gold and analytics freshness

Dataset remains the processing boundary. Refresh is explicit and dataset-scoped, independent of delivery processing and trusted-state publication. No workspace orchestration, cross-dataset analytics, SQL analytics, Spark, authentication redesign, or automatic/scheduled refresh is introduced.

## Discovery

Legacy `process_gold` reads a successful delivery Silver object. Its base and marts are dataset-version/file/rule-scoped; `gold_runs` links a dataset-version-file association, DQ run and processing attempt. Semantic metadata lives in `gold_artifact_models` and `gold_artifact_columns`. Existing deterministic builders produce identifier summaries, categorical summaries, and day/month/year summaries. All operate on an input frame with explicit aggregation grain and record counts; none requires a delivery timestamp or file identity.

Legacy `/analytics/dataset-versions/{id}` overview, KPIs, charts, suggestions, workspace dashboard, query and Ask APIs previously selected delivery artifacts by dataset version. Their catalog could combine marts from different deliveries and pick the latest base. Those endpoints now resolve the owning dataset's current cumulative catalog; there is no delivery fallback. They now also enforce development principal/workspace/dataset ownership. Historical delivery Gold and processing metadata are preserved. No history/artifacts are rewritten.

## Domain and schema

Additive migration `0017_cumulative_dataset_gold.sql` creates `dataset_gold_runs`, source ownership checks, immutable successful history/pins, and `datasets.current_gold_run_id`. A run pins dataset, published trusted state, dataset/schema version, policy, source manifest SHA-256 and build version 1. It stores started/completed timestamps, REFRESHING/SUCCESS/FAILED lifecycle, allowlisted failure category, internal immutable manifest/checksum/catalog, and authoritative base rows. UNIQUE(dataset,state,build-version) makes retries reuse a run. Gold identity never uses a physical file.

Dataset freshness is separate: NOT_READY (no state), STALE, REFRESHING, FRESH, FAILED. State-head changes force STALE in the database trigger. FRESH requires owned successful Gold for the exact current head. Failed refresh retains the prior pointer/history. A crashed coordinator is detected using the authoritative PostgreSQL advisory lock and exposed as interrupted/needs attention without mutating database metadata during a read; explicit retry resumes its pinned run.

Migration 0017 was applied to development `ai_data_workspace` only after explicit human approval on 2026-10-09. Earlier migrations are unchanged. No existing state, delivery, policy, or artifact was backfilled or deleted. Existing datasets become refreshable after migration without importing delivery Gold.

## Build and mart audit

Resolve current published state on the backend, pin it, verify its immutable manifest/checksums/schema/ownership/lineage using the existing state validator, and project the policy business columns. APPEND uses all trusted appended rows; UPSERT uses the latest trusted values; SNAPSHOT filters activity to true. Inactive rows remain in trusted state/history. Operational lineage/DQ fields never become business measures. No cumulative artifact intentionally includes inactive history in V1.

Profile the resulting current business frame, then reuse the deterministic planner, mart builder and catalog builder. Identifier and categorical summaries are safe with their declared grain, including repeated identifiers in unkeyed APPEND. Time summaries require an actual datetime dtype; legacy name-only time heuristics are excluded rather than coercing arbitrary strings. Empty active state produces a valid empty base/marts. All mart record counts reconcile with current base rows. Planning is deterministic profiling, not AI business-rule selection.

Dependencies are dataset-state granularity. Every current-state version rebuilds all safe dataset-level artifacts. Nothing claims partition/key-selective execution. Same successful state/build version is reused without new objects. Failed build/validation/publication retries the cumulative Gold stage only; ingestion, rules, Silver and state application are never rerun. Candidate objects from unsuccessful retries remain immutable orphans for a future retention policy.

## Publication and races

Use conditional immutable S3 writes under a unique dataset/state/run candidate prefix. Read back/check checksums, Parquet readability, business schema, catalog names/order/types/grain, source pins, ownership, base rows and mart accounting before publication. Internal catalog stores dependencies/source state/run IDs and checksums; paths never enter public read models or AI context.

A PostgreSQL session advisory lock protects refreshes of the same dataset in a namespace separate from dataset processing, allowing delivery publication during a build. Final transaction locks the dataset row, records run SUCCESS, and updates pointer/FRESH only if the captured state remains the current head. Older builds may finish historically but never replace current analytics or mark a newer state fresh. Failure rolls back pointer changes. Retry after response loss returns the same durable successful run. Interrupted builds are recoverable after their session lock is released. No browser-provided lists, state IDs or paths are accepted.

## Read path and UX

Scoped endpoints: GET analytics, GET analytics/readiness, POST analytics/refresh, POST analytics/ask, POST analytics/query under `/workspaces/{workspace}/datasets/{dataset}`. Readiness returns safe counts, filenames, source/run identifiers, timestamps, freshness and recovery flags. Fresh dashboards reuse existing KPI/chart/suggestion calculations against the cumulative catalog; stale reads return empty metrics. Reads check freshness again after artifact IO. Structured/AI queries require fresh cumulative state and recheck the catalog before returning an answer. The existing query planner/catalog validator remain in use; no SQL Analytics or Ask redesign is added.

Overview and Analytics distinguish trusted rows from analytics rows, show last state update/build time and latest applied filename, and provide Refresh Analytics / Retry Analytics Refresh. Normal stale state uses neutral wording. Active-only SNAPSHOT semantics are explained. Backend REFRESHING is polled; requests are scoped and abort stale reads on navigation. No fabricated percentage, optimistic FRESH, or automatic rebuild occurs. Delivery-level processing history is preserved and no refresh failure changes a successful dataset update.

## Observability and limits

Logs use workspace/dataset/state/run IDs, source checksum, artifact count, freshness, duration and safe failure categories. No row contents, credentials or storage paths are logged. Endpoints resolve ownership with the existing temporary development identity; full authentication remains future work.

V1 is synchronous and full-frame Pandas. Memory and S3 IO scale with dataset size; no safe maximum/benchmark, streaming, partition-aware rebuild, durable worker queue, automatic orphan cleanup, or background scheduling is claimed. Source/build/catalog interfaces retain immutable version lineage for a future partition/Delta/Spark/warehouse implementation. Current dashboard uses existing deterministic KPI heuristics; approved business-specific metric definitions remain future work. Delivery Gold remains historical and is still built by the existing delivery lifecycle, clearly separate from cumulative freshness.

## Validation evidence

Backend tests use isolated PostgreSQL schemas and real Parquet with an in-memory immutable S3 adapter. Tests cover mode inputs, inactive filtering, empty state, first build, freshness transitions, idempotency, previous pointer retention, build/validation/post-write publication failures and retry, stale-head races, concurrent locks, interruption recovery, immutable history, checksums, ownership, archived delivery, and safe API responses. The live helper never applies migrations; synthetic business-rule answers require explicit human approval.

Final validation: the full database-enabled backend regression run passed 232 tests in 2514.79 seconds. Final targeted `uv run --no-sync pytest tests/test_cumulative_gold.py tests/test_analytics_api.py -q` with `RUN_DB_TESTS=1` passed 22 tests; the four subsequently added artifact-context/dependency tampering cases passed separately. Together these cover all 20 final cumulative-Gold tests and the six analytics API tests. The sole warning was the existing Starlette/httpx TestClient deprecation. Frontend `npm run lint`, `npm run build`, and all 11 lightweight suites passed. `git diff --check` and whitespace checks for new text files passed. Migration 0017 was applied to development database `ai_data_workspace` with explicit approval; no further manual pgAdmin change is required. No commit was created.

## Changed files

Backend:
- app/api/analytics.py
- app/api/dataset_analytics.py (new)
- app/api/processing_errors.py
- app/main.py
- app/processing/cumulative_gold.py (new)
- app/services/analytics_service.py
- app/services/dataset_analytics_service.py (new)
- app/storage/cumulative_gold_artifacts.py (new)
- migrations/0017_cumulative_dataset_gold.sql (new)

Frontend:
- frontend/src/features/datasets/AnalyticsReadiness.tsx (new)
- frontend/src/features/datasets/contracts/components/IncrementalPolicy.tsx
- frontend/src/features/ingestion/components/ProcessingResult.tsx
- frontend/src/pages/Dataset/Analytics/DatasetAnalyticsPage.tsx
- frontend/src/pages/Dataset/Overview/DatasetOverviewPage.tsx
- frontend/src/services/api/analytics.ts (new)
- frontend/src/services/api/incremental.ts

Tests, documentation and evidence:
- tests/test_analytics_api.py
- tests/test_cumulative_gold.py (new)
- frontend/tests/cumulative-analytics.cjs (new)
- frontend/tests/ux-consistency.cjs
- docs/adr-0001-incremental-dataset-foundation.md
- docs/phase6e-cumulative-gold.md (new)
- docs/phase6e-live-results.json (new)
- tools/phase6e_live_check.py (new; pre-existing tools preserved)
- screenshots/phase6e/analytics-mobile.png (new)
- screenshots/phase6e/analytics-stale.png (new)
- screenshots/phase6e/analytics-fresh.png (new)
- screenshots/phase6e/snapshot-analytics.png (new)

Live workspace 14 contains dedicated APPEND (20), UPSERT (21), and SNAPSHOT (22) datasets. APPEND built 150 rows/amount 2000, became stale after another delivery, and rebuilt 151 rows/2030. UPSERT rebuilt the latest three records/160. SNAPSHOT kept three historical state records but built only two active records/310. Repeated refreshes reused successful runs. The browser explicitly refreshed delivery 74 to 152 rows/2070, hid stale KPIs, disabled the running action, and retained Fresh after reload. The 390px mobile view had no horizontal overflow. An older workspace (13, dataset 18) remained refresh-required with no delivery-Gold fallback or count leakage. Seven successful cumulative runs and their 17 artifacts were verified for checksums/schema/counts. See `phase6e-live-results.json`; its milestone results describe the build at that moment, not a claim that earlier heads remain current.
