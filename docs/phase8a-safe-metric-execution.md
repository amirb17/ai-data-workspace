# Phase 8A: safe metric execution and workspace analytics

Dataset remains the processing boundary; workspace analytics calculates immutable validated
metric definitions from current cumulative dataset Gold. No AI call, SQL generation, delivery
processing, business-rule change, automatic dashboard or Ask Your Workspace is introduced.

1. **Existing analytics architecture reused.** Phase 7E's strict ordered formula DAG,
   `validate_metric`, semantic/source gathering, decision policy and immutable candidates;
   Phase 7D's confirmed/current relationship reviews; Phase 6E's owned cumulative Gold
   pointer, active-only BASE artifact and checksummed catalog/schema reader; existing principal,
   repository transactions, PostgreSQL advisory locks and safe API error boundary. Existing
   dataset KPI/chart/Ask executors are unchanged. Their single-aggregation query shape cannot
   express the validated multi-aggregate metric DAG; the new engine implements that existing
   DAG rather than adding a second planner or unrestricted query language.

2. **Files changed.** Modified: `app/main.py`, `app/storage/cumulative_gold_artifacts.py`,
   `frontend/src/pages/Workspace/Analytics/WorkspaceAnalyticsPage.tsx`,
   `frontend/src/features/workspaces/metrics/MetricsPage.tsx`. Added:
   `app/api/workspace_analytics.py`, `app/db/metric_result_repository.py`,
   `app/processing/metric_execution.py`, `app/services/workspace_analytics_service.py`,
   `frontend/src/services/api/workspaceAnalytics.ts`,
   `frontend/src/features/workspaces/analytics/WorkspaceMetricCard.tsx`,
   `migrations/0023_metric_results.sql`, `tests/test_metric_execution.py`,
   `tests/test_workspace_metric_results.py`, `frontend/tests/workspace-analytics.cjs`,
   `tools/phase8a_live_check.py`, this document, `docs/phase8a-live-results.json`, and
   `screenshots/phase8a/` evidence. Pre-existing untracked `tests/test_metrics.py`, tools and
   screenshots are preserved. No commit.

3. **Executable metric eligibility.** A VALID immutable candidate from the latest successful
   discovery run must use the current validation/policy algorithms, remain unrejected, and
   pass deterministic validation against current required dataset profiles/semantics. Exact
   required schema/policy and semantic hashes must match the definition. Required sources
   must be owned, available and backed by fresh cumulative Gold. Relationship versions,
   direction, cardinality, grain, columns and filters are checked. INVALID proposals are
   excluded from analytics counts; unresolved candidates remain visible but blocked.

4. **User-interruption policy behavior.** Refresh executes all safe AUTO_ACCEPT and
   REVIEW_RECOMMENDED candidates without approval dialogs or user review events. Optional
   review never blocks safe execution. REVIEW_REQUIRED never executes. Existing business
   assumptions are never answered by the executor. No provider request is made by refresh.

5. **Metric execution-plan model.** Immutable internal `MetricExecutionPlan` pins workspace,
   candidate, exact definition (grain, graph, dimensions, time and filters), required dataset
   state/Gold IDs and structural/semantic hashes, direct relationship/version/direction/
   cardinality, dependency signature and execution algorithm 1. The candidate ID is the
   execution identity because recommended definitions need no approved-registry row. The
   immutable discovery run ID is the definition-version token. Clients cannot submit plans.

6. **Expression execution.** A replaceable `MetricExecutionEngine` protocol isolates the
   bounded Pandas adapter. Only the existing validated operations COLUMN, COUNT,
   COUNT_DISTINCT, SUM, AVG, MIN, MAX, numeric row MULTIPLY and aggregate DIVIDE execute.
   No eval, exec, user Python or SQL. ADD/SUBTRACT and new expression operations remain
   unsupported by the approved Phase 7E definition schema. Integer aggregates/products use
   Python integers to avoid silent int64 overflow; integers outside JavaScript's exact range
   are returned as exact strings. Floating averages/ratios retain ordinary numeric precision.

7. **Single-dataset execution.** Calculations read the checksummed record-grain cumulative
   BASE artifact, including current UPSERT representations and active-only SNAPSHOT rows.
   COUNT_DISTINCT counts non-null distinct values of the specified field, never substituted
   with record count. No historical delivery artifact or dataset KPI heuristic is substituted.

8. **Cross-dataset execution.** Exactly one direct confirmed current child-to-unique-parent
   lookup is supported. ONE_TO_MANY preserves the child fact rows; ONE_TO_ONE additionally
   checks child-key uniqueness. Multi-hop, bridges, many-to-many, parent-to-child fanout and
   composite relationships are rejected. Parent/child column lists preserve future compatibility.

9. **Relationship enforcement.** Only the owned workspace's confirmed relationships with
   current structure AND verification enter planning. The definition's relationship version
   must match; changed/rejected/missing reviews require revalidation/current confirmation.
   Runtime validates exact unique non-null dimension keys and complete fact-key coverage.

