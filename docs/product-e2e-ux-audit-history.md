# DataRise product UX reset and acceptance audit

Status: IN PROGRESS. No release recommendation or completed acceptance is claimed.

## Scope and evidence

Audit started 2026-10-14. Existing uncommitted Phase 9 work is preserved. No commit has been performed. The explicitly approved additive constraint migration 0026 was applied as documented below. Browser checks use the real project frontend and API; fixture-file calculations are distinguished from application results.

Supplied synthetic pack: `C:\Users\Amir Bensekar\Downloads\datarise_retail_demo_pack`. Offline counts, sums, distributions and exact duplicate equality are recorded in `product-e2e-pack-baseline.json`. These are expected values, not evidence of successful processing.

Dedicated workspace: DataRise Retail E2E (17). Customers dataset (31), baseline delivery (87), created/uploaded through the real UI. Inspection observed 250 rows. Backend policy: UPSERT, customer_id, additive inspection schema policy, no event ordering; latest applied delivery wins. Supplied signup_date is a date, not an update timestamp.

## Reproduced findings before redesign

| Finding | Classification | Severity | Evidence |
| --- | --- | --- | --- |
| Existing commerce SQL catalog has zero queryable sources; Add Column and Run are disabled | CORRECTLY_BLOCKED | — | Real catalog and editor validation require current cumulative Gold |
| Usable-looking Builder remains visible with several repeated warnings and no direct preparation guidance | POOR_UX / MISSING_GUIDANCE | P2 | screenshots/product-audit/before-sql-unavailable.jpg |
| Editor accepts typing but correctly rejects a stale Orders source | CORRECTLY_BLOCKED | — | Validation: Orders analytics and profile need refresh before this query can run |
| Refresh sources reloads catalog metadata rather than rebuilding Gold, without making this distinction clear | MISSING_GUIDANCE | P2 | Existing SQL unavailable state |
| Workspace chrome repeats breadcrumbs, title, description, counts and nine tabs above SQL content | POOR_UX | P2 | Before SQL screenshot |
| Analytics displays zero-current summary cards and prominent blocked metrics without one preparation action | POOR_UX / MISSING_GUIDANCE | P2 | screenshots/product-audit/before-analytics-ask.jpg |
| Old failed/clarification answers dominate the current Ask input | POOR_UX | P2 | Before Analytics/Ask screenshot |
| Browser contract is configured while authoritative backend policy remains unconfigured, requiring a separate inspected schema step | MISSING_GUIDANCE | P2 | Real Customers contract → Processing → backend policy journey |

## Environment interruptions

The pre-existing project API stopped during dataset creation. After restarting only the verified project API, an authoritative dataset read showed no persisted dataset; the UI retry created Customers once. This is recorded as an environment interruption, not attributed to a product defect without evidence. The API health and database connection checks passed after restart.

## Acceptance progress

Baseline processing, ready-state SQL/Ask, relationships, metrics, duplicate/APPEND/UPSERT checks, isolation, mobile/accessibility, security, UX changes and final regression remain pending. No historical test result is used as final validation for this milestone.

## Calendar-date processing defect and approved migration

The first Customers run passed 250 rows through Silver but rejected all 250 during trusted-state identity normalization: DATETIME conversion turned calendar dates into timezone-free timestamps. Timestamp rejection is correct; the missing explicit calendar-date conversion prevents a truthful date-only flow. The initial empty application/state remains immutable evidence in workspace 17. Screenshot: `screenshots/product-audit/calendar-date-rejection-before.jpg`.

Added explicit DATE rule conversion, accepting only actual calendar dates and exact valid YYYY-MM-DD text; time-containing strings and datetime objects are rejected rather than truncated or assigned a timezone. DATETIME behavior and identity timezone safeguards remain unchanged. DATE answers are explicit choices and can be frozen in approved rules. Four focused tests pass, including isolated DB persistence and Parquet/UPSERT insertion-update lineage. The earlier 21-case transform/identity regression passed.

With explicit user approval, 0026 was applied once to verified ai_data_workspace/public. It replaces only `chk_business_rule_answer` with the same existing allowlist plus DATE. No tables, columns, indexes, triggers, rows, approved policies, definitions, relationships or artifacts were changed by migration. All twelve checked core-table row counts remained identical; exact before/after evidence is `product-audit-date-migration-verification.json`. API startup had already loaded the date fix; post-migration health checks passed.

Corrected fixture workspace: Retail Acceptance (18), Customers (32), delivery 88. The earlier workspace is retained to avoid rewriting successful/immutable application history.

## Current verification checkpoint

