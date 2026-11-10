# Step 414D4e — Positive end-to-end recompilation on a materialized publication

**Status:** Four tests committed; local PostgreSQL verification pending.

This stage covers the positive path that 414D4d's negative tests did not
exercise. It starts with the existing `fact_provider_pg` fixture and
`tests.bigquery_fake.Client`, creates a real PostgreSQL warehouse
publication via the existing provider workflow, and materializes its
serving table through the **fake in-memory BigQuery client**.

After applying the query governance/attempt migrations to the disposable
schema, the test:
1. Registers an immutable, reviewed logical draft and server-issued attempt.
2. Uses the existing production `metric_warehouse_bindings`,
   `_publication_state_id`, `_source`, `bind_draft`, and v2 compiler
   to derive the exact reservation intent and serving table.
3. Pins that publication, admits a bounded SQL reservation, and links it
   immutably to the attempt.
4. Calls `recompile_stored_warehouse_attempt_without_dispatch` with
   only the trusted governance context and attempt handle.
5. Requires exactly the same compiled intent, unchanged ACTIVE
   reservation, no execution authorization, and no additional fake-cloud
   provider interactions.

## Strict safety scope

The fake provider's materialization is a **warehouse data-loading test**,
not an analytical SQL read or live Google BigQuery job. All query
recompilation is PostgreSQL metadata + pure Python compilation. No query
claim or analytical BigQuery dispatch code is added.

Successful compilation does not establish physical retention against
out-of-band cloud deletion, session provenance at live dispatch, atomic
revocation authorization, or job recovery. Those are subsequent P0
release gates.

**Live warehouse analytical-query dispatch: NO-GO.**
