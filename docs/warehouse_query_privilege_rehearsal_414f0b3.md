# 414F0-B3 — isolated transactional PostgreSQL permission rehearsal

**Status:** implementation ready for local verification; **not** a production
security acceptance or proof that the real warehouse privilege policy works.

B1 observed the normal development connection in `public` as `postgres`, with
all five proposed runtime roles and nine warehouse query control relations
absent. B2 correctly returned NO-GO. Do not create roles or apply grants to that
existing database to make the audit pass.

## B3 tests

```powershell
python -m pytest tests/test_warehouse_query_privilege_rehearsal_414f0b3.py -v --tb=short
```

This default run covers safe-target validation and **skips** the DB-specific
test. Do not claim an authorization or real PostgreSQL success from that skip.

For the database-backed test, first create a **separate disposable PostgreSQL
database** named `datarise_b3_test_<suffix>` on a consciously selected local
instance. Ensure its connection differs from `DATABASE_URL` and that the
connection's role can CREATE ROLE; do not use RDS or shared instances.
Set `B3_DISPOSABLE_DATABASE_URL` privately in PowerShell and set
`RUN_B3_ISOLATED_DB_TESTS=1`, then rerun the module. Never echo or commit
the connection string. The B3 SQL lives inside a rollback-only transaction.

It creates unique NOLOGIN test identities and two synthetic tables. Positive
SELECT assertions demonstrate an explicitly granted reader and independent
verifier; negative tests deny cross-role read, INSERT, DELETE, TRUNCATE and
schema CREATE. All synthetic roles, schema and grants roll back at teardown.

**Limitations:** This checks native PostgreSQL privilege mechanics in isolation,
not the full 0051-0056 migration/trigger dependencies or the reviewed runtime
permission matrix. It uses SET ROLE simulations, not independent authenticated
connections, TLS or real credentials. Distinct URL and database-name gating
reduce accidental use but do not prove an isolated server, authorization or
independent deployment. Operational acceptance remains NO-GO.

**Next:** specify complete positive grants for the actual warehouse controllers
and test negative rights against all real control relations/functions in a
separately isolated PostgreSQL instance. The cloud/IAM and physical retention
boundaries stay blocked. BigQuery analytics remains disabled.
