# DataRise product UX reset and end-to-end acceptance audit

Audit date: 16 October 2026 (Asia/Calcutta). Recommendation: **READY WITH MINOR ISSUES for final engineering/product review**. No commit or staging was performed. Phase 10 was not started.

This report supersedes the chronological checkpoints in `product-e2e-ux-audit-history.md`. Earlier test counts and pending statements in that history are historical, not the final validation result. Existing uncommitted Phase 9 work was preserved.

## Executive summary

The core journey was exercised against the real React frontend, project API, PostgreSQL control plane and private artifact storage using the supplied synthetic retail pack. Baseline ingestion, approved fixture rules, processing, profiles, AI interpretation, relationships, metrics, Ask, Builder, Editor, saved queries, APPEND and UPSERT all produced concrete results. No database seeding bypassed the ingestion UI.

The reported Add Column issue combined a correct missing-source block with poor readiness guidance. Editor lifecycle and Ask planning/composition had real defects. Those defects were fixed without weakening freshness, relationship, fanout, sensitivity, metric validation or approval requirements. A final live check also found SQL polling could overlap slow reads and retain a recovered connection error; polling now waits for the outstanding batch and successful recovery clears the obsolete error.

Final evidence: full DB suite **589 passed**; standard backend **435 passed / 154 DB opt-in skipped**; frontend **29 lightweight scripts passed**, lint and production build passed. SQL adversarial smoke **20/20 passed**. Final analytics has **14 current results and one legitimate definition blocker**. All six supplied relationships have current structural and verification evidence.

## Scope, approvals and environment

- Pack: `C:\Users\Amir Bensekar\Downloads\datarise_retail_demo_pack`.
- Working acceptance workspace: Retail Acceptance (18). The earlier DataRise Retail E2E workspace (17) retains the calendar-date failure reproduction; its history was not rewritten.
- The user explicitly approved fixture negative values, required supplied relationship keys, timezone-free event fields as TEXT with no event ordering, and redacted synthetic metadata analysis through the existing Gemini integration.
- The only audit migration was explicitly approved **0026** against verified `ai_data_workspace/public`. It replaces the existing rule-answer CHECK allowlist with the same values plus DATE. It adds no table, column, index or trigger and changes no rows. Counts in all twelve checked core tables were unchanged. See `product-audit-date-migration-verification.json`.
- Project services stopped across session pauses. Only verified-stopped project API/frontend services were restarted; health, DB readiness and exact database/schema were verified. Browser connection interruptions were recovered through visible Retry controls. No secrets or source rows were sent to Gemini.
- One retained browser error page could not be rebound because the browser URL-policy gate rejected its error-document protocol. That page was left alone; authorized audit pages and saved evidence remained usable.

## Root causes and fixes

| Finding | Classification / severity | Final behavior |
| --- | --- | --- |
| No current cumulative Gold/profile made commerce sources unqueryable | CORRECTLY_BLOCKED | Safety retained; preparation action explains the next step |
| Active-looking unavailable Builder, repeated warnings and ambiguous Reload catalog wording | POOR_UX / MISSING_GUIDANCE, P2 | One readiness callout; Builder unavailable; preparation link; metadata reload distinguished from analytics refresh |
| Search/select interactions and clause layout were difficult to use | POOR_UX, P2 | Shared accessible searchable selects; short clause labels; supported controls only |
| CodeMirror rebuilt on catalog metadata changes, losing caret and undo | FUNCTIONAL_DEFECT, P2 | Stable EditorView; completion compartment reconfigured; lifecycle test and live undo check |
| Gemini omitted optional intent fields and lacked explicit safe entity-count references | FUNCTIONAL_DEFECT, P1 | Existing centralized planner uses complete typed transport schema, explicit supported references, authoritative backend validation |
| Explicit entity-key count grounding only handled scalar COUNT | FUNCTIONAL_DEFECT, P2 | Strong entity grounding also supports validated grouped/breakdown/ranking shapes |
| Elliptical channel follow-up was not recognized; refresh reset stored context | FUNCTIONAL_DEFECT, P2 | Narrow supported wording with owned prior context; refresh retains validated stored intent |
| Metric-load failures remounted Ask and discarded an independent answer/session | FUNCTIONAL_DEFECT, P1 | Ask remains mounted independently; failed metric content is hidden with Retry |
| Calendar dates became timezone-free timestamps and failed trusted identity | FUNCTIONAL_DEFECT, P1 | Explicit strict DATE rule; timestamps still require existing safety semantics |
| AI handoff omitted deterministic datetime eligibility | FUNCTIONAL_DEFECT, P2 | Full/compact handoff includes datetime_candidate; invalid TEXT time roles still rejected |
| Slow SQL metadata polls overlapped; recovered errors remained visible | FUNCTIONAL_DEFECT / POOR_UX, P2 | Outstanding batches bound automatic polling; completed recovery clears stale error; failed checks still block execution metadata |
| Settings was an unexplained heading-only page | MISSING_GUIDANCE, P3 | Honest capability limitation and a working Open workspaces next action |

