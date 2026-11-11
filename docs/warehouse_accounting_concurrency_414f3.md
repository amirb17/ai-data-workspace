# 414F3 — concurrent settlement + strict replay identity

The first attested billing implementation passed locally: **6 focused and 12
combined** PostgreSQL tests.

This incremental hardening validates the existing global/workspace advisory
serialization and reservation-row lock under two concurrent settlement
workers. Exactly one worker should create the usage entry, the other must
return an idempotent replay; both must refer to the same provider job, byte
amount and terminal outcome. Publication dependency holds remain in place.

The settled replay validator additionally checks the exact terminal journal
phase/observation, provider attestation payload, policy byte cap, immutable
request identity, accounted-byte derivation, accounting basis and the explicit
zero-duration sentinel. This is defense-in-depth against divergent accounting
or mistaken evidence reuse, not a new ledger.

A deliberately simulated inconsistent ledger projection must fail closed. The
test does not mutate guarded production tables. No BigQuery insert, IAM
grant, result retrieval or physical deletion is enabled.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_accounting_concurrency_414f3.py -v --tb=short -x
python -m pytest tests/test_warehouse_attested_accounting_414f3.py tests/test_warehouse_accounting_concurrency_414f3.py -q --tb=short -x
```

Real PostgreSQL runtime role acceptance, cloud IAM and physical publication
retention still block live BigQuery query submission. The warehouse UI, job
cancellation, verified duration, bounded rows and release E2E remain pending.