Final full DB-enabled backend suite after 0026 and the specific Ask recovery diagnostic: **582 passed, zero skips/failures/errors**, one existing TestClient deprecation warning, 7202.37 seconds. Completion exit code 0 is durably recorded in `product-audit-final-full-db-completion.json`; XML is `product-audit-final-full-db-results.xml`. Standard suite: **428 passed, 154 DB opt-in skips**, zero failures/errors, `product-audit-standard-results.xml`. No backend implementation changed after this full run started.

Real corrected Customers flow: 250 inserted, zero conflicts/rejections, current cumulative Gold 250, current factual profile 250 rows/eight columns, configured unique customer_id and date-only signup_date with no inferred timezone. Current Gemini dataset suggestions were generated using redacted metadata; SQL catalog exposes seven nonsensitive columns and marks customer_name unavailable.

Ready-state Builder: actual Add Column, customer_id selection, COUNT DISTINCT, SQL preview and Run returned **250**, Current, one row, approximately 2120 ms. Screenshot `screenshots/product-audit/sql-customers-count-distinct.jpg`. Editor: actual typing, authoritative validation, format and execution returned **250**, Current, approximately 2050 ms; saved query name Retail Customer Count. This is an additional safe Customers check, not a substitute for the pending Orders 1000/1050 acceptance.

User explicitly approved allowing negative values in validation for the dedicated synthetic retail fixture. This applies to fixture numeric rule questions only; no existing business rules are changed. Non-key business requirements are not inferred from observed complete data.

Frontend first complete current run: 25 of 26 passed; one remaining wording assertion in ux-consistency was updated to check the safe backend error and preserve recovery/no-stale-content assertions. Its rerun passed. A final all-script run after the remaining browser verification is still required. Current lint/build passed; existing large-chunk warning remains.

## Resumed live acceptance checkpoint

Products (33), baseline delivery 89: approved rule version 1, UPSERT product_id with no event ordering; 80 inserted, zero updates/duplicates/conflicts/rejections, current cumulative Gold 80 and current profile. Products metadata-only Gemini analysis was explicitly approved after automatic approval review requested clearer transmission authorization.

Orders (34) was created through the UI and baseline CSV inspected/uploaded: 1000 rows, 10 columns. Orders and Payments contain timezone-free timestamps; Returns contains calendar dates. The user's selected text-preservation policy applies to all three fixture fields. User explicitly approved preserving these fields as text with no event-time ordering; no timezone or truncation is inferred.

Final frontend lightweight rerun: all 26 scripts passed. Lint passed and production build passed, with the existing >500 kB bundle warning. Generated fixture preview files were removed and the tracked Phase 8A HTML restored exactly to HEAD. git diff --check passed (line-ending advisory warnings only).

User additionally approved required fixture foreign/reference keys for the six supplied relationships, and metadata-only Gemini analysis for the synthetic fixture. Orders baseline delivery 90 completed with 1000 inserted, 0 duplicates and 0 incremental conflicts. Its DATE_TIME suggestion was explicitly answered NO to preserve timezone-free source text. OrderItems (35) was created and its 3081-row baseline uploaded through the UI; processing is at Bronze/rule review.

## Reproduced Gemini type-context defect

Orders analysis failed twice with INVALID_OUTPUT. A diagnostic using only the same approved redacted profile identified TYPE_ROLE_CONTRADICTION: Gemini selected TIME_DIMENSION for retained order_date TEXT while deterministic datetime_candidate=false. No invalid output was persisted or partially trusted. The handoff omitted datetime_candidate, and the prompt did not express the validator's exact role eligibility.

Fixed the centralized handoff to include that boolean in full and compact requests and clarified primary/secondary type eligibility in the existing Gemini system prompt. Strict validation is unchanged. The provider strategy fingerprint is advanced to v2-type-evidence so old suggestions remain historical and require regeneration under the corrected configuration; business rules, relationships, metrics and source artifacts are not edited. Two new meaningful tests check false text/true typed time evidence in full/compact mode and continued rejection of text time roles. Affected suites: 59 passed, 12 DB opt-in skipped.

Only the verified project API was restarted, then health and database-readiness passed. Exact DB target reverified ai_data_workspace/public. A subsequent complete RUN_DB_TESTS=1 regression completed, with distinct product-audit-type-evidence-full-db artifacts; the earlier 582-pass run is now historical because this backend fix followed it. UI readiness Retry recovered the deliberate restart interruption.

## 2026-10-15 resumed verification

Latest full DB regression after the planner-context fix: **584 passed**, zero failures/errors/skips, one existing Starlette TestClient warning, 7133.81 seconds. Exit 0; completed 2026-10-14 18:00:39 UTC. Evidence: product-audit-type-evidence-full-db-results.xml and completion JSON. Current standard backend: **430 passed, 154 DB opt-in skipped**, zero failures/errors. No backend code changes after these runs.

Real browser verification of corrected Orders understanding succeeded: order_date is TEXT_ATTRIBUTE, explicitly citing datetime_candidate=false and refusing TIME_DIMENSION. Evidence screenshot orders-text-time-understanding.jpg.