An additional P2 SQL result-association defect was reproduced during final saved-query testing: Duplicate changed the editor to a Customers draft but left a prior Orders result visible. Duplicate now clears the previous execution result, consistent with Open/New Query. The real browser rerun and an added component assertion both verify that the new draft has no unrelated result. No trusted data or execution calculation changed.

## Theme, navigation and workflow

Shared CSS tokens use the requested pale app background, white surfaces, dark sidebar, indigo primary and consistent success/warning/error colors. Focus-visible treatment and disabled-control cursor states are centralized. No new UI framework was introduced.

Global navigation is Home / Workspaces / Activity / Settings. Compact workspace sections are Overview / Data / Quality / Model / Analytics / SQL. Processing is under Data; relationships, understanding and advanced metric definitions are under Model. Dataset navigation exposes common pages with additional tools in a disclosure. Existing routes and domain semantics remain available.

Workspace progress is evidence-based: deliveries, trusted state, checks run, current confirmed relationships, current source analytics and current metric results. It does not manufacture completion percentages or treat checks run as a person's quality approval. The next action links to the actual missing preparation step.

## Page-by-page audit

Each row records purpose, visible evidence and recovery/next action. Desktop core pages and the specified mobile journey were inspected; this is not a claim that every possible backend failure was injected into every page.

| Page / surface | Purpose and validity | Clarity / next action / states observed | Responsive / accessibility / boundary |
| --- | --- | --- | --- |
| Home | Real owned workspace list and start guidance | View Workspaces; no fake KPIs | Responsive cards; real links |
| Workspaces | Create and select business boundaries | Create, search, open; loading and ready | Owned lists; workspace switch verified |
| Workspace Overview | Actual progress and next action | Six prepared datasets; current row counts; Explore analytics | Single-column mobile; no page overflow |
| Datasets | One logical entity per card | Six datasets; trusted rows and delivery status; search works | Mobile cards; named open links |
| Dataset Overview | Current dataset readiness and latest delivery | Customers255; two deliveries; current Gold; approved rules reused | Details disclosed; dataset-scoped links |
| Files / ingestion | Physical files and inspected deliveries | Baselines, duplicate rejection, APPEND and UPSERT inspection | No upload used as a substitute for a dataset |
| Rules / contract | Explicit validation and load policy | Required keys, DATE, retained text times, sign choice; policy per dataset | No inferred timezone, currency or approvals |
| Processing | Dataset-scoped delivery progress and retry | Eight successful accepted deliveries; none pending; stage/results disclosure | Mobile processing list; no unrelated rerun |
| Data Quality | Actual validation outcomes | Latest delivery Silver pass/reject and dataset-update rejects distinguished | Explicit checked-delivery context; review links |
| Profile | Factual current-state evidence | Orders1050; Customers255 distinct/nonmissing key; sensitive-name hint | Labels and disclosures; category labels redacted |
| Dataset Understanding | Suggested roles from bounded evidence | Current analyses; TEXT event times retained | AI suggestions do not approve rules or meaning |
| Workspace Understanding | Entity/process inventory | All six eligible/current; suggestion version2 | Redacted metadata only; uncertainty visible |
| Relationships | Current supplied key evidence and review | Six current confirmations; false candidates disabled; historical evidence labelled | 390px no page overflow; review/version boundary retained |
| Metric definitions | Review validated proposals | Current recommendations separated from required review | No ambiguous metric approval fabricated |
| Analytics | Trusted metrics and source updates | Final14 current;1 definition issue; refresh and details | Current values preserved; stale/failed states actionable |
| Ask | Typed question, clarification, answer, context | Baseline1000/current1050; follow-up; topic reset; refresh; suggestion; New analysis | Labelled input/status; mobile answer; no approval dialog for canonical count |
| SQL Builder | Visual read-only query construction | Add/remove/reorder, source, aggregation, alias, lookup, filter/group/sort/limit and run | 390px clause navigation; catalog drawer; disabled reasons |
| SQL Editor | Advanced validated SQL | Typing, completion, format, validate, run, undo across catalog refresh | Real CodeMirror; 390px/768px measured without page overflow |
| SQL results / saved / history | Bounded exploration and SQL metadata persistence | Counts, chart, search/sort/copy, saved queries, metadata-only history | Table headers; rows not persisted as trusted metrics |
| Dataset History / extra tools | Advanced dataset lifecycle access | Preserved under More dataset tools | No destructive history rewrite performed |
| Global Activity | Existing processing navigation retained | Dataset/workspace processing remains authoritative | No invented global activity values |
| Settings | Explain currently available settings scope | No account setting backend is invented; workspace next action | Standard accessible link |

