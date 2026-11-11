# 414F4 — one-shot durable cancellation-attempt fence

Previous stage verified locally: **11 focused transport tests and 15 combined tests passed**.

Migration **0060** adds an immutable cancellation-attempt marker keyed by
the original warehouse attempt. Its guard checks the original send intent,
immutable pinned request hash, cancellation intent, live executing reservation,
existing SEND_BOUNDARY, current leased journal revision and allowed
possibly-sent phase. The marker serializes on the original send-intent row.

The backend-only `record_cancel_attempt_boundary_without_provider` requires
the original typed recovery lease and CAS revision, then commits the marker.
Even if a process crashes after commit, no later worker may record another
cancellation attempt. The result is **lookup required**, not cancellation
confirmed; billing and physical publication holds remain intact.

**Important limits:** This does not call jobs.cancel, grant permissions,
expose a public endpoint or authorize a cloud cancellation. Nor is the marker
a standalone transport capability. Production integration must ensure that
only a separately approved worker can attempt cancellation after this durable
commit and that all unknown outcomes go through original-job jobs.get.
A never-delivered cancel request remains uncertain rather than blindly
replayed.

Run isolated PostgreSQL tests:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_cancel_attempt_boundary_414f4.py -v --tb=short -x
python -m pytest tests/test_warehouse_cancel_intent_414f4.py tests/test_warehouse_cancel_attempt_boundary_414f4.py -q --tb=short -x
```

No live GCP, cancellation execution or billing settlement is performed.
