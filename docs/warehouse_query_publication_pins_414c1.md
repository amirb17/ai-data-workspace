# Step 414C1 — Database-only publication selection pins

This checkpoint adds `migrations/0049_warehouse_query_attempt_publication_pins.sql`.
It is deliberately **not** the finished 414C authorization/retention fence.

## What the migration guarantees

- A pin references one durable, server-owned attempt and one publication
  resource, with a unique `(attempt_id, resource_id)`.
- An INSERT checks that the original attempt's workspace owner is still
  current and that the resource belongs to the workspace and remains an
  active development resource.
- An INSERT requires an existing publication matching the **current**
  `warehouse_publication_heads` entry and revision.
- PostgreSQL `FOR SHARE` row locks prevent concurrent head updates from
  racing *inside the insertion transaction*.
- Existing pins cannot be updated, deleted, or truncated through normal
  SQL operations. The table is additive and makes no provider calls.

## What is **not** guaranteed

- This is a transaction-time snapshot, **not** an execution-time permit.
- No physical provider table lifetime, cleanup protection, or reserved-job
  retention is established.
- Head advance or ownership revocation may occur after the pin commits.
- No transactional link from a query-governance reservation or actual
  BigQuery job to this pin has been established.
- No proof that an arbitrary internal caller supplying `attempt_id` is
  the authenticated request principal. Future service operations must use
  authenticated trusted repository writes, not accept arbitrary pin inserts.
- The current test set covers negative/structural cases, not successful
  publication publication fixtures or parallel head-update races.

## Remaining 414C2 work

Build a real approved-and-published resource fixture, validate successful
current-head pinning, prove stale heads are denied, exercise concurrent
writer/head-advance conflicts, and implement a retention/revocation fence
whose guarantees extend through bounded provider job completion.

**Release decision: NO-GO for live BigQuery query execution.**
