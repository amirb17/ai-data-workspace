# 414F0-B11 - Real warehouse function ACL rehearsal

**B10 locally verified:** 10 focused and 15 combined migrated-table tests passed.

B11 checks the 14 expected PostgreSQL warehouse security functions against
the **real, isolated** 414E3 fixture through migration 0056. It confirms
that their catalog metadata is complete and tests effective PostgreSQL
`EXECUTE` permission on every function under a newly created NOLOGIN,
NOINHERIT restricted role.

For each parameterized test, REVOKE PUBLIC EXECUTE on only the selected
fixture functions, GRANT EXECUTE on one selected function to one temporary
role, verify denial on the remaining functions, and ROLLBACK all role/ACL
changes. It does not call any function or submit any BigQuery job. These
changes are **not** deployment instructions for real runtime identities.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_function_acl_postgres_414f0b11.py -v --tb=short -x
python -m pytest tests/test_warehouse_control_table_permissions_414f0b10.py tests/test_warehouse_function_acl_postgres_414f0b11.py -q --tb=short -x
```

**Not yet accepted:** actual authenticated role logins and TLS, comprehensive
function dependency closure, SELECT FOR SHARE / UPDATE positive operations,
trigger-induced writes under restricted runtime context, production ownership
and grants, independent BigQuery IAM and physical retention. Live BigQuery
warehouse query submission remains disabled.
