# Phase 5B.2 review — 2026-10-06

1. **UX problems found**

   Dataset routes displayed both workspace and dataset headers and unavailable summary cards. Delivery IDs dominated filenames. Stage/results information was always expanded, refresh actions were repeated, and mixed-quality successes hid their warning significance. Rules discovery relied on localStorage even though Processing used authoritative backend deliveries. Per-delivery retries did not keep the dataset polling loop active while their requests were running.

2. **Files changed**

   Frontend source files (16):

   - `frontend/src/features/datasets/components/DatasetBreadcrumbs.tsx` — new named navigation.
   - `frontend/src/features/ingestion/processingPresentation.ts` — new presentation helpers for statuses, rates, counts, attention, scope, and issue labels.
   - `frontend/src/features/ingestion/processingState.ts` — contextual Retry Gold wording.
   - `frontend/src/features/ingestion/useDatasetProcessing.ts` — polling during individual retry requests.
   - `frontend/src/features/ingestion/components/DatasetProcessingSummary.tsx` — overview and primary action.
   - `frontend/src/features/ingestion/components/DeliveryRuleBridge.tsx` — contextual actions and notices.
   - `frontend/src/features/ingestion/components/IngestionBatchCard.tsx` — compact delivery card and disclosure.
   - `frontend/src/features/ingestion/components/ProcessingActivity.tsx` — new backend-driven activity region.
   - `frontend/src/features/ingestion/components/ProcessingStages.tsx` — new reusable stage tracker.
   - `frontend/src/features/ingestion/components/ProcessingResult.tsx` — new consolidated outcomes.
   - `frontend/src/features/ingestion/components/ProcessingDetails.tsx` — expanded result and metadata.
   - `frontend/src/features/ingestion/components/RuleReviewForm.tsx` — readable rule-version label without prominent database version ID.
   - `frontend/src/pages/Dataset/DatasetDetailPage.tsx` — compact header and wrapping tabs.
   - `frontend/src/pages/Dataset/Processing/DatasetProcessingPage.tsx` — integrated processing experience.
   - `frontend/src/pages/Dataset/Rules/DatasetRulesPage.tsx` — backend delivery discovery and scoped review navigation.
   - `frontend/src/pages/Workspace/WorkspaceDetailPage.tsx` — dataset routes inherit workspace context without duplicate workspace chrome.

   Tests (2): `frontend/tests/processing.cjs`, `frontend/tests/rule-approval.cjs`.

   Evidence: this report plus `docs/screenshots/phase5b2-processing-desktop.png`, `phase5b2-processing-overview.png`, `phase5b2-processing-result.png`, `phase5b2-processing-mobile.png`, and `phase5b2-pending-progress.png` in the same screenshot directory. The pending screenshot records the final rule-review state, not an intermediate running-stage capture.

3. **Navigation/breadcrumb changes**

   Backend workspace and dataset names appear in Workspaces → workspace → dataset → current tab. Routes retain their existing workspace/dataset identity. Dataset pages have one dataset header; the ordinary workspace pages retain their workspace UI.

4. **User-facing terminology strategy**

   Processing uses Delivery and filenames. Internal IngestionBatch types, upload requests, associations, and API contracts remain unchanged. Files and deliveries remain separate concepts.

5. **Processing overview design**

   Authoritative total, pending, completed, warning, rule-review, and attention counts. Warning presentation includes SUCCESS with rejected rows; backend lifecycle values are not mutated. Mixed-quality successes supplement the backend attention count because that count excludes them. Failed and actively processing counts appear when relevant.

6. **Dataset-level processing action**

   One Process Pending Deliveries (N) calls the existing dataset-scoped backend orchestration endpoint without an arbitrary client association list. Normal card Process/Continue actions remain absent. No pending deliveries means a truthful up-to-date or review-needed message, with no disabled zero-work button. Explicit per-delivery review/retry/recovery semantics remain available.

7. **Live pipeline progress UX**

   The existing scoped GET read model is polled every 2.5 seconds during dataset processing, known running stages, or per-delivery retries. Actual running contexts identify source filenames and stages. While no running context has yet been observed, the region explicitly waits for the next backend update. No invented ordinal, percentage, duration, or optimistic stage completion is displayed. Requests and timers are disposed on route change; stale ownership mismatches are hidden.

8. **Processing-result presentation**

   Input, valid, quarantined, Gold output, acceptance/quarantine rates, duplicates, and updated counts appear together. Unavailable or invalid metrics/rates use —. Rates require a positive authoritative input denominator. Results remain inside expandable delivery details.

9. **Quarantine/data-quality UX**

   Rejected rows produce an attention callout, backend issue counts, and a dataset Data Quality link. NOT_NULL is presented as Missing required values. The destination remains the existing Data Quality placeholder; row inspection, editing, replay, and repair were not implemented.

10. **All-rejected UX**

    Successful Silver with no valid rows shows Completed with warnings, Gold Skipped, zero published rows, the skip explanation, and View Data Quality. It offers no misleading retry.

