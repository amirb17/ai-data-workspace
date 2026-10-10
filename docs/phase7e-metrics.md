# Phase 7E: controlled workspace metric discovery

Dataset remains the processing boundary. Metrics describe one owned workspace; they never
process deliveries, mutate Gold, execute joins, or change rules, contracts or load policies.
The additional user interruption policy allows deterministic SYSTEM_POLICY acceptance,
not automatic trust in an AI assertion. Business decisions are never answered by discovery.

## Existing analytics retained

The existing KPI planner proposes Total Records and at most six SUM/MEAN measures from
semantic Gold catalog measure roles and name heuristics. Its Pandas KPI calculator, chart,
suggestions and Ask Your Data APIs remain unchanged. Ask Your Data already validates
structured operations against owned catalog fields; it is not a multi-dataset metric engine.
The cumulative Gold pointer/source-state check and state_analytics_status determine
analytics freshness. This phase reads that metadata; it does not reuse stale values.

## Inputs and expression boundary

Reuse READY current 7A profiles, current 7B suggestions, current 7C workspace suggestions,
owned workspace/dataset APIs and repository transactions. Only confirmed 7D relationships
with current structure AND current verification enter discovery. No state artifacts are read.
The existing Gemini provider/client and schema adapter receive names, types, configured
keys, aggregate null/uniqueness evidence, semantic roles and confirmed relationship metadata.
No raw rows, category labels, S3 paths, hashes, credentials or database access are supplied.
Names and prose are untrusted data. Limits: 20 datasets, 400 columns, 100 KB input, 20 metrics,
24 expression nodes per metric, depth six, three dimensions, one relationship, 250 KB output.

Strict provider output cannot assign approval or status. The ordered expression DAG permits
COLUMN, numeric row MULTIPLY, COUNT, COUNT_DISTINCT, SUM, AVG, MIN, MAX and aggregate DIVIDE.
Forward references/cycles, unused nodes, aggregate nesting, incompatible semantic/type roles,
SQL/code fields and invented references are rejected. Shared operands retain numeric checks
in every branch. Ratios/rates have NUMBER-free RATIO units; percentage scale is explicitly
100. Zero denominator is always NULL/unavailable, never zero. No expression is evaluated.

ENTITY grain requires the exact configured, observed unique and null-free key. Composite
tuple uniqueness is not inferred from names. Cross-dataset dimensions/time references only
allow a single confirmed child-to-parent lookup with unique/null-free parent and complete
observed coverage. Parent-to-child and many-to-many fanout, unused or disconnected paths,
bridge tables and multi-hop paths cannot validate. Required columns include join keys.
Time grouping requires typed TIME_DIMENSION evidence and explicit day/week/month bucket.
Filters are structured EQ symbolic category requirements. Without an approved dictionary,
all such filters remain unresolved; category labels are never sent to AI or invented.

## Decision policy and review

Validity and uncalibrated AI confidence are independent. Errors produce INVALID; unresolved
assumptions, business scope, sensitive fields, uncertain entity grain or categories require
REVIEW_REQUIRED. VALID definitions are safe metadata recommendations, not calculated values.

* AUTO_ACCEPT: only the canonical technical `Record count`, COUNT of base records without
  filters/grouping, RECORD grain, current dependencies, no warnings/assumptions, strong
  entity metadata (>=0.9), workspace metadata (>=0.8) and proposal confidence (>=0.9).
  All structural gates must pass. SYSTEM_POLICY stores the exact definition/dependencies.
  AI confidence alone can never authorize a business KPI. The narrow allowlist is intentional.
* REVIEW_RECOMMENDED: structurally VALID transparent column/formula names with no unresolved business assumption; optional
  approval, safe to display as a recommendation. No forced per-candidate approval dialog.
* REVIEW_REQUIRED: assumptions/unsafe structure must be resolved before approval. V1 cannot
  edit definitions or supply category meanings; do not silently invent answers.

Same names within the same base dataset with conflicting formulas, or identical formulas with competing business meanings,
require review. Duplicate proposals combine the worst validation/decision rather than hiding
assumptions through order. Only VALID/current candidates can be explicitly approved.
Unrelated upstream warnings prevent automatic acceptance without requiring a business
answer for every literal technical recommendation. Referenced-field warnings, unresolved
entity/key/category assumptions and arbitrary business labels still require review. Algorithm
and policy version 3 include these scoped naming and interruption-policy checks and prevent
SUM or entity distinct-count from borrowing the technical Record count label; run history
from earlier versions is preserved and does not become a current recommendation.
Approve/reject events preserve actor/time/version. Opposing stale reviews conflict; retries
of the already-recorded decision are idempotent. Reject disables use without erasing history.

## Persistence, races and freshness

Migration 0022 adds metric_discovery_runs, metric_candidates, metric_candidate_dependencies,
metric_reviews and approved_metric_definitions, plus five immutability/context triggers.
It does not alter existing analytics, ingestion or processing tables. Candidates, dependencies,
reviews and registry rows are append-only; successful runs and pins are immutable. Approved
definitions must exactly match validated candidate formula/dependencies and policy version.

