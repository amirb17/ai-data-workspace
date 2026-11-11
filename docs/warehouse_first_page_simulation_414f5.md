# 414F5 — first-page simulated retrieval with settled-job verification

The 414F5 foundational tests passed locally: **5 PostgreSQL eligibility
tests and 23 combined result-eligibility/parser tests**.

This increment adds a backend-only `read_first_warehouse_page_simulated`
function. It re-reads the authoritative settled-success eligibility in
PostgreSQL, passes only the original job project/location/ID and bounded
max_results to an explicitly supplied single-page transport, then rechecks
eligibility and validates the exact page/schema/size with the previously
tested pure result parser. Errors are redacted and never cause an automatic
retry. Provider pagination cursors are deliberately **not** exposed as
user-requestable state; only a boolean provider_has_more is returned.

Disposable PostgreSQL + fake result transport tests cover successful
first-page return, pre-fetch denial of unsettled jobs, timeouts/connection
errors, forged provider job IDs, hidden provider cursors and missing
transport binding. There are no real cloud credentials or provider calls.

**Caution:** the `approved_schema` argument is still supplied by a
trusted internal caller; a future implementation MUST derive it from
approved compiler/versioned model metadata and bind it to the persisted
original attempt, rather than accepting untrusted request schema. The
simulated transport is NOT a public API, permission token, live BigQuery
getQueryResults implementation or complete multi-page flow. The next
slice should establish server-owned field evidence plus authenticated
authorization and provider cursor ownership before live fetch.

Commands:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_first_page_simulation_414f5.py -v --tb=short -x
python -m pytest tests/test_warehouse_result_eligibility_414f5.py tests/test_warehouse_first_page_simulation_414f5.py -q --tb=short -x
```

Release remains NO-GO until real DB least-privilege roles, BigQuery IAM,
physical publication retention, live reviewed jobs.insert/jobs.get
integration, request identity/cursor scope and user-facing warehouse UI
are accepted.
