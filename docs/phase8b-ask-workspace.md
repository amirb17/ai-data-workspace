# Phase 8B — Ask your workspace

Migration 0024 was explicitly approved and applied to `ai_data_workspace/public`, and the
project API was restarted successfully. Live Gemini single-dataset arithmetic, persisted
history, API read, retry reuse and the real HTTP frontend are verified. The full DB
regression completed successfully: 453 passed. Final focused coverage passed 82 tests.
No commit or ingestion/business-data change was made. Commerce still lacks current Gold.

1. **Architecture reused.** Centralized `GeminiSemanticProvider` / `get_ai_client` interactions,
   strict schema adapter, owned workspace APIs, current semantic gathering, Phase 7E metric
   validation, Phase 7D confirmed relationships, Phase 8A metric plans/executor/result reuse,
   checksummed cumulative Gold reader, repository transactions and schema-scoped advisory locks.
   Legacy dataset Ask Your Data is unchanged. Gemini returns only `WorkspaceQuestionPlan`.

2. **Files changed.** Existing: `app/ai/semantic_provider.py` (configurable response-token
   budget, existing default retained), `app/main.py`, Workspace Analytics page. Added:
   `app/schemas/workspace_questions.py`, `app/ai/workspace_question_planner.py`,
   `app/processing/workspace_question_resolution.py`, `workspace_question_execution.py`,
   `app/services/workspace_question_service.py`, `app/db/workspace_answer_repository.py`,
   `app/api/workspace_questions.py`, `migrations/0024_workspace_answers.sql`, frontend
   `services/api/workspaceQuestions.ts`, `features/workspaces/ask/{WorkspaceAsk,AnswerCard}.tsx`,
   focused backend/frontend tests, fixture display generator, `tools/phase8b_live_check.py`,
   `tools/phase8b_verify_migration.py`, `tools/phase8b_api_live_check.py`, opt-in
   `tools/phase8b_live_fixture.py`, safe API JSON evidence, this report, and screenshots.
   Pre-existing untracked 8A tests,
   tools and screenshots remain preserved. The generated preview HTML was removed after
   verification, and the temporary browser viewport was reset.

3. **Question intent.** Strict owned references, bounded aggregate enum, dimensions, typed
   dates, time grain, at most one relationship, top N, context mode and meaning state.
   Requests accept question, owned previous/clarification answer, choice and IANA timezone;
   they reject client plans, SQL, storage paths, source versions and result values.

4. **Supported types.** Scalar, count, grouped, breakdown, time series, trend, ranking and
   scalar two-period comparison. Output must match type: a scalar cannot hide grouping and
   a count cannot borrow a SUM metric. Grouped period comparisons remain unsupported.

5. **Metric reuse.** Only current executable AUTO_ACCEPT / REVIEW_RECOMMENDED definitions.
   A matching metric result is reused/refreshed through Phase 8A without an approval dialog.
   Rejected, INVALID and REVIEW_REQUIRED definitions never execute.

6. **Temporary plans.** Backend builds literal definitions, calls the existing validator and
   compiler, then uses the same Gold reader/executor. No metric registry/discovery/review
   row is created. Entity count requires a configured single, proven unique/null-free key.
   Record count remains literal record count; it cannot silently answer an entity count.

7. **Interruption policy.** Literal supported calculations proceed. Business/category/grain
   uncertainty produces a concise business clarification. Backend-generated measure choices
   are revalidated and affect only that answer, never trusted definitions. Display time-grain
   assumptions are disclosed. No SQL/join/aggregation approval prompts are introduced.

8. **Semantic resolution.** Current owned schemas/roles/keys and transparent measures.
   Referenced sensitive fields, warnings, invalid role/type, invented columns and ambiguous
   entity keys are blocked. Explicit field/entity grounding guards against a provider
   selecting a valid but unrelated measure. This conservative lexical guard can require
   clarification for unsupported business synonyms; it does not prove arbitrary language.

9. **Cross-dataset behavior.** One current confirmed fact-to-parent lookup; measures remain
   on the fact. Real isolated fixture: SUM(amount) by parent status returned `open: 30`,
   pinned both Gold sources and relationship version, and reused the saved answer.