OrderItems baseline completed: 3081 inserted, zero duplicates/conflicts, current cumulative Gold 3081, current profile and v2 Gemini interpretation. Payments baseline completed: 1078 inserted, zero duplicates/conflicts; cumulative Gold now current with 1078 rows. Its all-status payment sum is 51,841,026.56, distinct from the pending Paid-only 43,584,403.88 check. Returns baseline delivery 93 inspected 47 rows, approved required reference keys and no extra uniqueness on order_id, explicit text date and numeric/sign rules; backend policy and publication still pending at this checkpoint.

## Ready baseline, relationships and analytics

All six live dataset and cumulative Gold counts match: Customers 250, Products 80, Orders 1000, OrderItems 3081, Payments 1078, Returns 47. Pinned API evidence: product-audit-live-baseline-readiness.json. All profiles and current Gemini dataset interpretations completed; workspace understanding generated from all six.

The six supplied endpoint pairs passed deterministic verification: configured unique/non-null parents, non-null child references, 100% distinct child coverage. Confirmed via actual UI one at a time. Returns → Orders is observed one-to-one in this sample (47 distinct order references); the expected potential one-to-many business multiplicity was not forced. No extra candidate was confirmed.

Metrics: 20 proposed, 17 REVIEW_RECOMMENDED, 3 REVIEW_REQUIRED, 0 AUTO_ACCEPT. The 17 optional-review metrics calculated successfully without an approval dialog, all current. Three blockers remain: total order_amount requires uniform currency/standards meaning; total and average refund_amount have unresolved column warnings. Exact metadata is product-audit-live-metric-decisions.json. No ambiguous definition was approved.

Orders Builder built the specified status + COUNT(*) order_count + SUM(order_amount) total_amount, GROUP status, SORT count descending, LIMIT 100, and returned Completed790/Processing100/Cancelled63/Returned47. Screenshot sql-orders-builder-baseline.jpg. Literal status=Completed filter returned one group790; filter removal, column add/remove/reordering worked. Query saved as Retail Orders by Status. Editor validation, format and run matched those four counts; saved as Retail Order Counts. SQL amounts are DOUBLE and expose floating-point tails (e.g. 41,036,543.07000004); no calculation semantics were changed to hide this limitation.

## Additional real interaction defects

P2 Editor caret/undo reset: confirming relationships changes SQL catalog metadata. SqlEditor rebuilt CodeMirror for any catalog object change, losing selection and undo; autocomplete inserted an alias at the start of existing SQL. Fixed by preserving one EditorView and reconfiguring a Compartment only when completion metadata changes. New real-CodeMirror lightweight lifecycle test verifies strict mount, SQL language, catalog refresh, source eligibility changes, document/caret/undo preservation. Clean live browser autocomplete inserted SELECT * FROM orders_d34 correctly.

P1 canonical Ask planner grounding: initial How many orders are there? was blocked. Persisted answer43 shows COUNT with base_dataset_id=null, aggregation=null, measure=null. Backend rejection was correct. The planner contract failed to make required literal count references explicit. Added supported entity-count plan references for current single non-sensitive, unwarned identifiers with strong entity evidence, plus explicit COUNT binding instructions and a new planner strategy fingerprint. Existing validation, grain/freshness/business guards remain unchanged. Focused question suite58passed, including excluded uncertain/composite/sensitive/warned keys and normal resolution of the supplied reference. No metric or business approval was fabricated.

The project API was restarted only after active calculations completed, then health/DB readiness and exact ai_data_workspace/public were verified. A fresh full DB regression after this backend fix is running under product-audit-count-planner-full-db artifacts; the prior584pass result is now historical.

## Complete live Ask contract and grouped/follow-up defects

The explicit count-reference hint alone did not fix live counts: API answers44–47 still contained an empty COUNT intent. A diagnostic traced the actual model response to only question_type and top_n. The transport schema allowed omission of optional domain fields. The centralized provider now exposes a response_schema hook; only WorkspaceQuestionPlanner requires every intent field in its transport schema, using null for unused references. The response type remains WorkspaceQuestionPlan, and backend validation stays authoritative. Planner strategy v3-complete-intent invalidates earlier incomplete-plan request caches. Focused provider/question tests93passed/7DBskipped.

Actual UI canonical question now answers1000 without approval, with current pinned cumulative Gold. Screenshot ask-orders-baseline.jpg. Repeating the question preserved the answer-history count, consistent with saved-result reuse; final precise API reuse evidence remains to record.

