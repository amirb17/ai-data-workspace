# Phase 7A: deterministic trusted dataset evidence

Dataset remains the profiling/processing boundary. Workspace and owner scope access and future aggregation. No AI/LLM calls, domain/entity classification, relationship inference, metric discovery, workspace orchestration, or new dependencies are added. Existing APPEND/UPSERT/SNAPSHOT, delivery processing and cumulative Gold remain intact.

## Existing metadata discovery and reuse

Bronze `dataset_profiles` / `dataset_profile_summaries` already hold file-scoped counts, physical types, null/distinct/duplicate counts, extrema and negative counts. Schema fingerprints and dataset versions already identify schema; neither an old Bronze profile nor a delivery Silver/Gold result represents cumulative current state. These are preserved and are not repurposed as current evidence.

The new service reuses `dataset_state_versions`, the current published dataset pointer, immutable state/application/policy pins and the existing checksummed `load_pinned_state` / `IncrementalArtifacts.validate_state` reader. No second source reader, file upload flow, state executor or S3 profile copy is introduced. Policy schema columns, ordered business keys, load strategy, event-time column, snapshot coverage and source application's effective timestamp are authoritative metadata. Required-field evidence uses the approved rule snapshot pinned to the state's source application; it does not use mutable draft rules. Rule version is recorded, and older state records retain their existing per-record rule lineage.

Cumulative Gold already has active-only row counts, immutable build/catalog lineage and heuristic dimension/measure roles. Profiling does not require a Gold rebuild, reuse stale catalog statistics, or turn those heuristic roles into authoritative semantic labels. Counts and statistics are computed on the validated current business frame because the requested evidence must exist even when analytics is stale/unbuilt. Schema identity, policy, ownership and rule definitions are referenced/reused rather than independently inferred.

New evidence includes normalized comparison names/tokens, exact null/non-null/cardinality statistics, numeric aggregates, typed time range/timezone, string lengths, bounded categorical frequencies, structural pattern counts, potential sensitivity hints, and explainable identifier candidate components.

## Storage and immutable publication

Migration 0018 adds `semantic_profiles` (one run per dataset/state/algorithm, owner/workspace/source schema/policy pins, profile version, status, timestamps, safe failure category and small summary) and `semantic_column_evidence` (one bounded evidence object per ordered column). It adds `datasets.current_profile_id` and `profile_status`. It does not copy raw source values into profile metadata. PostgreSQL foreign keys, ownership/source-pin triggers, immutable successful header/column evidence triggers, and owned/current pointer validation protect history.

The database stores source manifest SHA-256 privately for integrity pins; APIs never expose it. Profile ID and per-dataset profile version remain stable for retries. Algorithm version 1 is pinned on creation. A later algorithm version creates new evidence; readiness distinguishes the requested algorithm from the historical pointer's built algorithm. Successful profiles and their evidence cannot be changed or removed.

The migration is additive. Existing histories and policies are not rewritten or backfilled. Existing published states without a profile derive STALE on read; an unpublished dataset is NOT_PROFILED. New trusted-state publication forces STALE through the dataset trigger. The previous successful pointer remains historical.

## Source and deterministic evidence

Only the authoritative published state is read, with its original owned application, exact policy and checksummed business/operational schema. APPEND profiles cumulative rows; UPSERT profiles current representations. SNAPSHOT filters validated Boolean `_datarise_active` flags, reports active/inactive counts and profiles only active business rows. Operational lineage is excluded from business column evidence. An empty active state has real zero counts and unavailable ratios/extrema, not invented statistics.

Canonical types reflect the actual trusted frame, not name guesses or an assumed conversion result. For example, existing Silver may retain integer dtype for whole-valued numbers despite a DECIMAL rule; profiling truthfully reports INTEGER. Nullable is explicitly *observed* (`nullable_basis=observed_current_state`), separate from the approved required-field flag; an empty state has unknown (null) observed nullability. Object-backed date timezone awareness is also unknown rather than guessed false. Distinct count excludes nulls; uniqueness ratio divides distinct by non-null count; null ratio divides missing by total current profiled rows. Undefined ratios are null. Cardinality is EXACT.

