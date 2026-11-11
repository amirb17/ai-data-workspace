# 414F1 — one-attempt transport contract and simulated PostgreSQL lifecycle

Prior checkpoint: **5 focused and 9 combined** pinned-boundary tests passed.

This slice supplies a transport protocol that accepts one exact server-built
`BigQueryJobRequest` and makes at most one insert call. All outcomes
(including a returned acknowledgement) require independent jobs.get metadata
verification. Timeouts, duplicate conflicts, forbidden, lost responses and
other transport errors become `INSERT_OUTCOME_UNKNOWN`, not failure proof
and never trigger an automatic retry or a newly generated job ID.

The **production transport is not connected**. This module does NOT accept
credentials by default or provide a public endpoint or a release permit. Its
unit suite uses a fake transport; its integration suite first persists the
fingerprint and actual PostgreSQL SEND_BOUNDARY, then makes one fake insert
outside PostgreSQL and proves no second boundary, no usage settlement, and
the same job identity on ambiguous responses. The simulation is deliberately
not a substitute for cloud-side role/IAM acceptance or a vetted real
BigQuery jobs.insert binding.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_query_submission_transport_414f1.py -v --tb=short -x
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_simulated_submission_414f1.py -v --tb=short -x
python -m pytest tests/test_warehouse_query_submission_transport_414f1.py tests/test_warehouse_simulated_submission_414f1.py -q --tb=short -x
```

**Next:** bind the reviewed real transport only after PostgreSQL identities,
BigQuery IAM/table retention and query cost controls are independently
accepted; ensure an explicit process-level production release gate and
recovery lookup under every ambiguous outcome. Cancellation and verified
billing remain F2/F3 work.
