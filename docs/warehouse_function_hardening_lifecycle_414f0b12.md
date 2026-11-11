# 414F0-B12 - 0057 lifecycle compatibility

B11 **15 focused and 25 combined tests passed** in isolated PostgreSQL
fixtures. B12 checks the next essential property: actual database guard
changes do not break a fully authenticated warehouse metadata lifecycle.

It reuses the 414E3 disposable E3 fixture through migration 0056, applies
0057 in that fixture only and verifies: authenticated provider policy pinning,
idempotent preparation, fake GET-only provider metadata attestation, no
accounting settlement, existing publication hold, and denial after account
revocation. It does not run BigQuery SQL or enable execution.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_function_hardening_lifecycle_414f0b12.py -v --tb=short -x
python -m pytest tests/test_warehouse_function_acl_postgres_414f0b11.py tests/test_warehouse_function_hardening_lifecycle_414f0b12.py -q --tb=short -x
```

Still open: complete role-grant and role-login acceptance, IAM enforcement
and cloud physical retention, one-time controlled query dispatch, accounting,
and warehouse analyst UI. B12 is a regression gate only.
