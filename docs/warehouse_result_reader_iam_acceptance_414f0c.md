# 414F0-C — BigQuery result-reader IAM isolation acceptance

Local checkpoint preceding this task: **12/12** read-only transport and
PostgreSQL-to-fake-BigQuery tests passed (2026-11-11).

## Confirmed Google BigQuery permission semantics

Google documents `jobs.getQueryResults` requiring
`bigquery.jobs.get` on the original job **and**
`bigquery.tables.getData` on its result destination table. Where the
caller is the job's creator, the documented alternative uses
`bigquery.jobs.create` on the project and destination data access.
This project deliberately prefers a separate **non-creator** result
reader, to prevent the read identity from submitting additional jobs.

Official source: https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/getQueryResults

## Required review, not automatic grants

1. Identify the production project and region, the actual job-creator
   principal and the separate result-reader service-account principal.
2. Inspect the effective IAM permissions on **the original job** and
   **each query destination table**. Table data permissions cannot be
   inferred from job access alone. Job creator exemption is intentionally
   rejected for the dedicated reader.
3. Inspect inherited project/folder/org policies, impersonation chains,
   roles, deny policies, BigQuery row/column policy and resource lifetime.
   Confirm reader has no `jobs.create`, table update, delete, create,
   IAM-policy mutation or broad project-owner/admin grants.
4. Independently verify that the dedicated **warehouse serving
   publication tables** cannot be deleted/altered by query, result,
   API-web, or ordinary cleanup principals. Protect retention holds
   from out-of-band cloud deletion and demonstrate negative tests.
5. Prove authorized one-job retrieval and rejected cross-workspace,
   foreign-job, expired-session, missing destination-data permission,
   denied impersonation and protected-table mutation requests using
   actual constrained accounts in a disposable reviewed cloud project.
6. Evidence must include time, region, principal identifiers (redacted
   in shared outputs as necessary), exact scoped resources, policy
   revision, negative test results and independent reviewer approval.

## Repository review contract

`app/warehouse/query_result_iam_preflight.py` is a **pure offline
inventory validator**, not a permissions probe. It rejects missing
job/destination data access and explicit mutation/broad permissions,
and always returns `NO_GO`, even with a narrow synthetic inventory.
It never authorizes deployment or changes IAM.

The user must not run `gcloud ... add-iam-policy-binding`,
`terraform apply` or `bq rm` as part of this step. Real-resource
permission inspection and negative tests require separately reviewed
account credentials and change windows.

No live BigQuery client, provider endpoint, warehouse execution switch
or IAM grant was added.

Run:
```powershell
git pull --ff-only origin feature/warehouse-model-ux
python -m pytest tests/test_warehouse_query_result_iam_preflight_414f0c.py -v --tb=short -x
python -m pytest tests/test_warehouse_bigquery_readonly_transport_414f5.py tests/test_warehouse_query_result_iam_preflight_414f0c.py -q --tb=short -x
```
