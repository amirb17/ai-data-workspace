# 414F1 — exact pinned request to one-way send journal boundary

The durable fingerprint suite passed locally: **4 focused and 7 combined**.

This slice adds `record_pinned_send_boundary_without_dispatch` to the
**existing** warehouse send/recovery service. The trusted worker must possess
a current typed recovery lease, match the CAS revision and original owner,
pass the live account/session/workspace/serving/budget checks, and have a
durable policy-bound envelope row from migration 0058. Only then may it append
a single `SEND_BOUNDARY` event to the current journal and transition to
`SEND_UNKNOWN`.

The DB commit is the linearization point. Repeated calls and reclaimed
workers with the same deterministic job ID are forced into lookup-only
recovery; no new boundary can be created. An absent fingerprint, revoked
session or stale lease fails closed. The original request row remains
immutable, and the EXECUTING reservation and publication holds stay intact.

**This method does not send, return SQL, authorize provider execution, grant
IAM or settle usage.** It is a necessary internal coordination primitive,
not a complete send-once transport. A future external send boundary must
only be added behind independently accepted service roles, IAM and retention
controls and must deal with ambiguous provider acknowledgements by checking
the original job identity.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_pinned_send_boundary_414f1.py -v --tb=short -x
python -m pytest tests/test_warehouse_query_envelope_pinning_414f1.py tests/test_warehouse_pinned_send_boundary_414f1.py -q --tb=short -x
```