10. **Relationship/fanout.** Inherit compiler direction/cardinality/version/coverage checks
    and runtime unique-parent, key coverage, ONE_TO_ONE and unchanged fact-row-count checks.
    No multi-hop, bridge, many-to-many, composite lookup or parent-to-child expansion.

11. **Freshness.** Require current profile/understanding/current cumulative Gold for actual
    dependencies. Unrelated stale members do not block a safe literal calculation. Answer
    reads recompile pins and recalculate calendar identity; stale historical values are
    withheld. Metric auto-refresh never processes files or rebuilds dataset Gold.

12. **Time/ranges.** Explicit half-open dates and today/yesterday/this/last week, month,
    quarter and year. Monday-start weeks; day/week/month/quarter/year buckets. IANA user
    timezone, with an explicit question timezone taking precedence. Missing/ambiguous
    timezone needs clarification; UTC is never silently assumed. Comparisons display the
    earlier period as baseline, later period as comparison, with safe change/percentage.

13. **Follow-up/context.** One structured previous intent, no raw conversational transcript
    or chain expansion. Explicit new subjects clear inappropriate context before planning;
    owned references are revalidated. New analysis resets frontend context. Record/entity
    semantics survive follow-ups. Raw result values are not passed back to Gemini.

14. **Answer/result model.** Strict `WorkspaceAnswer`: identity, question/intent, six statuses,
    scalar/grouped/comparison, metric/temp reference, dimensions, freshness, timestamp,
    provenance, relationship versions, interpretation, explanation, caveats and suggestions.
    Immutable answer JSON and internal plan/dependency evidence preserve reproducibility.

15. **Formatting.** Counts with separators, bounded decimal/percentage precision, exact
    integer strings retained, null shown as unavailable. No guessed currency. Comparisons
    avoid division by zero and distinguish no records from a real zero.

16. **Provenance.** Safe dataset names/IDs, state/Gold IDs, relationship IDs/versions,
    calculation and dates. Internal signatures, hashes and storage handles are withheld.

17. **Visualizations.** Scalar cards, accessible exact-value tables, single-dimension bar
    charts and chronological line charts using existing Recharts. Exact large integers/null
    values fall back to tables. No dashboard, causal narrative or fabricated forecast.

18. **Suggestions.** Backend validates each readiness question/follow-up. Only executable
    current metric/literal counts, dimensions and supported dates are offered. Readiness
    is READY/PARTIAL/NEEDS_REFRESH/NOT_READY, independently of global workspace perfection.

19. **Unsupported/no data.** Forecast/general/raw-row queries, unsupported grain/filters,
    oversized rankings and unavailable semantics have explicit responses. No input records
    is NO_DATA, not a fake zero. Runtime failures and input limits remain safe and retryable
    or unsupported as appropriate; no raw exception body reaches users.

20. **Privacy/security/injection.** Structured allowlists, separate system instruction,
    no SQL/eval/tools, strict reference validation, question bounds, owned answer/choice IDs,
    no prompts/question text/results/provider exceptions in ordinary logs. Gold rows, category
    samples, extrema/statistics, credentials, storage paths and hashes never enter handoff.
    Obvious personal identifiers/raw-row/credential/instruction requests are blocked before AI.

21. **Sensitive data.** Aggregate only; sensitivity hints block referenced fields and exclude
    them from the provider column catalog. This is the existing heuristic policy, not a
    comprehensive DLP classifier. Question text is stored in owned answer history for the
    required conversation/audit experience; it is not put in ordinary application logs.

22. **Caching.** Reuse typed planning for exact question/context/timezone/configuration;
    revalidate before calculation. Arithmetic cache includes actual plan, intent, resolved
    dates, Gold/semantic/structural/relationship pins and algorithm. Paraphrases can reuse
    arithmetic while retaining their own immutable question record. Explicit refresh appends.

23. **Concurrency/races.** Separate schema/workspace question session lock. IO/provider work
    is outside publication transactions. Publication locks membership/heads, re-resolves
    actual dependencies and withholds output after a source change. Old answers are immutable.

24. **API.** POST `/workspaces/{id}/questions`; GET `/readiness`, `/answers`,
    `/answers/{answer_id}`; POST `/answers/{answer_id}/refresh` under the questions prefix.
    Existing principal/ownership and safe error boundary apply before reads or computation.