## Button/action audit

Detailed logical action cases are in `product-audit-button-matrix.csv`, with page, action, expected result, actual result, status and notes. **Final totals: 53 PASS, 3 correctly BLOCKED, 0 FAIL across 56 representative logical action cases.** Counts refer to cases, not raw clicks; repeated six-dataset operations are not inflated into dozens of passes. Correct safety blocks are explicitly distinguished from failures. Destructive deletion was not completed; its confirmation/cancel behavior was exercised in the real browser without deleting saved audit data. Rename was saved and restored; Duplicate opened a draft and cleared the unrelated old result. These actions added no fourth saved query.

## Ingestion, processing and quality

| Dataset | Baseline trusted rows | Strategy / key | Current trusted rows | Accepted deliveries |
| --- | ---: | --- | ---: | ---: |
| Customers32 | 250 | UPSERT / customer_id | 255 | 2 |
| Products33 | 80 | UPSERT / product_id | 80 | 1 |
| Orders34 | 1000 | keyed APPEND / order_id | 1050 | 2 |
| OrderItems35 | 3081 | keyed APPEND / order_item_id | 3081 | 1 |
| Payments36 | 1078 | keyed APPEND / payment_id | 1078 | 1 |
| Returns37 | 47 | keyed APPEND / return_id | 47 | 1 |

Baseline deliveries88–93 and incremental deliveries94–95 were created/processed through the UI. All baseline counts matched the supplied pack; zero baseline incremental conflicts/rejections were observed. Repeated child order_id values in items/payments were not incorrectly deduplicated. Latest Orders delivery has50 Silver-valid/0 rejected; Customers15/0; unrelated datasets retain their original completed deliveries.

## Relationship and metric results

Six supplied endpoint pairs are confirmed with current structure and current verification: Customers→Orders; Orders→OrderItems; Products→OrderItems; Orders→Payments; Orders→Returns; Products→Returns. Configured parents are unique/non-null, child references are required/non-null and coverage is100%. Returns→Orders is observed one-to-one in the sample; potential business multiplicity was not forced. Other low-coverage candidates remain unconfirmed/disabled.

Metric discovery is generative and versioned. Baseline generation proposed20 definitions:17 REVIEW_RECOMMENDED and3 REVIEW_REQUIRED;17 calculated without approval. After source/semantic/relationship refresh, an intermediate generation had14 executable and6 required-review definitions. Final generation has15 definitions:14 REVIEW_RECOMMENDED,1 REVIEW_REQUIRED,0 AUTO_ACCEPT. All14 eligible outputs are current; Total refund_amount remains blocked because column meaning has unresolved warnings. These are distinct retained generations, not evidence that review was bypassed. No metric was manually approved merely to make a demo pass. See `product-audit-live-metric-decisions.json`, `product-audit-final-metrics.json` and `product-audit-final-analytics-reuse.json`.

