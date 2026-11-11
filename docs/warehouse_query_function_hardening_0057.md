# 414F0-B11 repair: migration 0057 query guard hardening

B11 exposed a genuine gap: the 0048–0051 query guard functions lack pinned
`search_path` settings (and PostgreSQL function EXECUTE defaults require review).
Do not suppress this evidence by making B11 skip the assertion.

Migration 0057 is **additive**, atomic and explicitly refuses `public`,
system schemas, missing/overloaded functions and SECURITY DEFINER functions.
It pins all 14 known query security functions to
`pg_catalog, <current approved warehouse schema>, pg_temp` and revokes
PUBLIC EXECUTE. It does **not** create runtime roles, grant any role access,
change owners, perform cloud operations or authorize live query execution.

In B11 only, a fixture applies 0057 to the existing isolated fully migrated
E3 test schema. The original E3 fixture and earlier historical migration
tests remain unchanged. This keeps a reviewable, deployable forward migration
instead of retroactively rewriting the historical 0048–0051 files.

**Important:** Before applying 0057 outside disposable schemas, review
deployment schema choice, current grant behavior, service execution
dependencies and the migration plan. Revoking PUBLIC EXECUTE can affect
unreviewed callers. It is NOT applied to your normal dev `public` schema,
and it is not a production-ready least-privilege grant policy.

Tests:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_function_acl_postgres_414f0b11.py -v --tb=short -x
python -m pytest tests/test_warehouse_control_table_permissions_414f0b10.py tests/test_warehouse_function_acl_postgres_414f0b11.py -q --tb=short -x
```
