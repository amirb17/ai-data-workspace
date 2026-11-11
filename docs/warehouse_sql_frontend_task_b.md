# Task B - Warehouse SQL frontend implementation

Local frontend checkpoint on `feature/warehouse-model-ux`, following Task A at
`fa1a57ef602f370e6e89c9cf1cf539a88f271e68`. Production query execution remains disabled.

## Implemented experience

A dedicated workspace tab and lazy route at
`/app/workspaces/:workspaceId/warehouse-sql` show approved FACT/DIMENSION models,
columns/types/roles and a bounded logical SQL editor. The older dataset/Gold SQL
route and implementation are unchanged; the new page explicitly links to and
distinguishes it. Templates derive only explicit columns from the selected model.
The supported grammar is one model, explicit projection and literal LIMIT 1-500;
4096 characters, with stricter server budgets where applicable. No join, filter,
aggregate, wildcard, arbitrary physical identifier or new backend route is added.

The existing authenticated request client supplies cookies, CSRF and safe error
handling. Typed wrappers use only Task A's actual fields and routes: validate,
admit, status, execute, refresh, cancel and results. Budget numbers appear only
after a validated server response; generic compiler errors cannot expose extra
server diagnostics because Task A intentionally returns sanitized messages.

The screen keeps the server attempt handle for every subsequent action. Admission
retries reuse the original UUID idempotency key and locked SQL/model after an
uncertain response. Synchronous action exclusion prevents duplicate clicks before
React rerenders. After a possibly sent execution, recovery uses the original
handle and refresh; it never creates a replacement request. PREPARED/ADMITTED
observations permit continuation because those backend states have no send marker.
Cancellation requests are available for running/uncertain jobs and never imply
terminal success. Only SUCCEEDED with results_available can load result rows.

Status, errors, empty results and bounded first-page rows are displayed without
provider identities or pagination tokens. Responses with wrong handles, malformed
rows, unsafe browser integer numbers or changed execution flags fail closed.
Decimal strings stay strings. JavaScript cannot faithfully decode every signed
64-bit JSON integer: values outside its safe range are rejected rather than
silently shown rounded; a reviewed lossless backend transport is still needed for
those values. No backend contract is modified by this frontend task.

Each workspace mounts separate state; controllers abort and late responses are
ignored when leaving. No SQL/results/keys/handles are persisted in browser storage.
401/403 and cross-tab auth changes clear results and hide cached query/model
content. A known original handle can be resumed by an authenticated status GET;
the server still enforces original-session ownership. Copy the handle before
leaving. Closing/navigating away during an unresolved admission can lose its
in-memory retry key; there is no invented history/recovery endpoint to compensate.
A durable authenticated request-history feature would require separate review.

Live BigQuery remains unavailable: the banner explains this and no frontend flag,
credential or SDK client enables it. Public read-only mode blocks query operations
in both UI controls and the action boundary. The normal backend 503 is an explicit
unavailable/disabled response, never artificial execution success. Approved catalog
definitions do not prove serving readiness or authorize cloud execution.

## Verification

Executed from `D:\Project\datarise-warehouse-ux\frontend`:

```powershell
npm run build
npm run lint
npm test
```

Final commands all exited 0. TypeScript/Vite build passed; ESLint reported no
errors or warnings. The test runner passed **38/38 scripts**: all 36 prior scripts
plus two new scripts. New coverage contains **18 React interaction scenarios**
and **8 API contract scenarios**, not 26 separately collected pytest/Vitest cases.
No assertions were removed or weakened. Exact log SHA-256 and source inventory
are in `warehouse_sql_frontend_task_b_validation.json`.

Interactions use actual React DOM (happy-dom), the real session/CSRF client and
controlled HTTP responses matching Task A. Coverage includes model selection,
validation/admission, success/results/empty rows, duplicate clicks, running and
uncertain jobs, cancellation, expired/failed states, original-handle resumption,
503/400 handling, lost-admission retries, workspace-switch races, 401/403 and
cross-tab session changes, and production/public-mode denial. Contract cases reject
wrong scope, malformed responses, unsupported status/flags, oversized pages,
unsafe integers and raw internal error leakage.

No real browser-to-PostgreSQL acceptance, viewport screenshot, backend rerun or live
provider test is claimed. Task A's separate backend evidence remains historical;
these tests are not live-cloud readiness. Responsive grid/scrollable table behavior
is implemented with the existing Tailwind conventions. No dependency was added.

Vite retains a non-failing >500 kB chunk warning (AppLayout about 625 kB); the new
lazy Warehouse SQL chunk is about 14.57 kB / 5.00 kB gzip. Initial validation commands
were mistakenly launched from the repository root, then corrected to the frontend
package; an initial test-generation relative-path error occurred before tests ran.
An early ref-cleanup lint warning was resolved. Final results above supersede those
launch diagnostics. No timeout or security gate was relaxed.

## Exact file inventory

- `frontend/src/AppLayout.tsx`
- `frontend/src/pages/Workspace/WorkspaceDetailPage.tsx`
- `frontend/src/services/api/warehouseQueries.ts`
- `frontend/src/features/workspaces/warehouseSql/WarehouseSqlPage.tsx`
- `frontend/src/features/workspaces/warehouseSql/useWarehouseQuery.ts`
- `frontend/src/features/workspaces/warehouseSql/WarehouseQueryResults.tsx`
- `frontend/tests/warehouse-sql-api.cjs`
- `frontend/tests/warehouse-sql-workflow.cjs`
- `docs/warehouse_sql_frontend_task_b.md`
- `docs/warehouse_sql_frontend_task_b_validation.json`

Only route/navigation wiring, the new Warehouse SQL frontend, tests and this record
changed. Task A backend, dataset/Gold SQL, role matrix, migrations, auth client,
processing gates and infrastructure are unchanged. No cloud, deployment or Task C
work occurred. Stop for review; real-provider factories, role approval and external
security/operational acceptance still block production query execution.