10. **Grain/fanout protection.** Entity keys are exact configured keys and checked for actual
    nulls/duplicates before aggregation. Dimension joins use many-to-one validation, reject
    duplicate dimension keys rather than dropping them, and verify the fact-row count is
    unchanged. ONE_TO_ONE verifies both sides. Unsafe transitions never produce a result.

11. **Null/division/filter semantics.** COUNT counts actual rows; COUNT_DISTINCT excludes
    nulls. SUM/AVG/MIN/MAX ignore missing inputs and return null when no non-null input exists.
    DIVIDE returns null for zero or missing denominator/numerator. Nonfinite results fail
    safely. Null dimension keys remain JSON null and display `(null)`; no Unknown label is
    invented. Existing symbolic EQ filters lack an approved category dictionary and therefore
    remain blocked. No category value is guessed.

12. **Source/freshness behavior.** Every required dataset needs SUCCESS Gold for its exact
    current state AND dataset freshness FRESH. A single stale source yields REFRESH_REQUIRED
    and withholds values. No mixed-currentness result or delivery fallback. Required evidence
    must also be current: refresh profiles/understanding after a data update before structural
    revalidation, and refresh Gold separately. Metric refresh never rebuilds dataset Gold.
    Source version IDs are exposed safely; paths/checksums and storage handles remain internal.

13. **Result model/versioning.** `workspace_metric_results` stores append-only attempt IDs,
    owned candidate/definition version, algorithm, exact plan/dependency signature, start/
    completion time, COMPUTING/FRESH/FAILED status, scalar/grouped JSON and allowlisted error.
    Terminal rows cannot update/delete. Public current status additionally derives
    NOT_COMPUTED/STALE/BLOCKED. Historical FRESH means successful computation for pinned
    sources; only matching current signatures make a result currently FRESH. No mutable
    current pointer can accidentally publish an old source result.

14. **Idempotency.** Exact definition, required source versions, relationship versions and
    algorithm reuse successful result IDs without S3 reads/calculation. A partial unique
    success index reinforces this identity. Failed retries append new attempts, retaining
    old errors and successful results.

15. **Concurrency/source-change behavior.** A schema-scoped workspace session advisory lock
    serializes refresh/retry. Short reservation transactions lock workspace membership and
    dataset heads; artifact IO executes outside them. Each metric re-resolves inputs before
    reservation. Completed old-source output remains historical; current reads compare actual
    current versions and withhold it. Review rejection/source updates during calculation cannot
    mark the new source combination current. Readiness detects interrupted coordinators;
    explicit retry finalizes abandoned COMPUTING attempts as INTERRUPTED before appending.

16. **Failure/recovery.** A metric failure changes only its own attempt. No ingestion/state/
    Gold/definition mutation. Successful siblings remain current. Retry resolves current inputs
    and reruns only the requested calculation; a lost response can recover persisted success.

17. **Workspace analytics refresh behavior.** Server resolves current candidates, skips blocked
    definitions, reuses successful exact signatures and computes only missing/stale/failed
    eligible results. GET restores authoritative persisted results after reload. Workspace
    readiness includes totals, available executable definitions, fresh/stale/failed/blocked
    counts, last attempt completion, refresh availability and per-metric cards/provenance.

18. **Partial-success behavior.** Current successful results remain available alongside failed
    or blocked metrics; workspace status is NEEDS_ATTENTION rather than fully FRESH. Invalid
    proposals are omitted. `metrics_available` counts executable definitions with fresh inputs;
    `metrics_fresh` counts successful current results.

19. **Blocked/recommended/automatic metric behavior.** Cards show Needs definition and review
    reasons, Recommended metric / Review is optional, or Ready automatically respectively.
    Relationships needing confirmation link to Data Model; unavailable datasets explain their
    absence. Stale/failed/blocked cards never display historical numbers as current truth.

20. **Grouped/time-series behavior.** At most 100 groups, ordered ascending by typed dimension
    keys with nulls last. Total groups/truncation are explicit; there is no hidden top-value
    ranking or unbounded pagination. Typed datetime grouping supports DAY, Monday-start WEEK
    and MONTH using the source timezone's calendar without guessing a business timezone.
    JSON time buckets use ISO timestamps. Deterministic scalar/table/line hints accompany
    results; the card presents grouped/time output as a bounded table. No automatic dashboard.

21. **Existing dataset analytics compatibility.** Dataset analytics/Ask routes and builders
    remain unchanged. The Gold validator's optional base-only/validated-frame return avoids
    scanning marts for metrics; its default behavior still validates every artifact exactly
    as before. New workspace routes are a separate layer and preserve existing URLs.

22. **API/security.** GET `/workspaces/{id}/analytics` and `/readiness`; POST `/refresh` and
    `/metrics/{candidate_id}/retry`. Existing principal and ownership checks precede metadata/
    IO. Strict empty request bodies reject client SQL, plans, paths, source IDs, relationship
    IDs and result values. Candidate injection from another workspace fails owned resolution.
    Response allowlists expose only definitions, counts, values, safe versions and error codes.

23. **Observability.** Logs record workspace, candidate/definition/result, source versions,
    relationship IDs, algorithm, duration, status, input rows/output groups and reuse. No raw
    rows, category contents, provider prompts, credentials or private storage paths are logged.

