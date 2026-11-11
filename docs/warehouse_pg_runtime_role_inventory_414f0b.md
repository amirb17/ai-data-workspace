# 414F0-B — actual PostgreSQL multi-role inventory

Previous checkpoint locally verified: **10 focused / 27 combined**
PostgreSQL and IAM inventory tests passed.

This increment implements `inspect_runtime_role_inventory`, a
read-only catalog scan over the **actual connected PostgreSQL
database**, checking a caller-provided, validated list of candidate
runtime roles against a bounded list of protected warehouse tables.
It records role presence, LOGIN capability, elevated attributes,
role membership, schema CREATE and effective INSERT/UPDATE/DELETE/
TRUNCATE/REFERENCES/TRIGGER privileges, and missing migrated tables.

Important: the **five role names in the test fixture are exploratory
candidates only**, not a declaration of the project's final
five-role deployment matrix. Before any provisioning or acceptance,
reconcile the names and intended grants against the complete, approved
warehouse security design. The library accepts a trusted role list
instead of hard-coding guessed names.

No role is created, impersonated, granted privileges or mutated.
The inventory always returns `NO_GO` because a catalog check alone
cannot establish actual restricted-login behavior or cloud IAM.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_pg_runtime_role_inventory_414f0b.py -v --tb=short -x
python -m pytest tests/test_warehouse_pg_result_reader_permission_probe_414f0b.py tests/test_warehouse_pg_runtime_role_inventory_414f0b.py -q --tb=short -x
```

Next acceptance: inspect the approved runtime role matrix, connect
using each real restricted login, and prove denied mutations, sequence,
function/trigger and search_path privileges. Real cloud physical
retention and protected IAM remain NO-GO pending independently reviewed
cloud testing.
