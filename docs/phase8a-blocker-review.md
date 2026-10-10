# Phase 8A focused engineering review

1. **Root cause of the 16 blocked metrics.** Read-only review of workspace 16, immutable
   discovery run 4, candidates 39–54. Every candidate has `validation_status=REVIEW_REQUIRED`,
   `decision=REVIEW_REQUIRED`, and an explicit nonempty `unresolved_assumptions` list supplied
   upstream. Phase 8A therefore returns `DEFINITION_REQUIRED` / BLOCKED before source IO.
   All definition/dependency freshness checks are CURRENT. Independently, every required
   source has a published current state but no current cumulative Gold run and
   `state_analytics_status=STALE`. Gold absence is a second blocker on all 16, not the reason
   the API chooses DEFINITION_REQUIRED as the first blocker. No candidate has a filter,
   relationship, validation error or unsupported expression. Source states exist.

   Exact stored reasons follow. B = unresolved business meaning/definition annotation;
   G = missing cumulative Gold (secondary on every row). S is the additional exact reason
   “Business definition requires explicit scope, units or category meaning.” W is
   “Column meaning has unresolved warnings.” Every “No” means the immutable definition
   cannot reasonably execute **as currently stored under current product rules**. The last
   column distinguishes a safe literal replacement from approval of the current assumptions.

   | ID | Metric name / source | Decision | Execution eligibility | Exact first blocking reason (`DEFINITION_REQUIRED`) | Category | Execute as stored? / literal alternative when current |
   |---|---|---|---|---|---|---|
   |39|Record count / Customers|REVIEW_REQUIRED|BLOCKED|“Customer dataset represents unique active customer entities.” + S|B + G|No; active-customer scope requires a business answer.|
   |40|Total amount / Orders|REVIEW_REQUIRED|BLOCKED|“Order amount values are additive.”|B + G|No; a literal SUM(amount) is supported without interpreting revenue.|
   |41|Average amount / Orders|REVIEW_REQUIRED|BLOCKED|“Order amount is numeric and mean is a valid measure.”|B + G|No; literal AVG(amount) is supported.|
   |42|Minimum amount / Orders|REVIEW_REQUIRED|BLOCKED|“Zero or negative values are valid domain inputs.”|B + G|No; literal MIN(amount) needs no new negative-value business rule.|
   |43|Maximum amount / Orders|REVIEW_REQUIRED|BLOCKED|“Order amounts are free of arbitrary unhandled scaling factors.”|B + G|No; literal MAX(amount) operates on stored numbers without claiming currency/scaling semantics.|
   |44|Distinct customer_id count / Orders|REVIEW_REQUIRED|BLOCKED|“Customer ordering activity is captured via customer_id references.”|B + G|No; literal COUNT_DISTINCT(customer_id) needs no join or customer-activity assumption.|
   |45|Total amount / Payments|REVIEW_REQUIRED|BLOCKED|“Payment amount values are additive.”|B + G|No; literal SUM(amount) is supported.|
   |46|Average amount / Payments|REVIEW_REQUIRED|BLOCKED|“Payment amount is numeric and mean is a valid measure.”|B + G|No; literal AVG(amount) is supported.|
   |47|Distinct order_id count / Payments|REVIEW_REQUIRED|BLOCKED|“Multiple payments can reference the same order_id.”|B + G|No; COUNT_DISTINCT already handles repeated IDs; this is not a relationship blocker.|
   |48|Record count / Products|REVIEW_REQUIRED|BLOCKED|“Product catalog entries map to active distinct items.” + S|B + G|No; active-product scope is unresolved.|
   |49|Record count / Patient Admissions|REVIEW_REQUIRED|BLOCKED|“Patient admissions dataset is scoped correctly relative to commerce datasets.” + S + W|B + G|No; mixed clinical/commerce scope and referenced-field meaning require review.|
   |50|DIVIDE(SUM(amount), COUNT()) / Orders|REVIEW_REQUIRED|BLOCKED|“Denominator is non-zero in production evaluation.”|B + G|No; literal division already specifies zero_denominator=NULL, so this assumption is unnecessary for calculation.|
   |51|DIVIDE(SUM(amount), COUNT()) / Payments|REVIEW_REQUIRED|BLOCKED|“Denominator is non-zero in production evaluation.”|B + G|No; the same explicit null-on-zero rule applies.|
   |52|Distinct product_id count / Orders|REVIEW_REQUIRED|BLOCKED|“Product identifiers match catalog items.”|B + G|No; literal distinct count requires neither catalog matching nor a relationship.|
   |53|Record count / Orders|REVIEW_REQUIRED|BLOCKED|“Orders dataset primary key guarantees entity uniqueness.” + S|B + G|No; ENTITY grain cannot borrow the canonical RECORD-grain label. Explicit COUNT() with proven entity key, or a literal distinct-ID definition, is supported.|
   |54|Record count / Payments|REVIEW_REQUIRED|BLOCKED|“Payments dataset primary key guarantees entity uniqueness.” + S|B + G|No; the same explicit grain/label requirement applies.|

