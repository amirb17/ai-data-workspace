# 414F2 — pinned possible-send to jobs.get recovery

The preceding 414F1 simulated transport stage passed locally: **7 transport,
2 PostgreSQL integration and 9 combined tests**.

This stage adds `lookup_pinned_submission_without_resend` to the trusted
provider-policy service. It first proves, under PostgreSQL recovery lease and
CAS rules, that there is a committed SEND_BOUNDARY for the original attempt,
the phase is SEND_UNKNOWN or PROVIDER_RUNNING, and migration 0058 retains
the same immutable request fingerprint and provider job identity. It then
invokes the existing provider policy attestation flow, which performs
GET-only metadata inspection, independently verifies scope, identity,
SQL and billing data, and journals the observation atomically.

The external GET runs **without a database lock held** and revalidates
the policy and journal CAS on return. On NotFound/uncertainty, the send remains
ambiguous, billing remains unsettled, and the exact job identity is reused
on a later lookup. Revocation after possible send does not suppress recovery
inspection; it cannot authorize another send. No BigQuery insert, cancel,
result fetch, privilege grant or accounting settlement is introduced.

Tests in disposable PostgreSQL + fake BigQuery GET client:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_pinned_submission_recovery_414f2.py -v --tb=short -x
python -m pytest tests/test_warehouse_simulated_submission_414f1.py tests/test_warehouse_pinned_submission_recovery_414f2.py -q --tb=short -x
```

Remaining before a true production pipeline: runtime PostgreSQL login grants,
cloud IAM and physical table retention, reviewed actual jobs.insert transport
and release control, cancellation, verified billing settlement, bounded
result retrieval and warehouse analyst UI. A verified metadata result is
not itself an authorization to fetch rows or release historical holds.
