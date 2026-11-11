# 414G — authenticated, read-only warehouse result readiness API

Prior local checkpoint: **7 focused and 14 combined** authenticated
first-page simulated retrieval tests passed.

This incremental API mounts
`GET /workspaces/{workspace_id}/warehouse/queries/{attempt_handle}/result-readiness`
on the already registered warehouse router. It requires the normal
application identity dependency and independently validates the current
original login cookie, owned workspace, exact original attempt handle,
checkpoint session binding, revocation state and completed successful
attested warehouse accounting. Unknown or unready results return
redacted 4xx statuses.

The response intentionally includes ONLY the caller's attempt handle,
`RESULT_ELIGIBLE` status, permitted maximum page rows, and explicit
`results_available=false`, `query_execution_enabled=false`. It
does not return query data, internal attempt ID, BigQuery job ID,
provider credentials or pagination cursors. This is result readiness
inspection, **not a public result-fetch endpoint** and does not switch
on live BigQuery or publish a query-execution permit.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_result_readiness_api_414g.py -v --tb=short -x
python -m pytest tests/test_warehouse_authenticated_results_414f5.py tests/test_warehouse_result_readiness_api_414g.py -q --tb=short -x
```

Production release remains NO-GO until fully reviewed BigQuery result
transport, user-facing UI, permission-restricted DB roles, cloud IAM
and physical serving-retention protections are proven.