2. **Counts by blocker category.** Categories overlap; do not add the secondary Gold count
   to the first-blocker count.

   - Stored policy classification: 16 unresolved definition/business annotations; 16 secondary
     missing-Gold blockers; zero categorical filters, relationship blockers, grain/fanout
     validation failures, unsupported expressions, missing source states or implementation defects.
   - A: engineering assessment identifies **three clearly substantive business/sensitive-scope
     ambiguities** (39, 48, 49). The remaining **13 are primarily conservative proposal
     annotations or the ENTITY-vs-RECORD naming restriction**, not 13 demonstrated business
     ambiguities. Transparent arithmetic could be proposed without those irrelevant claims;
     that does not authorize editing or approving existing immutable candidates.
   - B: **zero blocked only by Gold freshness**; all 16 also have an unresolved definition.
   - C: **zero blocked by unsupported V1 execution capability**.
   - D: **zero blocked by an implementation defect found in this review**.
   - E: **yes**, a current literal count executes with no approval dialog. The intentional 7E
     AUTO_ACCEPT allowlist covers only `Record count`, RECORD-grain COUNT without filters or
     grouping. `Distinct id count` / COUNT_DISTINCT(id) is REVIEW_RECOMMENDED and still executes
     automatically during refresh. A business label such as `Total Orders` is not silently
     substituted for the literal technical definition.

   Diagnostic validation with assumptions removed (in memory only) produces 11 VALID /
   REVIEW_RECOMMENDED definitions; five entity-count definitions still require the explicit
   scope/label rule, and Patient Admissions retains the field-warning reason. No diagnostic
   definition was persisted, approved or executed. This isolates the discovery annotation
   issue from the Phase 8A executor; it is not a business-answer workflow.

3. **Whether blocking behavior is correct.** Yes for the stored definitions and current
   product policy. The executor must not discard provider-declared unresolved assumptions.
   Missing Gold also correctly prevents current values. The blanket live outcome is poor
   evidence of useful discovery, but is not evidence that safe AUTO_ACCEPT or optional-review
   metrics cannot execute. The 7E documented narrow allowlist and assumption policy explain
   the observed behavior. Improve proposal quality as separately scoped work; do not broaden
   trust or invent answers merely to make live cards display numbers.

4. **Safe single-dataset execution result.** A dedicated temporary PostgreSQL schema uses
   the existing ingestion/processing/profile/semantic/discovery services and builds actual
   cumulative Gold Parquet in the test suite's in-memory S3 adapter. Fixed synthetic records
   contain two non-null IDs. Definitions are discovered, deterministically validated and
   persisted, not injected directly into a result table. `Record count` is AUTO_ACCEPT;
   `Distinct id count` / COUNT_DISTINCT(id) is REVIEW_RECOMMENDED. Both have current definitions,
   dependencies and Gold; no relationship/filter/assumption is required. Real execution
   plans were captured and the production executor returned **2** for each. Two terminal
   FRESH results are persisted. GET analytics reads both values; POST retry for each preserves
   result IDs and does not call the executor again. Metric reviews remain zero. Execution
   calls no AI provider. The production WorkspaceAnalyticsContent component renders the
   actual API response with both numeric values, Current, Ready automatically and Review is
   optional; no Approve or Review Metric action/dialog. Saved HTML is static SSR evidence,
   not a hydrated development page; its controls are explicitly inert. It was visually
   inspected in the browser. No mocked metric output or invented business label is used.

5. **Safe cross-dataset execution result.** Live relationship 6, Customers.customer_id →
   Orders.customer_id, is CONFIRMED / CURRENT / CURRENT, version 1, ONE_TO_MANY. A live
   execution would additionally require both missing Gold runs and a current safe metric
   definition. No live definition or Gold was changed. Instead a dedicated isolated fixture
   uses real relationship discovery and explicit fixture confirmation after all semantic
   inputs are current. Its ONE_TO_ONE direct child-to-unique-parent lookup is confirmed and
   current. A literal `COUNT()` grouped by the parent's `status` is VALID /
   REVIEW_RECOMMENDED, requires no filter or category interpretation, and reads current
   cumulative Gold for both sources. It preserves two base rows, returns **open = 2**,
   persists one FRESH result and reads it through the API. Retry reuses the result; metric
   reviews remain zero. This proves the supported lookup path without changing live business
   meaning or claiming a live commerce cross-dataset result.

6. **Any defect found/fixed.** No production implementation defect found; no implementation
   changed. Added read-only audit tooling, two meaningful isolated end-to-end tests, an actual
   API-to-production-component display test and evidence/report artifacts. During test
   authoring the cross fixture was adjusted to confirm its relationship after current
   semantic evidence was generated, matching the real freshness contract. Live public counts
   remain 26 datasets, 41 states, seven Gold runs, 54 candidates, zero metric reviews/approved
   definitions, and zero workspace metric results. No AI calls, live uploads or approvals.

7. **Final focused test results.** Focused Phase 8A backend run: **28 passed** (24 execution
   cases, three existing real-PostgreSQL lifecycle cases, one new single-dataset end-to-end
   fixture), 175.55 seconds. New real-PostgreSQL confirmed cross-dataset test: **1 passed**,
   74.43 seconds. All **18 frontend lightweight scripts passed**, including the actual API
   fixture render. Existing Starlette/httpx TestClient deprecation warning only.
   `git diff --check` passed. No product UI was modified.

8. **Whether Phase 8A is ready for final approval.** Yes for engineering approval of safe
   execution, result lifecycle and frontend value display. The all-blocked live workspace is
   explained by immutable discovery assumptions plus missing Gold, not a demonstrated Phase
   8A defect. Live commerce KPI readiness remains upstream work. Review the evidence before
   committing; no commit or next-phase implementation was performed.

Evidence: `docs/phase8a-blocker-review.json`, `docs/phase8a-safe-fixture.json`,
`docs/phase8a-safe-cross-fixture.json`, `docs/phase8a-safe-fixture.html`, and
`screenshots/phase8a/safe-fixture-display.jpg`. Added verification sources:
`tools/phase8a_blocker_review.py`, `tests/test_phase8a_safe_fixture.py`,
`frontend/tests/phase8a-fixture-display.cjs`.
