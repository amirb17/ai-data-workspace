# Step 414C2 — Published-head concurrency verification

This checkpoint adds integration tests at
`tests/test_warehouse_query_publication_pins_concurrency_postgres.py`.

## Mechanism under test

The tests use the existing `reliability_pg` fixture and the normal
`ReliabilityService` manifest/intent/reconciliation/publish workflow to
create a real committed PostgreSQL publication head in an isolated schema.
Migrations 0048 and 0049 are applied only within that disposable schema.

1. A current publication can be selected as an immutable attempt pin while
   the query reservation table remains empty.
2. After a later valid publication advances the head, a *new* attempt cannot
   select the stale publication.
3. An already committed historical pin remains immutable after head advance;
   preservation of the row **does not** guarantee physical table retention.
4. During a pin INSERT transaction, the trigger's `FOR SHARE` head lock
   rejects a concurrent head UPDATE under a bounded `lock_timeout`.
5. Duplicate pins, updates and deletes fail closed.

## Explicit limitations

- PostgreSQL pinning is a point-in-time claim about publication metadata.
  It is not an execution-time fence, a user authentication mechanism or
  BigQuery dispatch permission.
- Provider table retention, revocation semantics, publication ACLs, and a
  durable link to a specific query reservation remain unresolved.
- Tests rely on existing in-memory reference target behavior for warehouse
  publication only; no live query SQL is submitted.
- This stage is **awaiting local PostgreSQL verification** until the
  full integration run is reported.

**Release decision: NO-GO for live warehouse queries.**
