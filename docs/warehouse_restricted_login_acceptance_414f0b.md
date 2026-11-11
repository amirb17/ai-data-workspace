# 414F0-B — opt-in restricted PostgreSQL login acceptance

Previous verified milestone: **10 focused / 20 combined** PostgreSQL
runtime-role catalog tests passed. Those tests used the ordinary developer
connection and did **not** validate a restricted login.

This increment includes two default-off, actual restricted-login acceptance
tests and 10 offline guard tests. Unlike catalog-only simulations, the
acceptance tests connect using a separately provisioned restricted DB
identity and:
- require **session_user and current_user both equal** the expected role;
- reject privileged role attributes and schema CREATE;
- require all targeted protected warehouse tables in a reviewed disposable
  `test_warehouse_...` schema;
- run `EXPLAIN DELETE FROM <qualified target> WHERE FALSE` (no ANALYZE,
  therefore **never executes DELETE**) and require a permission-denied
  error for every protected table;
- additionally inspect effective SELECT, INSERT, UPDATE, DELETE,
  TRUNCATE, REFERENCES and TRIGGER privileges against those tables.

No role or grant is created. The module never uses your developer DSN
as a fallback. The dedicated role must be provisioned separately and
the exact security matrix independently approved. These tests are
**strictly result-reader deny-only tests**, not a permission template
for the query worker, publisher, API service or cleanup roles.

Run safe default/offline tests:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_restricted_login_gate_414f0b.py -v --tb=short -x
python -m pytest tests/test_warehouse_restricted_login_acceptance_414f0b.py -q
```
The second command should show **2 skipped** until separately
configured; skipping is a positive default safeguard, **not**
proof of role acceptance.

Only after **a reviewed restricted account AND a separately prepared
disposable fully migrated schema already exist**, run in a controlled
environment (do not paste secrets into the chat):
```powershell
$env:RUN_RESTRICTED_DB_ACCEPTANCE = "1"
$env:RESTRICTED_DB_ACCEPTANCE_DSN = "<separate restricted-role DSN>"
$env:RESTRICTED_DB_EXPECTED_ROLE = "datarise_warehouse_result_reader"
$env:RESTRICTED_DB_ACCEPTANCE_SCHEMA = "test_warehouse_<reviewed_schema>"
python -m pytest tests/test_warehouse_restricted_login_acceptance_414f0b.py -v --tb=short -x
```

**Do not enable this gate** against production, the ordinary local
developer/superuser account, or a schema that is not disposable.
Test results do not by themselves authorize deployment. Actual
privilege matrix approval, other runtime roles, BigQuery IAM,
physical warehouse table retention and end-to-end live query
submission/settlement remain NO-GO.