The exact Show orders by status question then revealed a separate grounding bug: an explicit COUNT_DISTINCT configured key was recognized as an entity count only for question_type COUNT. A breakdown was rejected for not spelling order_id. Extended the grounded entity-key rule to supported output shapes with strong entity evidence, preserving wrong-entity, non-key, uncertainty and runtime uniqueness/fanout checks. Three new tests cover GROUPED/BREAKDOWN/RANKING. Actual UI now returns and renders the correct four status groups; screenshot ask-orders-status-baseline.jpg.

The exact What about by channel? follow-up was rejected because contextual grounding recognized pronouns but not elliptical breakdown wording. Added only the what-about-by form whose remaining words exactly match supplied breakdown columns, with owned previous context required by the service. Refresh now preserves an already stored follow-up intent rather than resetting it to NEW. Pure focused question suite62passed; real DB persisted-follow-up/refresh test is running. No arbitrary text is treated as contextual, no category filter or business definition is invented. The old full run was stopped after these new shared fixes and is not final proof.

SQL public API adversarial smoke: all20cases passed. Safe SELECT/CTE/aggregates/confirmed child→parent lookup succeeded; DROP/INSERT/multiple statements/ATTACH/external Parquet/CSV/network/system catalog/control-plane/other-workspace/comment-wrapped DROP blocked. Both no-LIMIT and oversized LIMIT returned500rows with truncation. Baseline channels: Mobile App431/Web391/Marketplace178; sum order_amount52732132.900000006; literal Paid payment sum43584403.880000055. Confirmed Orders→Customers lookup region counts South280/West263/North266/East191 sum1000. Exact current API evidence: product-audit-live-sql-smoke.json.

Saved queries survived full browser reload: Retail Order Counts, Retail Orders by Status, Retail Customer Count. Opening a saved query loaded Editor text without executing. Opening history displayed metadata only, with no retained result rows and an explicit rerun action. Editor→Builder correctly showed a guard; Keep SQL Editor preserved the query.

## Resume checkpoint: Orders APPEND and fresh regression

Actual UI uploaded orders_incremental_50.csv as delivery94 and processed only Orders. Existing rule/policy versions were reused. Application49 published trusted state49 version2: 50 input/valid/inserted, zero duplicates/rejections/conflicts; Orders now1050. Old cumulative Gold and seven Orders metrics became stale; ten unrelated dataset metrics remained current and three definition blockers remained blocked. Exact before/after public API evidence is product-audit-{pre,post}-append-{analytics,questions,relationships}.json and post-append-orders_processing.json. Ask explicitly returned Refresh needed, requesting current profile/understanding. Screenshots ask-orders-stale-after-append.jpg and orders-stale-after-append.jpg preserve the state. Actual Refresh Analytics rebuilt cumulative Gold: current1050, SUM(order_amount)54,121,293.78; profile/understanding and dependent refresh still pending.

The live payment topic reset succeeded: answer53 is context_mode NEW, base Payments36, SUM(payment_amount), grouped payment_status, provenance only Payments state47/Gold12. Failed0/Paid43,584,403.88/Pending3,255,482.75/Refunded5,001,139.93. Channel follow-up178/431/391 and payment reset screenshots were captured. Summary disclosures require text/summary locators; failed button-role automation selectors were not application defects.

Latest standard backend435passed/154DBskipped, current frontend28/28 scripts passed from frontend working directory, lint/build passed. Previous full DB run was interrupted by session pause at23% without assertions; it is archived and is not final proof. Fresh RUN_DB_TESTS=1 complete589-test regression started2026-10-15T15:39:03Z under product-audit-release-full-db files, process23884. No implementation changed since its start. Customers UPSERT and final responsive/isolation/evidence/report work remain incomplete.

## Current resume: incremental verification succeeded

Orders profile refreshed1050 and approved metadata-only Gemini regenerated successfully; order_date remains TEXT_ATTRIBUTE with datetime_candidate=false. Actual Ask now answers How many orders are there?1050 with no approval dialog (ask-orders-after-append.jpg). Show revenue. correctly returns Needs clarification and explicit measure choices; no revenue assumption selected (ask-revenue-clarification.jpg). Show total order amount. also requested measure selection; explicit Total order amount in Orders clicked, final outcome pending.

Actual UI Customers uploaded customers_upsert_15.csv,15rows8columns1085bytes,100%contractcompatibility. Process Pending reused approved ruleversion1 with no new rule approval. Completed successfully15input15Silvervalid,5insert10update0unchanged/duplicates/conflicts/rejections; currenttrusted255. Screenshot customers-upsert-processing.jpg. Subsequent Refresh Analytics completed cumulative Gold255, current. Profile refresh not yet started at checkpoint; semantics/relationship/metrics refresh pending. Latest readonly API snapshots product-audit-resume-{questions,analytics,customers_processing}.json captured before final completion; do not infer final current pins from the early snapshot.

Release full DB regression continues,10% at latest log read, no failures observed. No code edits this resume; only audit evidence/report changed.
