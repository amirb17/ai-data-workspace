# 414F5 — authenticated first-page internal integration

Verified previous checkpoint: authenticated result eligibility **7 focused /
12 combined** PostgreSQL tests passed.

The new backend-only `read_authenticated_first_warehouse_page_simulated`
takes a session-bound HTTP Request, workspace ID, original attempt handle
and an explicit fake first-page transport. It **does not accept caller-supplied
SQL, result schema, provider job ID, provider pagination cursor or internal
numeric attempt ID**.

The call:
1. Inspects original account, workspace, login session, revocation and
   settled-success evidence.
2. Resolves the internal attempt identity under a second session-bound
   transaction, never exposing the ID.
3. Fetches a **single simulated page** through the earlier backend
   server-owned approved-column, bounded-result and accounting gates.
4. Rechecks the original authenticated session and query eligibility
   after provider IO, refusing returned rows after in-flight revocation.

No credentials or live BigQuery SDK client are added; this **does not
expose a public warehouse results route**. Actual provider read transport,
runtime service identities, source retention/IAM, public response
serialization and user-facing SQL/results UI remain blocked until reviewed.

Run in disposable PostgreSQL schemas:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_authenticated_first_page_414f5.py -v --tb=short -x
python -m pytest tests/test_warehouse_authenticated_results_414f5.py tests/test_warehouse_authenticated_first_page_414f5.py -q --tb=short -x
```
