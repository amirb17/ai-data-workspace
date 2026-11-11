# 414F0-B — real PostgreSQL result-reader permissions evidence

Previous local milestone: **17 focused / 28 combined** BigQuery IAM
inventory checks passed. These were offline checks, not live-cloud IAM
tests.

This stage introduces `probe_warehouse_result_reader`, a read-only
PostgreSQL catalog inspection operating on the **actual psycopg
connection provided**. It reports current_user/session_user,
connection's active schema, login/superuser/CREATEROLE/CREATEDB/BYPASSRLS
status, schema CREATE, and effective SELECT and mutation privileges on
a defined subset of protected warehouse query, journal, envelope,
publication and serving-materialization tables. Missing tables and
missing expected role are explicit NO-GO findings.

It **never** performs role creation, grants, SET ROLE, writes, drops or
cloud calls, and **always returns NO_GO**, even with a seemingly narrow
session. Running as the developer's superuser or as an unrelated
role cannot be misrepresented as acceptance for the restricted
`datarise_warehouse_result_reader` identity.

The role name used for the focused probe is a *candidate to review*;
it is not a claim that this role already exists in the environment
or is part of the final approved role-name matrix. Production
acceptance requires reviewing all five intended service-role names,
migrations and privilege matrices before provisioning any new roles.

## Current limitations and next acceptance

- The fixture's ordinary database login is **not** a dedicated restricted
  login and so cannot prove least privilege.
- The table inventory is targeted, not an exhaustive schema/sequence/
  function/column/search-path or advisory-lock audit.
- An actual restricted login test must connect with separate scoped
  credentials; verify denied INSERT/UPDATE/DELETE/TRUNCATE and DDL,
  workspace ownership, transactional isolation, role inheritance,
  EXECUTE privileges and migration upgrade/rollback.
- Cloud IAM, BigQuery physical table retention and out-of-band deletion
  safety remain unverified. No deployment or service-account grant.

Run the new isolated PostgreSQL tests:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_pg_result_reader_permission_probe_414f0b.py -v --tb=short -x
python -m pytest tests/test_warehouse_query_result_iam_preflight_414f0c.py tests/test_warehouse_pg_result_reader_permission_probe_414f0b.py -q --tb=short -x
```

A NO-GO report for the development connection is the expected,
security-preserving behavior, **not a reason to create production grants
without review**.
