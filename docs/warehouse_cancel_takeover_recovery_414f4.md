# 414F4 — cancellation worker takeover and original-job recovery

Previous checkpoint: **6 focused / 10 combined** tests passed for
the permanent cancellation-attempt marker (migration 0060).

This stage introduces `lookup_after_cancel_boundary_without_resend`
on the trusted cancellation service. Under the existing PostgreSQL
global/attempt lock and the fenced recovery CAS lease, it proves all
three immutable records agree: original send intent, cancellation
request and cancel-attempt boundary. It then delegates to the
existing independently attested `jobs.get` path. No cancel transport
can be called by the recovery method.

Disposable PostgreSQL + fake GET tests cover verified CANCELLED with
exactly-once existing-ledger failure accounting; a successful query
despite a cancellation request (accounted as success); NOT_FOUND
remaining EXECUTING with publication hold; missing cancel marker
rejected before provider lookup; and takeover after lease expiry
with the same original job ID and permanently blocked second
cancel boundary.

No new migration, live cloud credentials, BigQuery jobs.cancel,
query result retrieval or publication release is included.
Actual runtime permissions, BigQuery IAM and cloud data-retention
remain release blockers.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_cancel_takeover_recovery_414f4.py -v --tb=short -x
python -m pytest tests/test_warehouse_cancel_attempt_boundary_414f4.py tests/test_warehouse_cancel_takeover_recovery_414f4.py -q --tb=short -x
```
