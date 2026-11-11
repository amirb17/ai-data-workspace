# 414F4 — simulated single-attempt cancellation and independently verified recovery

The preceding 414F4 durable cancellation intent stage passed locally:
**4 focused / 10 combined** tests.

The pure `query_cancellation_transport.py` contract accepts a fixed,
validated project/location/BigQuery job ID and an explicit, injected transport
with no automatic retry. It performs at most one cancel operation. Both
acknowledgements and exceptions return an unverified outcome that REQUIRES
independent `jobs.get` attestation. This interface grants no job permission
and does not itself validate the database cancellation intent.

The integration tests exercise **disposable PostgreSQL** with migration 0059
and a **fake cancel** client. They first pin the exact job, record one
SEND_BOUNDARY, persist the immutable cancellation intent, make one fake cancel
request, and then use the existing GET-only provider-policy attestation to
observe either SUCCEEDED, CANCELLED or NOT_FOUND. A request to cancel does
not force a CANCELLED outcome; a successful job remains successful. Billing
does not settle and publication dependencies remain held in this stage.

Do **not** connect this cancel adapter to a live provider, public API or
automated retry loop. Production cancellation additionally requires a
transactionally fenced cancel-attempt claim/outbox to prevent two workers
from issuing duplicate calls, real runtime role and IAM acceptance, physical
publication retention and independent verified recovery/settlement.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_query_cancellation_transport_414f4.py -v --tb=short -x
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_cancel_simulated_recovery_414f4.py -v --tb=short -x
python -m pytest tests/test_warehouse_query_cancellation_transport_414f4.py tests/test_warehouse_cancel_simulated_recovery_414f4.py -q --tb=short -x
```
