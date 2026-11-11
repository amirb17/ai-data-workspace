# 414F1 — deterministic BigQuery job request (no transport yet)

The backend now has a pure `build_reviewed_query_job_request` function.
It accepts the server-owned expected policy shape already produced by
`warehouse_sql_provider_policy_service._derive` and the trusted compiled SQL
from the existing warehouse compiler. It creates one deterministic BigQuery
`jobs.insert` request body, with scoped project/location/job ID, immutable
intent and SQL fingerprint, approved physical table scope, exact query labels,
byte cap, timeout, no query cache, no legacy SQL and no large-result writes.

It rejects foreign workspaces, reservations, tables, project/location drift,
unbounded or boolean numeric policies, extra expected policy fields, bad
fingerprints and incompatible provider/lookup ceilings.

**This is intentionally a pure format builder, not an authorization or
dispatch endpoint.** No callsite currently sends its output, and it is not
permission to change that. 414F0 production database identities, BigQuery
IAM and physical retention remain NO-GO. Before enabling transport, the
future implementation must establish an authenticated live policy recheck,
durable send boundary, single deterministic provider submission, race-safe
recovery/unknown-result handling and verified billing settlement.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_query_job_request_414f1.py -v --tb=short -x
```

No `RUN_DB_TESTS` or GCP credentials are needed for this pure unit suite.
