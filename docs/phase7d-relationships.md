# Phase 7D: deterministic workspace relationship discovery

Dataset remains the processing boundary. Workspace is the only relationship scope. Nothing
executes joins, processes deliveries, alters rules/load policies, generates KPIs or starts a
later phase. Optional new AI ranking/explanation is deferred: existing current 7B roles and
7C domain/entity context are read as suggestions only. No provider receives identifiers.

## Existing architecture and readiness

Reuse owned workspace/dataset APIs, published state/version/policy metadata, READY 7A profiles,
7B/7C current selection, repository transactions, trusted checksummed load_pinned_state and
IncrementalArtifacts.validate_state. No second source-data reader or metadata profiler exists.
The existing artifact reader gained an optional bounded-byte argument; default callers retain
the original behavior. Relationship computation has a separate pure engine and bounded adapter.

At least two owned non-archived datasets with current trusted state and current READY profiles
are required. Missing/stale 7B/7C does not exclude deterministic sources. Coverage identifies
every excluded dataset, and semantic availability/current workspace context are separate.
No archived, unowned or cross-workspace dataset is read. SNAPSHOT uses validated active records.

## Pruning, exact comparison and evidence

Indexes of normalized names and identifier/entity tokens prune pairs before any artifact IO.
Only identifier-like/configured parent candidates and compatible TEXT/INTEGER/DECIMAL pairs
participate. Matching uses exact normalized names, entity-qualified generic id/key/uuid, or
shared meaningful identifier tokens. Parent uniqueness is required for confirmation, not for
showing unsafe candidates. Integer widths share canonical INTEGER. TEXT/numeric types never
coerce, nor do whitespace/case/leading-zero strings. Original schemas/names remain untouched.

Reverse orientations of a physical pair are deduplicated. Observed unique/null-free side,
whole configured single-column key, then stable dataset ID break direction ties. This is a
tentative direction, never authoritative PK inference. Named endpoints contain ordered arrays
for future composite support. Automated composite parent keys are deferred; a component of a
configured composite key is not promoted to the whole parent key. Manual arbitrary creation,
bridge inference, graph editing and multi-hop discovery are deferred.

Exact scalar normalization reuses row identity and Pandas scalar handling. Actual trusted
values are compared privately, without hashes/samples in persisted/public evidence. Per-column
value sets are cached and bounded. Evidence reports total/non-null/distinct/repeated rows,
null ratio, uniqueness, distinct matched/missing child keys, child-to-parent coverage and parent
referenced ratio. Null references are excluded from unmatched-key counts. Undefined ratios are
null. Score components are name (20/15), type (15), unique/null-free parent (20), non-null coverage
(up to 30), whole configured parent key (15); the score is not a probability or approval.

Cardinality reflects exact observed uniqueness/repeats and matched values. Both unique gives
possible ONE_TO_ONE; unique parent/repeated child gives ONE_TO_MANY; both repeated gives
MANY_TO_MANY_CANDIDATE; zero matched values or inadequate evidence gives UNKNOWN. This is not
a prediction of future cardinality. Algorithm 2 explicitly leaves zero-overlap cardinality
unknown; initial algorithm 1 live history is retained and is not current.

V1 Confirm requires compatible types, a nonempty unique null-free parent and 100% non-null
child distinct-key coverage. Empty/all-null children, unsafe precision, duplicate/null parent
or missing references cannot be silently overridden. Nullable children require user review of
optionality. Nonconfigured observed unique parents are labeled unique-side candidates rather
than PRIMARY KEY. Reject and explicit subsequent review are supported.

## Bounds and performance

Direct discovery supports 20 eligible datasets, 400 columns, 200 emitted oriented pairs,
100000 trusted rows per dataset and 500000 total trusted rows. Artifact reads are limited to
64 MB per object and 128 MB per run; identifier values to 1024 characters and 32 MB cumulative
encoded value bytes. Limits fail safely, without arbitrary truncation/sampling. Only datasets
participating in pruned pairs incur state IO. Metadata indexing is O(C + emitted indexed hits),
worst-case candidate collisions can still grow quadratically before the 200-pair cutoff.
Exact verification scales with scanned values plus distinct-set intersections.

Pandas still materializes full participating trusted frames; compressed byte bounds and row
bounds are not a hard bound on decompressed heap usage. Python objects have overhead beyond
encoded byte accounting. No workload benchmark or streaming/sketch/Spark implementation is
claimed. Engine/adapters can be replaced while retaining aggregate evidence and source pins.

## Persistence, freshness and review

Additive migration 0021 adds relationship_discovery_runs, relationship_candidates and
relationship_reviews, plus immutable-history protection triggers. No existing table/data is
changed or backfilled. Successful runs/candidates and append-only decisions remain immutable.
Run uniqueness pins workspace/source signature/algorithm; retries reuse failed headers.
Candidate identity uses datasets and ordered columns, never array indexes or raw values.
Decisions pin candidate/evidence, workspace, actor, time and numbered relationship version.
Confirmed configuration is a separate projection of explicit review events, not an AI status.

Membership/current state/profile and current semantic context are selected in one repeatable
snapshot. A schema-scoped PostgreSQL session lock coordinates discovery. Reserve/publication
and review transactions lock workspace then ordered dataset heads, recompute signatures and
reject changed sources. Expensive IO runs outside those row locks. Late results cannot publish
for a newer source combination. Unique constraints and expected review versions serialize
opposing decisions; identical accepted review retries do not insert another event.

