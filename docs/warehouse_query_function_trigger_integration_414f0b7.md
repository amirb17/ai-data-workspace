# 414F0-B7 — real PostgreSQL catalog integration

The B6 file passed static tests, but static checks did not prove that
catalog queries worked on the actual migrated warehouse schema. B7
reuses the 414E3 isolated PostgreSQL fixture through migration 0056, and
executes the **B6 SQL batch** under SET TRANSACTION READ ONLY.

B7 asserts that the expected warehouse security functions and triggers
exist and are enabled, and confirms function ownership metadata exists.
It detects PUBLIC EXECUTE as an observed boolean only; **PUBLIC EXECUTE
is not accepted as safe merely because this test passes**. Test code
does not create cluster-wide roles or apply grants. The underlying fixture
creates and drops its own isolated schema.

To execute the actual integration test:
```powershell
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_query_function_trigger_integration_414f0b7.py -v --tb=short -x
```

Requires the existing local PostgreSQL `DATABASE_URL` that supports the
warehouse integration fixtures. Do not echo or commit credentials. Run the
integration test only after reviewing the fixture's schema creation/deletion.

The result validates the catalog audit against real migration objects;
it does not verify runtime role logins, restrictive function EXECUTE ACL,
SECURITY DEFINER safety, true complete trigger dependency closure or IAM.
The deployment decision remains **NO-GO**.
