# 414F3 — attested warehouse billing through the existing ledger

Previous milestone: terminal evidence gate **6 focused and 11 combined**
PostgreSQL tests passed.

This step provides the first **actual** accounting transition for the
warehouse-specific attestation path. It does not make a BigQuery call.

The service uses ONE database transaction and the established global budget
lock, original claim/reservation lock, immutable send journal, provider policy
and attestation evidence. It rejects unverified terminal or incomplete byte
evidence. It uses the existing
`query_governance_repository.complete` on verified SUCCEEDED, and
`query_governance_repository.fail` for verified FAILED/CANCELLED.
The repository computes conservative accounted bytes and updates the
reservation and usage row atomically, with idempotency on repeated checks.

**CANCELLED** is recorded as FAILED in the existing two-state usage ledger;
the original provider observation remains distinguishable in the immutable
journal and recovery attestation.

The old governance usage schema requires duration_ms; this first controller
uses **0 as an explicit unavailable-duration sentinel**, not an invented
provider runtime metric. Accurate runtime duration needs a future, verified
provider-timestamps ledger amendment. Exact processed/billed bytes are read
from existing attestation only.

Already-settled attempts are read-only and must match the durable terminal
attestation and original job identity. A mismatch fails closed. No provider
resend, cloud deletion, result retrieval or publication hold release can
occur; permanent 0053 checkpoint dependencies persist after settlement.

Tests are scoped to disposable PostgreSQL and fake GET-only metadata:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_attested_accounting_414f3.py -v --tb=short -x
python -m pytest tests/test_warehouse_terminal_evidence_414f2.py tests/test_warehouse_attested_accounting_414f3.py -q --tb=short -x
```

Production release remains NO-GO until actual runtime login-role privileges,
BigQuery IAM and publication retention, reviewed live transport, execution
recovery and broader integration/cancellation/result handling are accepted.
