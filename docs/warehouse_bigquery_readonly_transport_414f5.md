# 414F5 — explicit BigQuery read-only first-page transport

Prior local validation: **19 focused / 21 combined** adapter and
fake BigQuery bridge tests passed on 2026-11-11.

New module `query_bigquery_readonly_transport.py` accepts only an
explicitly injected and reviewed JSON connection with exact project
and asia-south1 scope. Calls the published BigQuery
`jobs.getQueryResults` REST path using GET with the original deterministic
job ID, bounded maxResults (1..100), timeoutMs=1000, HTTP timeout=10 and
retry=None. All provider failures are redacted. No client factory,
default credentials, query submission, polling, cancellation or execution
grants exist here.

Unit tests verify exactly one GET, strict request parameters, wrong
project/job/row bounds denied before IO, transport error sanitization,
and no implicit client startup. A separate PostgreSQL integration
test composes the original owner/session gate, verified settlement,
server-owned projection, fake JSON connection, BigQuery row decoder
and first-page result limits end to end.

**Production NO-GO:** The transport is *not mounted* to the app, nor
is a real BigQuery client/connection initialized. Explicit service-role
IAM, cloud retention, live job submission/attestation, safe cursor
ownership, per-user authorization review, result expiry, and
end-to-end deployment/operations gates remain outstanding. Existing
warehouse SQL UI is still intentionally disabled.

Official interface: https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/getQueryResults

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_bigquery_readonly_transport_414f5.py -v --tb=short -x
$env:RUN_DB_TESTS = "1"
python -m pytest tests/test_warehouse_bigquery_readonly_full_path_414f5.py -v --tb=short -x
python -m pytest tests/test_warehouse_bigquery_readonly_transport_414f5.py tests/test_warehouse_bigquery_readonly_full_path_414f5.py -q --tb=short -x
```