Runs pin dataset profile/state/schema/semantic versions, structural/semantic hashes, current
workspace suggestion, required relationship review versions, algorithm/policy and provider
configuration/prompt/model. Successful identical runs are reused without another AI call.
A schema-scoped PostgreSQL advisory lock serializes discovery; provider calls happen outside
publication transactions. Publication/review locks workspace membership then ordered dataset
heads, rechecks all source/configuration signatures, and atomically saves complete results.
Failures expose safe codes, can retry the same reserved run, and never publish partial output.
Late source changes discard output. Interrupted reservations can retry. No giant processing
transaction or frontend-only orchestration is introduced.

Definition, dependency and analytics-data freshness are separate. Appended rows make pinned
dependencies stale without automatically invalidating unchanged structural semantics. Schema,
keys, semantic roles or rejected/changed relationships require revalidation. Archived/missing
datasets make definitions unavailable. Current cumulative Gold determines data freshness.
Freshness polling includes Gold metadata separately from AI discovery identity, so Gold refresh
updates UI without charging for a new discovery. Discovery is explicit, not automatic per row.

## Workspace UI and deferred execution

Workspace Metrics shows coverage/exclusions, failures/retry, source readiness, exact bounded
formula, grain, dimensions/time/filters, required columns, confidence versus validation,
policy decision/review and all three freshness states. Navigation aborts old requests, resets
workspace state, serializes actions and avoids overlapping readiness polls. Cards wrap on
mobile and controls have touch targets. Current safe technical counts need no approval dialog.

All definitions are definition-only: execution_available=false and preview_value=null.
Single/cross-dataset execution and previews are deferred together rather than introducing a
second query engine or fake results. No dashboards, SQL Analytics, unrestricted joins,
scheduling or subsequent phase work is implemented.

## Validation and development rollout

Run standard backend pytest, RUN_DB_TESTS=1 tests/test_metrics.py, frontend lint/build and
all existing frontend/tests/*.cjs. PostgreSQL fixtures apply migrations in isolated disposable
schemas, covering ownership, publication/retry/stale races, idempotency, exact registry storage,
immutability and concurrent discovery/review. Pure cases cover expression, privacy, categorical,
time, grain, fanout, dependency and decision boundaries.

Applying 0022 to the development public schema requires explicit approval under the user's
earlier migration instruction. Live Gemini calls require phase-scoped permission to send
synthetic names/redacted metadata. Disposable-schema tests do not imply the real development
database has been migrated. Never print database connection secrets or raw provider output.

Manual checks: open Workspace Metrics, discover once, refresh/repeat (same run); inspect all
three decision types; check optional approve/reject and unresolved approval absence; switch
workspaces during discovery; append data or change required relationship and check staleness;
refresh cumulative Gold and check data freshness without a new run; check narrow/mobile layout.

## Files in this phase

Backend:
* app/main.py
* app/ai/metric_discovery.py
* app/api/metrics.py
* app/db/metric_repository.py
* app/processing/metric_validator.py
* app/schemas/metrics.py
* app/services/metric_service.py

Frontend:
* frontend/src/App.tsx
* frontend/src/pages/Workspace/WorkspaceDetailPage.tsx
* frontend/src/features/workspaces/metrics/MetricsPage.tsx
* frontend/src/features/workspaces/metrics/MetricCard.tsx
* frontend/src/services/api/metrics.ts
* frontend/tests/metrics.cjs

Storage, verification and documentation:
* migrations/0022_business_metrics.sql
* tests/test_metrics.py
* tools/phase7e_live_check.py
* docs/phase7e-live-results.json
* docs/phase7e-metrics.md

## Final verification

Standard backend regression: 232 passed, 137 database-gated tests skipped. Separately, the
Phase 7E PostgreSQL-enabled suite passed all 30 tests in disposable schemas. The entire
database-enabled regression suite was not rerun for 7E; do not confuse focused DB coverage
with a complete DB-enabled run. Existing Starlette/httpx deprecation warning remains.
Frontend lint and production build passed; all 16 lightweight frontend test scripts passed.
git diff --check and no-index whitespace checks of new phase files found no whitespace errors.

With explicit user approval, migration 0022 was applied to ai_data_workspace/public after
confirming all five new tables were absent. Five protection triggers and 76 PostgreSQL
constraints (including PostgreSQL 18 NOT NULL constraints) were verified. Existing counts
were unchanged: 26 datasets, 41 state versions, 12 profiles, 10 dataset suggestions, four
workspace suggestions and two relationship reviews. No manual pgAdmin migration is required.

The approved live check uses only synthetic workspace 16, and verifies source ownership,
strict validation, exact run/candidate reuse, workspace 15 isolation, no raw-value/path leakage,
no preview/execution, unchanged upstream counts and zero user review events. The accompanying
JSON records the latest successful run, decisions and exact candidate evidence. Earlier
experimental runs remain immutable audit history, not current recommendations.
The final live run is run 4: five of six datasets eligible, 16 REVIEW_REQUIRED proposals,
zero automatically accepted definitions and zero user approvals. The provider declared
unresolved scope/additivity assumptions; they were preserved rather than answered. All three
decision classes, including automatic acceptance and optional review, are covered by the
synthetic isolated tests. Provider quality can affect how many useful recommendations appear.

Live desktop/mobile visual checks are incomplete: Computer Use stopped because it could not
determine Chrome's current URL confidently enough to enforce policy. No further UI input was
issued after that stop. Frontend SSR/scoping/responsive-structure checks passed, but do not
substitute for a live browser walkthrough. No commit or subsequent phase work was performed.
