# 414F1 — durable pre-send request fingerprint (migration 0058)

The authenticated policy/envelope suite passed locally: 3 focused and
25 combined tests.

This patch implements the next production-oriented mechanism:
a **transactionally persisted immutable request fingerprint**. Migration
0058 defines `warehouse_query_request_envelopes`, keyed to the existing
server-owned expected-policy row, with the exact deterministic provider job
ID, request SHA256 and provider-policy SHA256.

The PostgreSQL trigger serializes with the send-intent row, refuses a
post-send or revoked/expired checkpoint, enforces immutable records and
disallows TRUNCATE. PUBLIC has no table access and the guard function's
search path is pinned.

`pin_authenticated_query_envelope_without_dispatch` reuses the original
session-bound policy verification, rechecks current ownership, publication,
budget, journal and freshly recompiled SQL **inside the same transaction**
as the fingerprint insert. Repeated calls return the same stored fingerprint
or fail closed on drift. No SQL, provider request, credential or send
capability is returned. The prior read-only inspector remains unchanged.

**Not a send permit:** the feature does not enable BigQuery submission,
does not release historical publication holds and does not settle billing.
Actual runtime role grants/cloud IAM, a reviewed send-once transport,
uncertain-commit recovery and live integration tests remain prerequisites.

Run only against the existing isolated warehouse integration fixtures:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_query_envelope_pinning_414f1.py -v --tb=short -x
python -m pytest tests/test_warehouse_query_envelope_integration_414f1.py tests/test_warehouse_query_envelope_pinning_414f1.py -q --tb=short -x
```

Migration 0058 is applied **only inside disposable test schemas** by this
test. Do not apply it to the ordinary database until dependency/grant review.
