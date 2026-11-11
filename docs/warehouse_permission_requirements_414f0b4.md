# 414F0-B4 — warehouse controller permission requirements (draft)

**Status: review-only; grants NOT authorized.** B3's 9 real PostgreSQL
rehearsal tests passed, including the gated rollback-only transactional test.
B3 did not verify the permissions of the application's actual warehouse tables.

B4 enumerates minimum *logical* SELECT/INSERT requirements for four internal
controllers against nine warehouse control relations. The mapping is in
`app/security/warehouse_permission_requirements.py`. It is immutable and
cannot execute SQL, grant privileges, or enable a provider.

The requirements derive from repository/controller accesses in the 414D/E
recovery, preparation, and retention flows. They are **not complete effective
PostgreSQL role entitlements**. In particular:

- `SELECT ... FOR UPDATE/FOR SHARE` requires effective row-locking
  permissions, including column-level UPDATE rights where applicable.
- Trigger bodies, functions, function owners, search paths, sequences, schema
  USAGE, and referenced supporting tables need independent analysis.
- The real backend uses a general DB connection today; these are controller
  labels, not authenticated `datarise_*` PostgreSQL identities.
- Runtime roles remain absent in the observed normal development database.
- Granting these requirements without proving full dependency closure could
  create a broken runtime or an unexpected privilege escalation.
- No in-cluster DDL, runtime GRANT/REVOKE, cloud IAM, provider send, or
  deletion changes have been made.

The actual least-privilege acceptance gate stays **NO-GO**. Next: derive a
complete, per-role effective-privilege matrix from all SQL callsites and
triggers, including negative cases. Validate against real warehouse schema
tables in a fully isolated PostgreSQL instance and obtain review before any
provisioning. Live BigQuery remains disabled.

Run:
```powershell
python -m pytest tests/test_warehouse_permission_requirements_414f0b4.py -v --tb=short
python -m pytest tests/test_warehouse_query_privilege_audit_414f0b1.py tests/test_warehouse_query_privilege_assessment_414f0b2.py tests/test_warehouse_query_privilege_rehearsal_414f0b3.py tests/test_warehouse_permission_requirements_414f0b4.py -q --tb=short
```
