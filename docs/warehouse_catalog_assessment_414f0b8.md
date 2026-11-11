# 414F0-B8 - Fail-closed PostgreSQL function/trigger security assessment

B7 verified the B6 catalog queries on real, isolated PostgreSQL warehouse
migration fixtures: 2 focused and 5 regression tests passed. B8 now interprets
that evidence explicitly rather than accepting object existence as security
authorization.

The assessment flags:
- Missing or ambiguous expected function/trigger definitions
- PUBLIC EXECUTE function privilege exposure
- Unexpected SECURITY DEFINER or missing owner metadata
- Absent pinned function search_path for manual review
- Disabled/missing triggers and unknown trigger targets
- Incorrect schema selection

**NO-GO is unconditional.** These are diagnostics, not deployable grants.
An absent function search_path is a review finding, not proof of an exploit;
PUBLIC EXECUTE is also a policy-review finding rather than proof of reachable
application access. Further transitive permissions, runtime role isolation,
positive/negative SQL execution, object ownership and IAM must be validated.

Run the unit checks:
```powershell
python -m pytest tests/test_warehouse_catalog_assessment_414f0b8.py -v --tb=short
```

Run the real catalog integration (local test DB must be configured):
```powershell
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_catalog_assessment_postgres_414f0b8.py -v --tb=short -x
```

No PostgreSQL runtime roles, grants, migrations or cloud provider operations
are created or changed. Live query dispatch and physical deletion remain disabled.
