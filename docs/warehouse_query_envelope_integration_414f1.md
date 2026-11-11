# 414F1: authenticated warehouse policy -> deterministic query envelope (NO SEND)

B12 passed 3 focused / 18 combined. The preceding 414F1 pure envelope
builder passed **22** unit tests.

The next integration stage invokes the existing authenticated provider-policy
preparation, then reopens the lock-serialized workspace transaction and
revalidates the live original session, workspace ownership, checkpoint,
serving receipt, source/SQL recompile, durable reservation, send intent
and immutable provider policy. It calls the canonical BigQuery jobs.insert
request builder with **server-recompiled SQL**, not caller text.

The result is **inspection metadata only**: deterministic job ID, SHA256 of
canonical request and `ENVELOPE_REVIEWED_NO_DISPATCH`. SQL/request body is
never returned to users or sent to a provider. The existing client is
GET-only; there is no public endpoint or new IAM permission.

A journal with CREATED or LEASE_ACQUIRED at INTENT_RECORDED is eligible
for inspection. Once the durable SEND_BOUNDARY is recorded, this method
rejects new envelope inspection as LOOKUP-ONLY. Revoked identities also
fail closed.

Run on your existing isolated development PostgreSQL:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_query_envelope_integration_414f1.py -v --tb=short -x
python -m pytest tests/test_warehouse_query_job_request_414f1.py tests/test_warehouse_query_envelope_integration_414f1.py -q --tb=short -x
```

**Still not release-approved:** real restricted PostgreSQL login roles and
transitive privileges; BigQuery IAM/physical publication retention; a
transaction-bound send-once provider adapter; unknown-commit recovery;
verified completion, cancellation, billing settlement and result pagination;
the final analyst SQL UI and E2E release.
