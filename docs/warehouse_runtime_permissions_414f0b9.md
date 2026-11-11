# 414F0-B9 — migrated warehouse permission rehearsal

B8 catalog evaluation passed 5 focused, 1 integration and 11 combined cases.
This checkpoint adds **actual migrated warehouse control tables**, not
synthetic table stand-ins. It uses the existing isolated warehouse E3 fixture
(which includes migrations up to 0056), then creates unique temporary
NOLOGIN test roles within a PostgreSQL transaction and tests actual table
permissions using SET ROLE. Four controllers each receive SELECT on exactly
one real control table and are denied cross-table reads, INSERT, UPDATE,
DELETE, TRUNCATE, FOR UPDATE and schema CREATE.

The test always rolls back role creation and grants in the local development
cluster; the disposable warehouse fixture separately drops its schema.
**No persistent `datarise_*` identities or service credentials are created.**
It uses no AWS/GCP APIs and does not enable execution.

Commands:
```powershell
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_runtime_permissions_postgres_414f0b9.py -v --tb=short -x
python -m pytest tests/test_warehouse_catalog_assessment_postgres_414f0b8.py tests/test_warehouse_runtime_permissions_postgres_414f0b9.py -q --tb=short -x
```

This is a **focused negative permission acceptance step**, not a real
deployment-ready role grant policy. It does not cover full transitive
dependencies, authenticated TLS connections, production role grants, active
FOR SHARE/UPDATE positive operations, triggers/functions, sequence ownership
or BigQuery IAM. Those remain explicit blockers. Actual role policy must be
reviewed and tested across all controller operations before deployment.