25. **UX.** Within Workspace Analytics: question, chips, preparing state, clarification
    choices, values/tables/charts, details, recent owned answers, retry/refresh, follow-ups,
    context reset. Server history is reloaded/polled; action revisions prevent old reads
    overwriting a newer answer; requests abort when the workspace changes.

26. **Accessibility/responsive.** Labels, live status, keyboard form submission, accessible
    details and table captions, 44px controls and wrapping layouts. Fixture replay inspected
    at desktop 1280, tablet 768 and mobile 390; document widths 1265/753/375 respectively.
    Keyboard Tab/Enter, provenance expansion and New analysis reset were verified.
    Live-Gemini fixture result 2 and its calculation/source details also displayed correctly
    in the production component replay at desktop and 390px, with no horizontal overflow.

27. **Observability/cost.** ID/status/duration/algorithm logs, private provider/model/strategy,
    request/output bytes and duration metadata. Limits: question 1000 chars, context 20
    datasets/400 columns/100 metrics/100 relationships/100KB; provider 2048 output tokens
    and 16KB parsed output; two dimensions, one hop, 100 groups/top20/history20. Existing
    250k rows/source, 64MiB/object and 256KB result limits apply. Provider timeout 60s;
    execution has a separate 60s elapsed-budget check. The synchronous adapter cannot
    forcibly interrupt an already-blocked artifact read; this is an explicit V1 limitation.

28. **Tests.** Focused pure tests cover strict schemas/provider/privacy, metrics/temp plans,
    source/sensitivity/type/grain/relationship gates, no business guesses, all calendars/time
    grains, comparisons/zero/no-data, rankings, formatting, contexts and explicit topics.
    Real PostgreSQL tests cover API/history/immutability/ownership, current arithmetic/retry,
    clarification choices, source race, confirmed direct lookup, metric reuse and refresh;
    final additions exercise semantic-cache history, concurrent question locks, and column-free
    distinct counts resolving only a configured single key (including grouped counts).
    Live verification found two defects: column-free COUNT_DISTINCT received a generic
    unsupported response rather than the same proven-key resolution used for COUNT, and
    the provider transport omitted numeric bounds so Gemini sometimes returned top_n=0.
    Fixed the former through the existing key/grain rules and the latter with explicit
    planner bounds/defaults and a schema description. Final review also found that an omitted
    follow-up question_type defaulted to SCALAR, rejecting inherited grouped/time-series
    shapes. A declared follow-up now inherits an omitted type while preserving explicit
    changes/new subjects. The prompt/configuration fingerprint changed to avoid reusing
    plans made before this correction. A real grouped-lookup API follow-up returns open:30.
    Strict backend validation stays intact.

29. **Standard suite.** Final standard regression: 311 passed, 145 skipped in 7.90s.
    Final focused question/executor suite: 82 passed, including three real PostgreSQL scenarios,
    in 175.74s. Existing TestClient deprecation only. An intermediate focused run failed
    because its new COUNT_DISTINCT case used the test helper's unrelated amount question;
    the corrected helper and clean final run pass.

30. **Full DB suite.** The replacement DB-enabled full regression completed successfully:
    453 passed, zero failures, one existing TestClient deprecation warning, in 3290.70s
    (54m 50s). Durable results: `phase8b-full-db-results.xml` and `phase8b-full-db-output.txt`.
    It started before the final follow-up correction and its three additional pure cases;
    the subsequent 82-test focused suite and 311-pass standard suite cover the final source.
    This scope difference is explicit: the full run is not claimed to include that correction.
    The earlier interrupted session had no recoverable completion result and is superseded.

31. **Frontend validation.** Lint/build passed; existing >500KB bundle warning remains.
    All 20 lightweight scripts completed successfully, including fixture generators and
    question states/ownership/chart/table tests. Tracked diff and 21 new phase text files
    passed whitespace checks.
    Production component replay uses real persisted fixture answers. The live-evidence variant
    uses a real Gemini typed plan and backend result; browser requests replay saved responses.

