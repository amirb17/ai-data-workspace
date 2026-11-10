# Step 414C3c — Read-only linked-publication eligibility inspection

**Status:** Code and tests committed; PostgreSQL verification pending.

The inspection in
`app/db/warehouse_query_publication_inspection_repository.py` checks:
- Current workspace ownership against PostgreSQL.
- An immutable attempt-to-reservation link, matching exact server token,
  SQL consumer, BigQuery provider, and unexpired ACTIVE / EXECUTING status.
- Between one and two pinned, registered, development resources.
- Resource-level retirement transaction locks and absence of a recorded
  retirement fence.

An existing query whose publication head subsequently advances remains
metadata-consistent until terminal settlement/retirement. Head advancement
alone does not retroactively revoke the historical immutable pin.

The result is deliberately named `metadata_consistent`, and the returned
`execution_authorized` is always `False`. It is diagnostic information,
**not an execution permit**.

## Unresolved P0/P1 blockers

- A transaction-scoped inspection does not confer a lock across network
  provider IO or prevent out-of-band physical BigQuery table deletion.
- No trusted authenticated handler, sealed reviewed compiler result, durable
  dispatch grant, or true atomic revocation handshake is present.
- Provider table ACLs, physical retention guarantees, recovery concurrency,
  source publication revocation and cloud job identity verification remain
  subject to integration/security review.
- No provider client is constructed; no query is dispatched.

**NO-GO for live analytical BigQuery query execution.**