## Deterministic analytics validation

| Check | Baseline | After incremental refresh |
| --- | --- | --- |
| Total Orders | 1000 | 1050 |
| Orders status | Completed790 / Processing100 / Cancelled63 / Returned47 | Completed840 / Processing100 / Cancelled63 / Returned47 |
| Orders channel | Marketplace178 / Web391 / Mobile App431 | Marketplace196 / Web405 / Mobile App449 |
| Literal SUM(order_amount) | 52,732,132.90 | 54,121,293.78 |
| Literal Paid payment amount | 43,584,403.88 | Unchanged source |
| Customers count/distinct key count | 250 / 250 | 255 / 255 |

The amount is labelled order_amount, not assumed Revenue. Ad hoc SQL uses DOUBLE in V1 and exposes floating-point tails for some sums; this was not hidden by changing financial semantics.

## Ask validation

“How many orders are there?” answered1000 at baseline and1050 after APPEND/current profile/understanding, with numeric persisted backend results and no approval dialog. Repeating the current question returned the same answer ID with reused=true. Refreshing the historical baseline answer produced a new current1050 answer and preserved old history.

“Show orders by status.” matched the baseline fixture. “What about by channel?” retained the owned Orders count intent. “Now show payment amount by payment status.” created NEW context, pinned Payments only, and returned Failed0 / Paid43,584,403.88 / Pending3,255,482.75 / Refunded5,001,139.93. Selecting the Record count suggestion populated the input and returned Customers255. New analysis clears the current context.

“Show revenue.” required clarification. “Show total order amount.” was conservatively clarified in the earlier generation; selecting the exact literal Orders measure answered54,121,293.78 for that question only. No revenue, currency, payment or refund policy was invented.

## SQL Builder and Editor validation

The requested visual baseline query used Orders, status, COUNT(*) AS order_count, SUM(order_amount) AS total_amount, GROUP status, descending count and LIMIT100. It returned the four expected baseline groups. Completed filtering returned790; removing the filter restored all groups. Add/remove/reorder, dataset, aggregation, alias and supported clauses worked.

A current confirmed Orders→Customers lookup was built through the UI and returned1050; query details named both sources and the confirmed lookup. Editor typing/completion/format/validation/execution worked; saved status SQL returned840/100/63/47 after APPEND. Three saved queries survived reload/switch. History retains execution metadata and SQL with explicit rerun, not result rows. Editor→Builder guards unsupported conversion. A real inserted comment was undone after catalog reload, confirming undo continuity; the lifecycle test additionally covers caret/completion eligibility changes.

## Idempotency and incremental lineage

- Exact physical Orders duplicate was blocked with no extra trusted records.
- Repeating Process Pending for Customers and Orders processed0; delivery objects/counts were identical.
- Customer profile20 and dataset interpretation21 were reused; Orders profile19 and interpretation20 were reused.
- Current relationship discovery reused run5; final current metric discovery reused run7 with the same15 candidates (product-audit-final-metric-reuse.json); run6 reuse is retained as an earlier checkpoint. A changed dependency correctly causes a new discovery generation.
- Final analytics retry reused all14 fresh candidate/result-ID pairs.
- Repeated canonical Ask reused its persisted answer. Repeated ad hoc SQL returned1050 with identical source pins and separate execution metadata, as intended.
- Evidence: `product-audit-final-reuse.json`, `product-audit-relationship-reuse.json`, `product-audit-final-analytics-reuse.json`.

APPEND delivery94/application49 published Orders state49/version2 from previous state45:50 input/valid/inserted; zero duplicates/rejections/conflicts; trusted total1050. Only Orders was processed. Gold14/profile19 became current after refresh.

