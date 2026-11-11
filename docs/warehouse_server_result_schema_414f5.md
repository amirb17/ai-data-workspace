# 414F5 — server-owned warehouse result schema

Earlier 414F5 simulated fetch: **7 focused / 12 combined tests passed**
in isolated PostgreSQL.

This increment derives the exact result column names and scalar types
from the persisted warehouse logical draft and approved, version-pinned
model configuration/source schema. It independently compares the
re-planned SQL AST and logical draft digest with the original attempt.
Unsupported physical/semantic types fail closed. A new backend-only
`read_first_warehouse_page_server_pinned_simulated` call no longer accepts
`approved_schema`; it verifies the server projection before and after
the bounded simulated lookup, and delegates to the previously tested
settled-success and result-size gates.

The earlier internal first-page function with an explicit approved schema
remains for backwards-compatible tests; **do not expose that low-level
function as a public endpoint or allow its schema to come from HTTP**.

No BigQuery getQueryResults SDK binding, real cloud permissions, public
user authentication, pagination cursor binding or warehouse SQL UI is
released. This feature validates current server-side model approval;
retrospective access after publication changes will require separately
reviewed historical-retention policy.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_server_result_schema_414f5.py -v --tb=short -x
python -m pytest tests/test_warehouse_first_page_simulation_414f5.py tests/test_warehouse_server_result_schema_414f5.py -q --tb=short -x
```

The production security/IAM/retention and live execution release are
still NO-GO.
