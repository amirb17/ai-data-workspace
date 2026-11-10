# Step 414D4d — Server-owned current physical binding (NO-GO)

**Status:** Implementation and defensive regression tests committed; local
PostgreSQL verification pending.

This internal service accepts **only** an existing trusted governance
context and server-issued attempt handle. In a single database transaction
it resolves the attempt, active/EXECUTING linked reservation, immutable
logical draft, approved model and source membership, exact current pinned
publication, and serving materialization.

It reuses the existing metric warehouse source path:
`metric_warehouse_bindings` → `_publication_state_id` → `_source`.
This performs production checks of physical resource naming, publisher
state, model configuration, serving-receipt hashes and generated column
mapping. It then invokes `bind_draft` and `compile_single_model_sql`,
and requires the durable reservation's exact compiler intent hash and the
attempt's server-owned token to match.

No physical identifiers, raw SQL, query object, publication binding or
provider instance are accepted from the caller.

## Deliberate restrictions

- Single approved FACT logical draft only for this checkpoint; reviewed
  DIMENSION and joins/SCD2 need their own explicit compiler support.
- The current-head-only resolver fails closed if the pinned publication is
  historical, even if the historical publication remains metadata-retained.
- The result contains no SQL string and cannot authorize execution, claim
  a reservation or dispatch a job.
- The first local test suite covers **negative/fail-closed paths only**,
  including synthetic receipts that must be rejected by the authoritative
  production verifier. A positive, independently materialized serving
  receipt integration test remains an explicit follow-up before relying on
  this chain.
- External BigQuery physical retention, provider attestation, true
  authenticated dispatch and recovery integration are still release
  blockers.

**NO-GO for live BigQuery analytical queries.**