UPSERT delivery95/application50 published Customers state50/version2 from previous state43:15 input/valid;10 updates;5 inserts; zero unchanged/duplicate/conflict/rejection; total255. The immutable validated state was read locally and all15 delivery rows matched all eight source fields, including the name field without printing or transmitting it. Distinct keys255; duplicate keys0. Secured public SQL separately verified all15 nonsensitive field predicates. See `product-audit-upsert-state-verification.json` and `product-audit-upsert-field-check.json`. One first combined verification query exceeded the existing expression-depth policy and was correctly blocked; smaller bounded checks passed. A pandas Boolean normalization error in the audit helper was corrected; no production UPSERT defect was found.

## Freshness and workspace isolation

After Orders changed, seven dependent metric results became stale while ten unrelated current results remained reusable; three definition blockers remained blocked. Relationship verification became stale only for affected pairs. Ask returned a specific profile/understanding refresh message and did not show stale values as current. After refresh,1050 and current sums were obtained. Later semantic/relationship generation changes correctly invalidated definition dependencies as well.

Switching from workspace18 to the retained synthetic workspace17 showed its own single unavailable Customers source, no saved Retail queries, no previous SQL result and no Retail Ask context/answers/metrics. Returning to18 restored the three saved queries and six current sources. Frontend ownership checks and backend validation remain in place. Actual SQL reload preserved saved metadata; opening major pages in the resumed browser session reloaded authoritative state without publishing another delivery.

## Responsive and accessibility audit

Actual390px browser layouts were inspected for Overview, Data, Processing, Quality, Model, Analytics/Ask and SQL. Measured document width stayed at or below the viewport (375–390px including scrollbar behavior); Model/Analytics had no horizontal page overflow. SQL uses a catalog drawer. Tablet SQL at768px measured753px content width; desktop screenshots use the ordinary desktop viewport. Mobile Ask returned1050; mobile Editor ran COUNT DISTINCT and returned1050, Current, approximately2.4 seconds.

Keyboard verification: mobile navigation initially focuses Close; Shift-Tab wraps to Settings; Escape closes and returns focus to Open navigation. Catalog focuses its search. Inputs have semantic labels, results use table headers, status text is readable, and visible focus tokens are centralized. Component tests cover dropdown keyboard behavior, dialogs, guards and narrow layouts. This is a practical accessibility audit, not an independent WCAG certification or a full assistive-technology/browser matrix.

## Security and resource regression

Final public SQL smoke20/20 passed: current SELECT, CTE, aggregate, confirmed lookup; DROP, INSERT, multiple statements, ATTACH, external Parquet/CSV/network read, system catalog, control-plane table, another workspace alias and comment-wrapped unsafe syntax were rejected. Queries without LIMIT and with LIMIT1000000 returned at most500 rows and reported truncation. Evidence: `product-audit-final-sql-smoke.json`.

Input loading is independently bounded before the worker timeout: at most2 datasets,250000 rows/source and500000 total,128000000 transferred/materialized-budget bytes,64MiB/artifact,1000000 metadata bytes,128 columns and512 row groups. Checksum/footer/decoded-size checks precede full conversion; Arrow batches are bounded to at most512 rows with conservative expansion and deep-memory checks. Input time is cooperatively checked with a30-second default/60-second ceiling; it is not an OS preemption deadline for each decoder call.

DuckDB defaults to256MB (512MB configuration ceiling), one thread, no external access/extension autoload or install, locked configuration and zero disk spill. Results independently default to500 rows/1000000 bytes with hard configuration ceilings. Worker timeout/termination/kill cleanup closes pipes/resources. OOM/crash is recorded safely; current source pins are revalidated before publishing execution metadata, and ad hoc workers cannot mutate trusted state. There is no OS heap cap in V1; Arrow/pandas allocations are outside DuckDB's memory limit. This limitation remains explicit rather than claiming a total-process memory guarantee.

## Validation results