Numeric statistics include min/max, mean, median, population standard deviation, zero/negative counts and ratios. Aggregates explicitly declare float64 precision; nonfinite/unavailable aggregate results are null. Decimal extrema and integer extrema outside JavaScript's exact numeric range use exact textual representations. Numeric types/keys are not called revenue, profit, quantity or measures. Typed datetime columns expose ISO extrema and known dtype timezone; no strings are parsed into dates and no timezone/business meaning is guessed. Name normalization uses NFKC, CamelCase splitting, separator removal/lowercase tokens; original names and actual schema are unchanged.

Identifier candidate components disclose configured business-key membership, ID/UUID name token, observed uniqueness/null ratios, repeated-value count and physical/canonical type. Configured ordered keys are authoritative; other candidates require an ID-like name plus at least 98% non-null uniqueness and no observed missing values. Candidates never approve primary keys. Event-time configuration is independent of a typed datetime candidate. Monotonicity is intentionally omitted because state row order is not guaranteed business-time order.

## Safe-value policy

V1 is conservative: **no raw samples, categorical labels, text extrema, value hashes or source paths** in public evidence. Text columns with at most 100 distinct non-null values expose at most five descending frequency ranks/counts/percentages with `value=null`; ties cannot leak labels. Boolean distributions expose aggregate true/false counts. Category percentages use non-null rows. No unbounded value list is built.

Patterns include email-like, UUID-like, phone-like, URL-like, numeric string, code-like, fixed length and IP-like. Regex/IP matching is limited to values of at most 512 characters; evaluated/skipped counts are recorded, not fabricated as complete coverage. Length statistics still cover the full text column. Email/phone/IP matches produce potential hints only. Sensitive name tokens and personal-name naming hints are explicitly low confidence. Numeric/date extrema/statistics are suppressed for fields with sensitivity hints. No government-ID or card validation/classification is claimed. Potential patterns can have false positives/negatives and require review.

## Lifecycle, locks and races

NOT_PROFILED / STALE / PROFILING / READY / FAILED are independent of Gold freshness and delivery status. An explicit refresh resolves the current head server-side. A separate PostgreSQL session advisory lock coordinates profiling of one owned dataset while allowing delivery updates. The unique source-state/algorithm key prevents duplicate profile runs. Interrupted PROFILING metadata is exposed as retryable when no coordinator lock remains; GET does not mutate database state.

Evidence is computed outside the publication transaction. Final publication locks the dataset row and atomically inserts column records, marks the profile READY, and moves the current pointer only if the source head still matches. A completed older profile remains historical and cannot mark a newer state READY. Reads check readiness again after column reads and withhold stale evidence. Failures preserve the previous pointer; retries reuse the same pinned profile and do not rerun ingestion, Silver, application execution or Gold. Publication failure rolls back both evidence and pointer writes.

## API, UX and future handoff

Routes follow `/workspaces/{workspace}/datasets/{dataset}/profile`: GET evidence, GET `/readiness`, POST `/refresh`. No state IDs, raw results or paths are accepted as execution inputs. Every operation resolves the existing development principal and validates workspace/dataset ownership. `SemanticEvidenceBundle` / `ColumnEvidence` define the safe typed read/handoff contract; no AI invocation or sample handoff exists. The structured profile table, READY workspace index and dataset pointer allow a later owned-workspace gather of matching current profiles without cross-dataset inference now.

Dataset navigation adds Data Profile. It shows current row/column and candidate counts, ordered configured key/event/effective-time metadata, actual column types, missing/uniqueness statistics, numeric/time ranges and careful sensitivity wording. Stale/failed profiles hide column evidence and expose Refresh Profile / Retry Profile. Busy actions are disabled, backend readiness is polled, scoped reads are aborted on navigation and resource/content identity includes workspace plus dataset. Network errors hide cached evidence. No fake percentage or optimistic READY is displayed. A backend request may finish after navigation; only its owned persisted result is retained. Existing routes and responsive wrapped navigation remain intact.

## Observability and V1 limits

Logs include owner-scoped workspace/dataset/state/profile IDs and versions, algorithm, counts, duration, lifecycle and safe failure category. No raw row values, category labels, private hashes, paths or credentials are logged by the profiler.