24. **Performance/V1 limitations.** Synchronous Pandas, max 250,000 rows per source, 64 MiB
    per object and 256 KB result JSON. A two-source LRU per refresh reuses validated immutable
    frames. Current reads load bounded latest/current attempts rather than all historical
    result JSON. Grouping still scans bounded inputs; compressed-size/row limits are not a
    complete memory bound or performance benchmark. No durable queue, distributed engine,
    approximate calculations, partition pruning or scheduling. The engine protocol permits
    later SQL/DuckDB/warehouse adapters without changing the validated definition contract.

25. **Migration changes.** 0023 is additive: one table, two indexes, one immutability/context
    function and one trigger, plus owned candidate/version foreign keys and status/output
    constraints. No definition backfill, destructive alteration or old state/Gold rewrite.
    Explicit user approval was obtained before applying to ai_data_workspace/public and
    restarting only the verified project API. Existing counts remained: 26 datasets, 41
    state versions, seven Gold runs, 54 candidates, zero metric reviews/approved definitions.

26. **Tests added.** 24 pure execution/planner/freshness cases; three real isolated PostgreSQL
    lifecycle scenarios exercising first/repeat refresh, optional review, immutable results,
    API injection/ownership, actual state update, unrelated membership reuse, partial failure/
    retry and concurrent refresh/source staleness. Frontend SSR/API suite exercises lifecycle,
    real-value gating, optional review, grouping, responsive structure and workspace isolation.
    Existing Phase 7D/7E suites provide underlying relationship/definition safety coverage.

27. **Standard backend result.** Final standard regression: 256 passed, 140 database-gated
    tests skipped (15.13 seconds). Existing TestClient deprecation warning only.

28. **Full DB-enabled backend result.** Full regression completed with 390 passed and three
    setup errors in 4624.74 seconds. It started before the isolated test-store fixture was
    corrected; all three errors are in that fixture, not assertion failures. The initial focused
    metric-result suite passed three scenarios; the expanded lifecycle rerun also passed all
    three in 249.63 seconds. Phase 8A/cumulative Gold focused regression passed all 47 cases
    in 858.10 seconds. Final exact-value lifecycle regression passed all three scenarios in
    190.12 seconds, including repeated-delivery SUM 60 and COUNT 4. Final `--lf` rerun of the
    full suite's three recorded setup errors passed all three in 142.99 seconds. No unresolved
    regression remains. This is not claimed as a single clean full-suite run against the final
    files; additional final pure cases are covered by the standard and focused suites.

29. **Frontend validation result.** Lint and production build passed; all 17 lightweight
    frontend test scripts passed. Tracked diff and all new Phase 8A text files passed whitespace
    verification.

30. **Manual integration result.** Approved synthetic commerce workspace 16 has 16
    REVIEW_REQUIRED definitions, zero safe executable definitions. GET and POST refresh show
    16 blocked/zero current/zero exposed values; no result or review row is created. Injected
    SQL/state/relationship/result bodies return 422; nonexistent metric retry returns 404.
    Workspace 15 reads zero metrics and receives no changes. Desktop (1280px) and mobile
    (390px) were visually inspected with no horizontal overflow (1265/375px document widths).
    Browser reload restored the authoritative blocked state; switching to workspace 15 showed
    zero metrics and returning to 16 restored its 16 blocked metrics. Temporary viewport
    overrides were reset after saving evidence. Live A/B/D/F/G/H arithmetic
    walkthrough cannot be demonstrated using this workspace's unresolved definitions;
    those behaviors are exercised by isolated tests without inventing business meanings.
    Chrome attachment timed out; the in-app browser successfully verified the live app.

31. **Known limitations.** Category dictionary/definition editing is absent from Phase 7E;
    review-required assumptions cannot be resolved simply by clicking approval. No live
    Revenue/AOV/segment values are claimed. Current evidence and Gold are explicit upstream
    prerequisites. No unsupported joins, new AST operators, arbitrary SQL, Ask Your Workspace,
    automatic dashboards or next-phase features. Existing temporary development identity and
    Starlette/httpx TestClient deprecation warning remain.

32. **Architecture deviation discovered.** Phase 7E discovery identity includes all workspace
    members; execution must not invalidate unrelated metrics on any membership/state change.
    Execution retains the candidate's immutable workspace business-scope evidence when no
    matching current workspace suggestion exists, while revalidating all actual required
    dataset structure/semantics/relationship inputs. It does not claim historical workspace
    discovery is current. Results pin actual dependencies only. Approved definition/review
    registries and discovery freshness remain unchanged.

33. **Whether Phase 8A is ready for approval.** Ready for engineering review with the live
    arithmetic limitation in item 30 explicit. Implementation, approved migration, regression
    checks and live blocked UX are complete; no unresolved test failure remains. Successful
    commerce arithmetic acceptance still requires properly resolved upstream business
    definitions and current Gold. Recommended next step: review this phase and its evidence;
    do not start the next phase or approve ambiguous definitions merely to produce values.