Structural signature covers schemas, exact types, ordered configured keys and normalization.
Structural CURRENT/REVALIDATION_REQUIRED/UNAVAILABLE is separate from verification CURRENT/STALE.
After data changes, reviewed configuration remains visible; historical coverage is labeled and
not treated as current. A fresh run does not silently reconfirm a prior decision. Schema/key
changes require structural revalidation. Removed/archived/excluded sources become unavailable.
No consumer is allowed to execute joins using these review-only APIs in this phase.

## API and frontend

GET /workspaces/{workspace}/relationships returns current candidates plus reviewed configuration.
GET /readiness returns lightweight lifecycle/context/source pins/review revision without cards.
POST /discover resolves sources server-side and accepts an empty strict request.
POST /candidates/{id}/confirm or /reject accepts only expected_review_version. No client-supplied
state IDs, evidence, score, model, path, or arbitrary join definition is accepted. Ownership is
checked before metadata/IO; cross-workspace candidate injection is rejected.

Workspace Data Model shows responsive candidate cards, separate coverage directions, score
components, nulls, repeats, warnings, source versions, semantic availability and explicit
Confirm/Reject. Stale candidates are hidden; reviewed configuration keeps labeled history.
Actions are guarded; scoped reads/action requests abort/reset on navigation. Nonoverlapping
polling includes source pins and review revision so another tab's decision is detected.
No new UI framework/dependency, graph editor, backend processing button or fabricated metrics.

## Development changes and validation

User explicitly approved applying 0021 to ai_data_workspace/public and deterministic discovery
on synthetic workspace 16. Before apply, all three tables were absent. Verified three triggers
and 53 constraints afterward; existing counts unchanged: 26 datasets, 41 states, 12 profiles,
10 dataset suggestions and 4 workspace suggestions. No manual pgAdmin change is required.
No data, policies or business-rule answers were changed. After separate explicit user approval,
the browser confirmed Customers (25).customer_id -> Orders (27).customer_id and rejected
Customers (25).customer_id -> Missing AI understanding (29).customer_id. Exactly two review
events exist in workspace 16, each version 1 with the development actor and timestamp recorded.
All other candidates remain unreviewed. None is inferred from high scores. Live evidence is in
phase7d-live-results.json.

The intermediate DB-enabled focused suite passed 14 collected cases. The final focused suite
adds SNAPSHOT active-only source validation and strict evidence rejection; its outcome is recorded
below. The standard final full backend run passed 206 with 133 database-gated skips; focused
checks enable disposable PostgreSQL schemas. All 15 lightweight frontend suites, frontend lint,
production build and git diff --check passed. Existing TestClient deprecation warning only.
No new framework or dependencies were added. An initial all-frontend command was run from the
repository root rather than frontend; corrected invocation passed. The new relationship suite
now also resolves its files relative to itself. Test fixture mistakes were corrected before the
final successful runs; no failing application check is suppressed.

Final PostgreSQL-enabled run: RUN_DB_TESTS=1 uv run --no-sync pytest tests/test_relationships.py -q:
16 passed in 266.94 seconds. Includes exact overlap/privacy/cardinality, SNAPSHOT active-only
selection, strict invalid evidence rejection, lifecycle/review audit/ownership/isolation,
immutable history, retry/interruption, membership/state publication races and concurrent
opposing decisions. No failed final check remains. Frontend evidence labels refer to semantic
roles at verification so historical cards cannot imply current AI context.

Browser checks: both review decisions persist after refresh and after switching workspace 16 ->
15 -> 16. Workspace 15 has no relationship discovery/reviews from workspace 16. Unsafe zero-overlap
Confirm is disabled. Desktop, 390x844 mobile and 768x1024 tablet were visually inspected; document
width never exceeded viewport width. Overrides were reset. No additional discovery, review,
delivery processing or AI requests were made in workspace 15. Proof screenshots are in
screenshots/phase7d; approved-reviews.png shows the two explicit decisions.

Manual follow-up: open workspace 16 Data Model and verify both decisions; refresh and switch to
another workspace; inspect zero-overlap disabled confirmation; inspect mobile navigation.
For a separately authorized disposable delivery, update a source and refresh its 7A profile:
old reviewed configuration must remain visible with stale verification until fresh explicit
review. A material key/schema change must require structural revalidation. These change/race
paths are covered by isolated PostgreSQL tests; no live business data was changed to test them.

Git HEAD advanced externally to 7b2b7a3 during implementation, committing eight initial backend
files with the message "feat: add workspace semantic discovery". The agent did not create that
commit or revert it. Remaining edits/builds/tests continue on top. No later phase was started.

## Files changed in this phase

Backend: app/main.py, app/storage/incremental_artifacts.py, app/api/relationships.py,
app/db/relationship_repository.py, app/schemas/relationships.py,
app/processing/relationship_evidence.py, app/services/relationship_service.py,
migrations/0021_relationship_discovery.sql.

Frontend: frontend/src/App.tsx, frontend/src/pages/Workspace/WorkspaceDetailPage.tsx,
frontend/src/services/api/relationships.ts,
frontend/src/features/workspaces/relationships/RelationshipsPage.tsx,
frontend/src/features/workspaces/relationships/RelationshipEvidenceCard.tsx.

Validation/evidence: tests/test_relationships.py, frontend/tests/relationships.cjs,
tools/phase7d_live_check.py, docs/phase7d-relationships.md, docs/phase7d-live-results.json,
screenshots/phase7d/ proof images. Existing untracked tools/screenshots are preserved.
