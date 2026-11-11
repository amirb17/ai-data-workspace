# 414F5 — authenticated result eligibility inspection

Previous server-owned result schema tests: **5 focused / 12 combined**
disposable PostgreSQL tests passed.

The authenticated result-inspection boundary uses the same server
session cookie and signed-in principal checks as the existing warehouse
checkpoint path, rejects another workspace or attempt handle, requires
the original dispatch checkpoint's account/user/session identity,
checks current workspace status and append-only revocations, and
executes the settled-success eligibility gate inside the same locked
PostgreSQL transaction.

Only the original attempt handle, bounded maximum page rows and a
NO-FETCH status are returned. No provider job ID or SQL, provider SDK,
row values, credentials or result cursor leave the service.

The new service is *internal-only*, not mounted as a public API or
permission token. Production result retrieval must compose this identity
gate with the server-owned schema, reviewed bounded BigQuery getQueryResults
transport, provider cursor ownership, and a second identity check
after external IO. Deployment remains NO-GO without cloud IAM,
restricted DB login roles and physical publication retention.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_authenticated_results_414f5.py -v --tb=short -x
python -m pytest tests/test_warehouse_server_result_schema_414f5.py tests/test_warehouse_authenticated_results_414f5.py -q --tb=short -x
```
