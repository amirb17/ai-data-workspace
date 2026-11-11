# Warehouse completion audit — 2026-11-11

**Branch reviewed:** `feature/warehouse-model-ux`, after 414F0-B8 and AWS
readiness-preflight commits. **Decision: warehouse NOT complete; do not
describe the warehouse-specific BigQuery analytical lifecycle as production
ready.** This is a repository/source review, not a full test or cloud audit.

## What is implemented in source

- Warehouse modeling domain: FACT/DIMENSION configurations, approved
  versions, pinned source evidence, grain/keys, SCD1/SCD2 and fact planners.
- Physical development provider: `app/warehouse/providers/bigquery.py`
  has bounded BigQuery provision/apply/lookup/reconcile primitives,
  with fake tests and separately opt-in real BigQuery tests.
  The `app/warehouse/provider.py` planned-provider interface remains
  non-executing by design. Live test coverage is not deployment approval.
- PostgreSQL warehouse publication and serving metadata, relationship
  contracts, analytical SQL compilers (single model, SCD1, SCD2 and joins),
  budget/reservation and recovery designs.
- 414D/E durable intent/checkpoint/recovery journal/metadata attestation/
  server-owned provider-policy, with non-dispatch gates.
- UI: React workspace data-model tab includes a warehouse FACT/DIM graph
  and reviewed relationship inspection. The existing workspace SQL editor,
  analytics and Ask interface exist for the **pre-warehouse dataset/Gold
  analytics engine**. They are not proof of BigQuery warehouse analytics.
- Dedicated warehouse GET catalog and relationships GET/POST endpoints are
  wired; no public warehouse execution endpoint.

## What is explicitly NOT complete

1. **414F0-B restricted runtime identities:** five `datarise_*` roles absent
   in the reviewed normal development database; source-level requirements,
   privilege inventory, isolated synthetic permission rehearsal, catalog
   tests and a NO-GO assessment do **not** equal real role-enforced operations.
   Must validate schema/sequence/function/column permissions and actual
   restricted authenticated connections against full migrations.
2. **414F0-C cloud physical-retention safety:** PostgreSQL retirement fences
   and indefinite checkpoint holds do not prevent an out-of-band BigQuery
   table deletion. Need independently reviewed cloud IAM, protected identities,
   cleanup control and negative protection tests; never release holds early.
3. **414F0-D security concurrency/revocation validation:** integrated
   attacks/races involving live runtime roles, cloud-side retention and
   simultaneous publication/query/cleanup still outstanding.
4. **414F1 BigQuery analytical query send:** no accepted, controlled live
   warehouse SQL dispatch; `prepare_authenticated_provider_policy` and
   existing reconciliation operate as non-dispatch metadata only.
5. **414F2 warehouse execution, cancellation, recovery:** currently
   checkpoint/send journal, GET-only metadata verification and older generic
   recovery paths; integrated send-once/unknown-commit/cancel/result paths
   still require implementation/acceptance.
6. **414F3 accounting & retention:** actual BigQuery job lifecycle/billing
   settlement, terminal invariants, historical dependency cleanup and
   operational reconciliation remain unaccepted. Generic settlement functions
   do not establish a complete authenticated warehouse E2E path.
7. **414G analyst warehouse UI:** current catalog returns
   `serving_status='NOT_VERIFIED'`, `queryable=False`; the graph tells users
   warehouse SQL is disabled. The existing `/workspaces/{id}/sql` route
   runs the earlier dataset/Gold SQL engine, not the warehouse BigQuery query
   controller. Need verified serving-readiness state, browsable warehouse
   fields/joins, query validation/estimate/run/history/cancel/recovery,
   cost and provenance UX, loading/errors/empty state and ownership UX.
8. **414G release evidence:** end-to-end authenticated multi-workspace
   tests with real restricted PostgreSQL logins, provider negative tests,
   frontend build/lint/component/E2E tests, failure/retry/cleanup testing,
   migrations/rollback, observability and deployment checklist.
9. **AWS deployment:** separate; Terraform contains optional S3/SQS/Lambda/
   EC2/RDS infrastructure, with creation disabled by default. AWS readiness
   preflight code does not establish deployed resources or operational access.

## Do not confuse these surfaces

- `frontend/src/features/workspaces/sql/SqlPage.tsx` and
  `app/api/sql_analytics.py`: existing dataset SQL experience.
- `frontend/src/features/workspaces/ask/WorkspaceAsk.tsx`: existing
  workspace analytics questions.
- `frontend/src/features/workspaces/model/WarehouseModelPage.tsx`:
  reviewed warehouse catalog/graph, explicitly **not queryable**.
- `app/api/warehouse_catalog.py`: only warehouse catalog and
  relationship routes, not warehouse analytical execution.
- `tests/test_warehouse_bigquery_live.py`: explicit opt-in,
  not proof of production queries or IAM readiness.

## Order to finish without expanding audit-only checkpoints

**Stage 1 — close 414F0 as one security acceptance package.**
Prove real restricted role sessions on full migrated disposable DB;
review grants/ownership/function/trigger/search-path and negative DML.
Then cloud IAM/physical retention and concurrency protection tests.
Document the exact NO-GO blockers until eliminated.

**Stage 2 — 414F1/F2/F3 as one bounded warehouse query vertical slice.**
Authenticated user request -> current serving/policy attestation ->
exact budget/claim -> deterministic BigQuery single submit ->
recovery/GET under uncertainty -> verified result/settlement ->
retention hold reconciliation. Prove retries and adversarial failure cases.
Keep feature disabled until accepted.

**Stage 3 — finish 414G UI tied to those APIs.**
Live serving status, model-and-relationship explorer,
warehouse-specific query builder/SQL, preflight cost, history/results,
cancel/recovery/error UX; ensure existing dataset SQL remains distinct.

**Stage 4 — release/regression and AWS.**
Backend full suite, frontend test/build/lint, migrations, on-demand
provider smoke, security/cost review, staged S3/SQS/Lambda/RDS deploy.
Review actual account and region before any Terraform apply.

## Known local evidence from the current discussion

414F0-A tests: 9 focused, 54 combined passed.
414F0-B1/B2: 7 combined passed.
B3: 9 passed including synthetic-role PostgreSQL integration.
B4: 22 passed, one expected B3 skip in a later combined run.
B5: 3 focused, 10 combined passed; identified 20 literal lock sites.
B6: 3 focused, 6 combined passed.
B7: 2 focused, 5 combined passed.
B8: 5 unit, 1 PG, 11 combined passed.
**None is a full project test run or live cloud production acceptance.**

## Definition of done

Warehouse DONE only when: (a) all required migrations succeed on a fresh
environment; (b) real restricted DB roles and cloud IAM enforce least privilege;
(c) physical publication retention survives adversarial deletion/races;
(d) a reviewed BigQuery analytical query can run once, recover safely,
account bytes correctly and release nothing without proof; (e) analyst UI
operates against the actual warehouse query API; (f) backend + frontend E2E,
security and rollback evidence is green; (g) deployment and operations have
been reviewed. Until then: **warehouse incomplete, BigQuery execution NO-GO**.