Pandas reads and validates a full trusted frame and computes exact distinct counts in memory (cardinality memory scales with rows). Numeric statistics and text/pattern scans scale with rows × columns. Top-K output and low-cardinality value counting are bounded; profiles use separate per-column metadata rows rather than raw-data-sized JSON. No safe workload maximum or benchmark is established. No sampling/approximate statistics, streaming, partition-aware execution, Spark, background worker queue, scheduling, automatic refresh, cross-dataset reasoning or historical profile browser is implemented. Float64 aggregates cannot promise arbitrary Decimal precision. Later execution adapters must preserve the versioned evidence contract and declare approximation/coverage changes.

## Development migration and live evidence

User explicitly approved 0018 and dedicated synthetic rules. Before applying, verified `current_database()=ai_data_workspace`, public schema, both new tables absent and both dataset columns absent. The first transactional verification rolled back because its trigger query counted concurrent disposable test schemas. The corrected schema-scoped application committed once. Post-checks verified two tables, two dataset columns, four protection triggers and 36 new-table constraints. Existing counts remained 18 datasets, 29 trusted states, 7 cumulative Gold runs at migration time. No existing real/dev rules, contracts, policies or business data were changed.

Dedicated workspace 15: Orders Profile Check dataset 23 and Healthcare Profile Check dataset 24, uploads 75–78. Approved Orders rules: order_id required/unique, customer_id required, order_date DATETIME, amount DECIMAL/no negatives. Approved Healthcare rules: patient_id required, admission_date DATETIME, treatment_cost DECIMAL/no negatives. UTC timestamps were used. The helper stopped before extra Orders answers until explicit additional approval arrived.

Orders initial profile: 3 rows/state 1/profile 1. New delivery: stale with no summary/columns, refreshed to 4 rows/state 2/profile 2. A further delivery was refreshed through the browser: disabled Profiling action, then 5 rows/state 3/profile 3, persisted after reload. Healthcare: 3 rows, typed admission date, numeric statistics, patient identifier evidence, email pattern/sensitivity warning, no raw email/identifier samples or domain label. Four immutable profiles remain, with no invalid READY pointer. API retries reused successful profiles; mismatched workspace was rejected. Browser dataset switching, 390px mobile and 768px tablet had no horizontal overflow; overrides reset. Evidence is in `phase7a-live-results.json` and `screenshots/phase7a/`.

## Changed files

Backend: `app/main.py`, new `app/api/dataset_profiles.py`, `app/db/semantic_profile_repository.py`, `app/processing/semantic_evidence.py`, `app/schemas/semantic_evidence.py`, `app/services/dataset_profile_service.py`, `migrations/0018_semantic_profiles.sql`.

Frontend: `frontend/src/App.tsx`, `frontend/src/pages/Dataset/DatasetDetailPage.tsx`, new `frontend/src/pages/Dataset/Profile/DatasetProfilePage.tsx`, `frontend/src/features/datasets/profile/DataProfile.tsx`, `frontend/src/features/datasets/profile/ColumnEvidenceCard.tsx`, `frontend/src/services/api/profile.ts`.

Tests/evidence: new `tests/test_semantic_profiles.py`, `frontend/tests/semantic-profile.cjs`, `tools/phase7a_live_check.py`, `docs/phase7a-semantic-evidence.md`, `docs/phase7a-live-results.json`, and four `screenshots/phase7a/` images. Pre-existing untracked tools/screenshots are preserved.

## Validation

Full database-enabled backend regression: `RUN_DB_TESTS=1 uv run --no-sync pytest -q` passed 251 tests in 2550.02 seconds. That run began before three additional Phase 7A edge cases were added; the finalized focused suite covers all 16 Phase 7A cases and passed in 211.24 seconds. The three final empty-state/timezone/coverage checks and three precision/nonfinite/core-statistic checks passed separately after their final refinements. Typed Decimal and large-integer JSON serialization also passed. Only the existing Starlette/httpx TestClient deprecation warning remains in the final runs. Frontend lint/build and all 12 lightweight suites pass; desktop/mobile/tablet browser checks pass. Git diff and new-file whitespace checks pass. The final development API was restarted and the ready Orders page verified after reload. No commit; no Phase 7B.
