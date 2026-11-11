# 414F0-B10 — complete control-table privilege negative test (disposable schema)

B9 passed five focused tests and six combined B8/B9 tests on the existing
isolated migrated PostgreSQL warehouse fixture. B10 broadens the negative
permission check from four tables to **all nine current warehouse security
control relations**. Tests are parameterized to catch drift in any one table.

Checks: exact object presence, normal table kind and owner, unexpected PUBLIC
write/trigger privileges, a fresh NOLOGIN/NOINHERIT role granted SELECT on
exactly one relation, cross-table read denial, FOR UPDATE/UPDATE/DELETE/
TRUNCATE/INSERT/CREATE denial, and rollback-confirmed role disappearance.

The tables and grants are **real catalog objects** created by fixture
migrations; the roles are **temporary SET ROLE simulations**. They are not
real authenticated service login tests or production role grants.
No cloud calls, query submissions, physical deletion, or AWS actions occur.

Run from the existing `feature/warehouse-model-ux` worktree:

```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_control_table_permissions_414f0b10.py -v --tb=short -x
python -m pytest tests/test_warehouse_runtime_permissions_postgres_414f0b9.py tests/test_warehouse_control_table_permissions_414f0b10.py -q --tb=short -x
```

**Acceptance remaining:** actual `datarise_*` roles with independent
authenticated TLS connections; positive controller permissions (including
locking and transitive trigger/function/sequence privileges); PUBLIC EXECUTE
function ACL review; cloud-side IAM/retention and concurrency protection.
The test must not be used as a deployment permit. BigQuery analytical
dispatch and physical cleanup remain disabled.
