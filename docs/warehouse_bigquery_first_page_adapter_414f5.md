# 414F5 — bounded BigQuery-shaped first-page decoding (fake only)

**Previous verified frontend acceptance (414G):** production build succeeded
with a non-blocking chunk-size warning, ESLint succeeded, and 36/36
frontend lightweight tests passed.

This phase introduces a pure BigQuery `jobs.getQueryResults` response
adapter, with a fake-provider bridge into our existing first-page
validation path. It binds exact original project/location/jobReference;
requires jobComplete=true and no errors; verifies ordered, scalar,
non-repeated column names and BigQuery types against the
server-approved expected schema; converts BigQuery REST scalar strings
safely (INT64 range, BOOL, finite FLOAT, NUMERIC preserved as text);
enforces row and serialized response-size limits; rejects unsupported
nested/repeated values and unknown fields; and keeps pagination cursor
inside the backend contract.

The bridge makes exactly one explicit transport call and is internal
simulation infrastructure. It **does not create a Google Cloud client,
load credentials, use a live SDK, enable a provider runtime, or expose
SQL execution/result fetch through HTTP**. A real getQueryResults client
binding needs separate review of API fields and SDK hydration,
retry=None, connection/request timeouts, IAM, server-owned schema,
historical publication retention, and post-network session revocation.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_bigquery_first_page_adapter_414f5.py -v --tb=short -x
python -m pytest tests/test_warehouse_bigquery_first_page_adapter_414f5.py tests/test_warehouse_bigquery_first_page_bridge_414f5.py -q --tb=short -x
```

DO NOT treat these passing tests as real BigQuery connectivity or
deployment approval. Production execution and direct result fetch remain
NO-GO.