32. **Manual integration.** Real API fixtures: distinct-ID count 2, literal total 30 after
    an explicit question-local choice, direct parent breakdown `open:30`, immutable read and
    retry reuse. Pure engine fixtures verify time series/comparisons/ranking/no-data.
    Browser replay verifies production controls/layout/provenance/context. Real centralized
    Gemini produced the safe COUNT_DISTINCT(id) plan: result 2, FRESH, persisted/API read,
    same answer ID on retry, one planner call and zero approval events. The live commerce
    walkthrough exercises A–L with existing metadata; A/B/E correctly require Gold refresh,
    D asks a business clarification, I/J block injection/forecast, and L uses workspace 15
    without commerce references. C/F/G/H cannot produce trusted results from missing or
    unresolved dimensions/context. K cannot verify empty-period arithmetic without current
    Gold; deterministic empty-period tests cover it. See `phase8b-live-walkthrough.json`.
    The full UI and fixture replay check M (390px). Live Gemini also planned an explicitly
    literal sum of child amount grouped by parent status through the confirmed ONE_TO_ONE
    relationship: open:30, two current Gold sources, fresh API read, retry reuse, zero approvals.
    The production Ask component additionally called a real temporary loopback fixture API:
    form submission returned 2; calculation/Gold provenance displayed; desktop and 390px
    had no overflow; no approval dialog appeared. The fixture server/schema were cleaned up
    after the successful 141.42s check. See `phase8b-live-ui-complete.json` and
    `phase8b-live-ui-results.xml`. This latter verification does not replay responses.

33. **Live clarification observations.** Commerce 16 has no current executable metrics/
    trusted Gold inputs and Ask readiness NEEDS_REFRESH; no values were fabricated. Real
    development counts remain 26 datasets, 41 states, seven Gold runs, 54 candidates,
    zero reviews/approved definitions/metric results. These seven core counts match before
    and after migration and after live checks. The new answer table was empty immediately
    after migration; only new owned live question history is appended afterward.
    Live Gemini initially over-clarified synthetic cross-dataset literal questions and
    incorrectly classified some supported requests; no model output was forced through.

34. **Known limits.** No categorical dictionary, semantic definition editing, raw-row export,
    forecast, unrestricted joins/SQL, causal inference or currency inference. Conservative
    literal language resolution and synchronous bounded execution remain V1 constraints.
    Gemini may conservatively clarify a supported literal question; backend safeguards are
    retained. The commerce empty-period request safely failed at the planner boundary in
    the walkthrough; a read-only probe subsequently returned a strict explicit-date plan
    with business ambiguity. No empty-period value is claimed without current Gold.
    The full regression's earlier-source scope is disclosed in item 30; the final correction
    is independently covered by the completed focused and standard suites.

35. **Architecture deviations.** Quarter/year/calendar filtering/ranking/comparison are a
    deterministic adapter around the Phase 8A executor, not an AI arithmetic or SQL engine.
    Exact source frames are copied before date adaptation. Temporary definitions remain
    outside the trusted metric registry. No Phase 8A/domain semantic rewrite was made.

36. **Approval readiness.** Ready for final Phase 8B review/approval, with the V1 limits and
    test-scope distinction above. Migration, API startup, live single/direct-cross execution,
    real HTTP frontend, full DB regression and final diff/whitespace checks passed. No commit.
    The user explicitly approved 0024 for ai_data_workspace/public. Before applying, exact
    database/schema and absent answer table were checked and the entire migration inspected:
    only additive CREATE statements, no ALTER/backfill/business DML. Application was atomic.
    Verified all ten columns/types/nullability/defaults; primary/unique keys and two implicit
    indexes; two explicit indexes (descending history and successful-answer cache); seven
    checks (three hash lengths, positive algorithm, two JSON object types and status enum);
    workspace and same-workspace previous-answer foreign keys; owned BIGSERIAL sequence;
    enabled BEFORE UPDATE/DELETE immutability trigger and raising function. See
    `phase8b-migration-verification.json` for exact catalog definitions and unchanged counts.
    Only the verified DataRise uvicorn process was restarted; no old API was running at
    initial startup. Startup, /health, /health/db, readiness and history reads passed before
    live questions. No existing rules/definitions/relationships/metrics/Gold/prior answers
    were modified. The only development data added is new owned answer history.