11. **Success/warning/failure UX**

    Clean completion is green; mixed-quality and terminal all-rejected outcomes are warnings. Awaiting rules explicitly requires decisions. Silver failure offers Retry Processing and explains that the uploaded source remains safe. Gold failure offers Retry Gold and explains reuse of successful Silver. Backend errors retain existing safe API handling; private arbitrary response fields are not rendered.

12. **Delivery-card redesign**

    Filename, upload date, business status, relevant actions/notices, compact stages, and four authoritative outcome counts are visible initially. Recent deliveries use newest-first backend list presentation. Separate stable upload-request keys preserve delivery identities.

13. **Technical-detail disclosure strategy**

    Native collapsed details contain consolidated results, full stages, delivery/upload request ID, source reference, association reference, dataset/rule versions, latest attempt, and timestamps. No raw object dumps, S3 paths, signed URLs, credentials, or stack traces are displayed.

14. **Responsive/mobile changes**

    Breadcrumbs and dataset tabs wrap. Stages become vertical below the small breakpoint. Counts use compact grid layouts, filenames wrap, and actions wrap with touch-sized targets. At 390×844, document clientWidth and scrollWidth were both 375px (15px scrollbar); collapsed and expanded results had no horizontal overflow. Desktop was checked at 1280×900.

15. **Accessibility changes**

    Named breadcrumb and tab navigation, current-page semantics, hierarchical headings, labelled stage/result regions, icon plus text status, hidden decorative icons, live activity announcements, error alerts, semantic definition lists/timestamps, focus styling, and native keyboard-operable disclosure. Enter expansion was exercised in the real browser. No external accessibility audit was run.

16. **Tests/checks run**

    Passed `npm run lint`, `npm run build`, `git diff --check`, and all six existing lightweight suites: contracts, ingestion-batches, file-content-duplicates, backend-integration, rule-approval, processing. On Windows these use npm.cmd and node tests/<suite>.cjs. API assertions in lightweight suites use mocked responses, not end-to-end infrastructure tests.

    Expanded processing checks cover real route/header rendering, named breadcrumbs, single primary action for two pending deliveries, clean/mixed/all-rejected states, approved rule reuse, explicit awaiting rules, Silver/Gold retry states, actual running-stage presentation, unavailable/invalid rates, empty/up-to-date states, ownership filtering, refresh reads, private-field exclusion, and backend-discovered Rules navigation including a foreign delivery rejection.

17. **Backend changes, if any**

    None. No migrations, API semantics, storage, processing execution, or dependencies changed. Backend pytest was not rerun because the phase changes only frontend code/tests.

18. **Manual browser test result**

    Used existing real deliveries only; no new files, rule answers, or approvals created.

    | Requested case | Result |
    | --- | --- |
    | A: clean success | Passed on workspace 9/dataset 11, compatible delivery: 2 valid, 0 quarantined, 2 published. |
    | B: mixed quality | Passed on mixed delivery: 2 input, 1 valid, 1 quarantined, 1 published; both rates 50.0%. |
    | C: all rejected | Passed: 2 input, 0 valid, 2 quarantined, Gold Skipped, 0 published, 0.0% acceptance, 100.0% quarantine; correct CTA and no retry. |
    | D: awaiting rules | Passed on workspace 8/dataset 9 additive delivery; Review Rules loads authoritative draft questions with no invented answers. |
    | E: reused rules | Passed: Approved Rule Version 1 reused shown on compatible/all-rejected deliveries, no new approval action on their cards. |
    | F: pending operation | Partial live coverage: dataset 9 had one pending delivery (upload 33). One primary action moved it from READY_TO_PROCESS to AWAITING_RULES after Bronze. Four successful deliveries remained successful; the prior awaiting delivery remained awaiting. Busy/waiting UI and final stage transition observed. No intermediate Bronze_PROCESSING frame captured and no existing dataset had two pending deliveries; multiple-pending/current-stage variants are covered by lightweight tests. |
    | G: failed delivery | No real failed fixture available. Silver/Gold failure and retry presentation covered by lightweight tests, not live failure/retry execution. |
    | H: refresh | Passed: backend results restored, details collapsed initially. |
    | I: scope switch | Passed between workspace 9/dataset 11, workspace 8/dataset 9, and workspace 8/dataset 10. Breadcrumbs and lists changed correctly; dataset 10's pending delivery stayed pending after processing dataset 9. |
    | J: mobile | Passed at 390×844, including expanded results and keyboard disclosure; no horizontal overflow. |

    Live action changed only existing test upload 33 to AWAITING_RULES; no rule decisions were saved. Data Quality navigation was checked and reaches the existing placeholder.

19. **Known limitations**

    Polling can miss short intermediate stages. No existing real failed fixture or two-pending dataset was available for a complete F/G browser exercise. Current delivery summaries expose aggregate stage state and latest attempt, not a complete processing-attempt history. The Data Quality page remains a placeholder. Counts/rules/outputs keep backend semantics; no quarantine management, workspace orchestration, incremental redesign, or Phase 6 work was added.

20. **Whether Phase 5B.2 is ready for approval**

    Ready for code/UX approval with the live F/G coverage limitations above. Recommended remaining acceptance check: use a dataset with two pending deliveries and a known failed delivery to observe active-stage polling and contextual retry end-to-end. No commit created; Phase 6 not started.
