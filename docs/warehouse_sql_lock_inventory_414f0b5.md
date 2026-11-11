# 414F0-B5 — static SQL locking dependency evidence

**Status:** review-only, no grants. The B4 logical controller-to-table
requirements are not sufficient to provision real runtime identities.

Source-level review found explicit `FOR UPDATE` and `FOR SHARE` reads in
the checkpoint, recovery, provider-policy and retention controllers and the
query send/attempt repositories. These are not equivalent to plain SELECT.
PostgreSQL row-locking privileges require review of UPDATE/column permissions,
and their scope must not be inferred from the B4 minimal draft.

`app/security/warehouse_sql_lock_inventory.py` extracts literal SQL lock
clauses from a fixed list of seven modules. It is deliberately non-executing.
It cannot prove completeness because queries may be dynamic, SQL may be
assembled from fragments, and service calls transitively invoke other
repositories. PostgreSQL triggers/functions can read or write additional
relations under their own effective execution security context.

It returns NO_GO even when matching source locations are found. This code
is a regression-oriented review aid, **not** a SQL parser, permission grant
generator, role implementation, authenticated permission proof or deployment
approval. No changes are made to migrations, cloud or PostgreSQL roles.

Tests:
```powershell
python -m pytest tests/test_warehouse_sql_lock_inventory_414f0b5.py -v --tb=short
python -m pytest tests/test_warehouse_permission_requirements_414f0b4.py tests/test_warehouse_sql_lock_inventory_414f0b5.py -q --tb=short
```

Next security gate: inspect full transitive SQL operations, trigger definitions,
column/sequence grants and function SECURITY INVOKER/DEFINER ownership.
Review the mapping against actual migrated tables using separate authenticated
restricted roles on a dedicated instance. No live BigQuery dispatch or cloud
deletion can be enabled from this checkpoint.
