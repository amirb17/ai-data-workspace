# Step 414C3a — Query-reservation/publication dependency linkage

**State:** additive migration + isolated integration tests committed;
**local PostgreSQL verification pending**.

Migration 0050 links a registered warehouse query attempt to exactly one
existing ACTIVE owned BIGQUERY/SQL governance reservation with the same
server-issued request token. An attempt must have one or two published
resource pins. When linking, PostgreSQL locks and rechecks each head, and
the pin set becomes immutable. Link rows are append-only.

`warehouse_query_publication_dependency_present(resource_id,publication_id)`
reports that an actual linked reservation is ACTIVE and unexpired or
EXECUTING. COMPLETED/FAILED/RELEASED reservations do not count. This is a
**read-only advisory signal**, not permission for provider table deletion.

## Still missing / NO-GO

- No cleanup or provider table-deletion path consumes this dependency check
  under an atomic retirement fence; any external administration can still
  delete physical tables.
- An expired ACTIVE lease is not considered held by the advisory function,
  but lifecycle cleanup must still lock/recheck durable ownership and state.
- A link may be created before a later publication advances; historical
  pins do not freeze the current head after the committing transaction.
- The owner is checked at link time, not continuously as an execution grant.
- The logical attempt digest is distinct from the compiled query intent hash;
  binding those safely requires the trusted compiler and gateway.
- No query provider execution, dispatch gateway, authorization grant or
  retention cleanup is implemented by this stage.
- Future publication cleanup requires a same-transaction state transition,
  a safe physical deletion protocol, no ACTIVE/EXECUTING references and
  protected table immutability/retention across provider IO.

**Live BigQuery analytical query dispatch remains NO-GO.**
