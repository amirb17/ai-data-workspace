# 414F0-B6 - Warehouse control function/trigger catalog inventory

**Status:** read-only inspection only; runtime least-privilege acceptance remains NO-GO.

B5 passed 3 focused and 10 combined tests and found 20 static row-lock
references (5 UPDATE, 15 SHARE). B6 extends review to the PostgreSQL
control-plane functions and triggers introduced in query migrations
0048-0056. A static SQL scan cannot establish the effective privileges
of trigger-invoked functions, especially transitive reads and writes.

The B6 SQL file inventories expected functions and triggers **inside
the current PostgreSQL search-path schema**, reporting missing objects,
SECURITY DEFINER status, function settings/search_path, owner,
PUBLIC EXECUTE rights, trigger status and target. It makes no changes.

Important: the previous B1 audit examined the public schema and found no
warehouse runtime roles or control tables. **Do not run B6 against public
and interpret missing objects as a defect in isolated migration schemas.**
Use a separately provisioned, fully migrated disposable development
schema or instance after checking its name and effective search_path.
For any unexpected grant, owner, SECURITY DEFINER function or absent
trigger, stop and review. PostgreSQL catalog fields do not, on their
own, prove effective least-privilege behavior.

Run the static checks:
```powershell
python -m pytest tests/test_warehouse_query_function_trigger_audit_414f0b6.py -v --tb=short
python -m pytest tests/test_warehouse_sql_lock_inventory_414f0b5.py tests/test_warehouse_query_function_trigger_audit_414f0b6.py -q --tb=short
```

Only after reviewing migrated disposable schema selection, run the catalog
SQL inside an explicit READ ONLY transaction using the verified connection.
Do not apply `deployment/task21g_6_provision_runtime_roles.sql` or
`deployment/task20_runtime_privileges.sql` to the normal database to
make the audit pass.

**Known remaining gaps:** exhaustive dependency graph, all truncate
triggers, inherited role privileges, function ACLs including overloads,
column-level FOR SHARE/UPDATE rights, sequence privileges, runtime
authentication, cloud IAM, retention protection and provider operation
acceptance. These must be independently reviewed before any GRANT.
No live analytical BigQuery or physical cloud cleanup is enabled.
