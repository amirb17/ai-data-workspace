# 414F2 — terminal evidence gate before accounting

The pinned submission-recovery tests passed locally: **5 focused, 7 combined**.

This step adds a transactionally consistent **read-only** terminal evidence
inspection using the existing authoritative PostgreSQL control tables. It
requires the immutable 0058 envelope and provider-policy digest, original
deterministic job ID, a committed send boundary, latest matching journal
observation and attestation with `VERIFIED_METADATA`, and complete bounded
byte evidence. Success, failure and cancellation are distinguished; missing
or uncertain billing remains explicitly held.

No result object is a settlement permit: `settlement_authorized=False`,
`publication_release_authorized=False`. No BigQuery operation, accounting
mutation, grant, deletion, endpoint or new migration is introduced.

This is a prerequisite for, **not a substitute for**, an authorized atomic
settlement controller. That controller must use
`query_governance_repository.complete/fail` with immutable attestation
identity, reconcile post-settlement idempotency and ensure permanent
publication dependencies are not lifted. Real runtime PostgreSQL identities,
IAM, retention and query execution release remain NO-GO.

Test:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_terminal_evidence_414f2.py -v --tb=short -x
python -m pytest tests/test_warehouse_pinned_submission_recovery_414f2.py tests/test_warehouse_terminal_evidence_414f2.py -q --tb=short -x
```
