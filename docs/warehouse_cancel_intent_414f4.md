# 414F4 — durable cancellation intention, no cloud cancellation yet

The previous 414F3 concurrency checkpoint passed locally: **6 focused and
12 combined** tests.

This step introduces migration **0059** and a trusted cancellation-intent
service. Only a worker holding the current fenced recovery lease may request
cancellation after a committed possible-send boundary. The request binds to
the original immutable BigQuery job ID and request fingerprint; retries are
idempotent, and the database rejects mutation/deletion/truncation.

**A cancellation request is not a successful cancellation.** This release
does not call BigQuery jobs.cancel, mark the journal CANCELLED, settle billing
or release publication holds. A future transport must use the original job,
perform bounded cancel IO without blind retry and then independently
observe jobs.get for a final status. If the job already completed, actual
terminal evidence and accounting still govern the result.

Run on disposable PostgreSQL fixture schemas only:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_cancel_intent_414f4.py -v --tb=short -x
python -m pytest tests/test_warehouse_accounting_concurrency_414f3.py tests/test_warehouse_cancel_intent_414f4.py -q --tb=short -x
```

The production cloud cancellation transport, PostgreSQL runtime role
acceptance, BigQuery IAM/physical publication retention and end-to-end
release remain NO-GO.