| Validation | Final result | Evidence |
| --- | --- | --- |
| Complete current DB-enabled backend | 589 passed;0 failures/errors/skips;5357.182s | product-audit-release-full-db-results.xml and completion JSON |
| Standard backend | 435 passed;154 expected DB opt-in skips;11.19s | product-audit-release-standard-results.xml |
| Focused question/planner checks |62 question tests; persisted follow-up DB test passed | product-audit-followup-focused-results.xml / product-audit-followup-db-results.xml |
| Frontend lightweight scripts |29 passed, including real CodeMirror lifecycle and SQL polling recovery | product-audit-frontend-results.json |
| Frontend lint / production build | Passed; existing large-chunk warning | product-audit-final-lint.log / product-audit-final-build.log |
| Final SQL adversarial smoke |20/20 passed | product-audit-final-sql-smoke.json |
| git diff --check | Passed | product-audit-diff-check.log |

The final full DB run started after all shared backend fixes and migration0026 and completed successfully. Subsequent implementation edits were frontend-only, so they did not invalidate the full DB result. Earlier interrupted/historical full runs are not used as the release result. Existing Starlette TestClient deprecation remains one warning. Tests were not weakened to force a pass. An initial frontend test invocation from the wrong working directory was an audit-runner fixture-path issue; the correct frontend-directory runs passed.

## Screenshots and evidence

All final product screenshots are actual application screenshots under `screenshots/product-audit/`, not fake/static HTML:

1. final-overview.jpg
2. final-datasets.jpg
3. final-dataset-detail.jpg
4. final-processing.jpg
5. final-data-quality.jpg
6. final-data-model.jpg
7. final-analytics.jpg
8. final-ask-success.jpg
9. final-sql-builder.jpg
10. final-sql-results.jpg
11. final-sql-editor.jpg
12. final-mobile-overview.jpg
13. final-mobile-analytics.jpg
14. final-mobile-sql.jpg

Additional final-sql-duplicate-recovery.jpg and final-tablet-sql.jpg and baseline/stale/duplicate/APPEND/UPSERT screenshots preserve the specific transitions. Some screenshots intentionally capture the audit state before a later metadata-only refresh; exact final counts/pins are authoritative in the final API JSON evidence. In particular, early overview images with five current relationships precede the last revalidation; the final relationship evidence has six.

## Changed files and review scope

Production changes are concentrated in existing centralized AI/planning/date rule modules; shared layout/tokens/guidance; dataset ingestion/processing/profile/rule views; workspace preparation/model/metrics/Ask composition; and modular SQL components. Relevant new focused files include `tests/test_calendar_date_processing.py`, the question/provider cases, `frontend/tests/analytics-ask-recovery.cjs`, `frontend/tests/sql-editor-lifecycle.cjs`, `frontend/tests/sql-refresh-recovery.cjs` and other SQL interaction tests. Audit-only scripts and evidence are under `tools/`, `docs/` and `screenshots/`.

The working tree also contains the previously authorized uncommitted Phase9 SQL implementation, dependencies and migration0025. They were not staged or committed or rewritten as an unrelated phase. Review `git status` and the full diff together; tracked diff statistics alone omit new untracked Phase9 modules.

## Remaining issues and recommendation

- **P0:** None found in the exercised workflows/security suite.
- **P1:** None remaining in the exercised core journey.
- **P2:** No reproducible unresolved core interaction defect after the fixes. Full browser/assistive-technology certification and every theoretical failure combination are outside the practical coverage claimed here.
- **P3:** Large main JS chunk (approximately876kB minified); ad hoc DOUBLE amount tails; conservative literal-measure clarification can require one extra choice; long metadata-heavy preparation loads were observed during concurrent multi-tab audit and session recovery. The SQL overlap defect is fixed; no production latency SLA is inferred from this development session.
- **V1 limitations:** One direct current confirmed child→parent lookup, bounded results, advanced SQL-only capabilities where labelled, no OS-level heap cap, timezone-free event fields remain TEXT, account settings backend unavailable.

**READY WITH MINOR ISSUES for final approval of this UX/acceptance milestone.** The useful core flow is proven with actual retail results, not just unit tests. This recommendation does not authorize a commit, deployment, a new phase, new business approvals or weakened safeguards. No commit was made.
