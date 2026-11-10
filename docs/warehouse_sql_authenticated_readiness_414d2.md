# Step 414D2 — Authenticated, non-dispatch gateway readiness

The new internal service
`app/services/warehouse_sql_authenticated_readiness_service.py`
takes only an ASGI request, workspace ID, and server-issued attempt handle.

It requires configured session authentication and a valid user session
resolved by the existing authentication service. It deliberately refuses
development-identity fallback, arbitrary caller-provided principals,
unconfigured secrets, and missing/invalid sessions.

Once authenticated, it instantiates the existing governance service using
the trusted principal, then delegates to 414D1 PostgreSQL metadata
readiness. It **does not** accept client SQL, physical publication pins,
query tokens, provider instances or execution flags, nor does it claim
a reservation, create an execution permit, dispatch a query, or expose
a new HTTP route.

## Limits before any live execution

- The authenticated session lookup occurs before the governance transaction.
  The future dispatch gateway still needs an atomic authorization and
  revocation decision at the point where a provider job is submitted.
- The existing generic governance service's constructor can still be used
  by trusted internal callers with a dictionary; this wrapper does not
  automatically make all other pathways authenticated.
- No proof of compiled-query provenance, physical provider-table retention,
  job recovery or cloud-side permissions is provided by this checkpoint.

**Live BigQuery analytical-query dispatch: NO-GO.**
