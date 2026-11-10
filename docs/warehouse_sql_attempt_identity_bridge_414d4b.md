# Step 414D4b — Query identity separation (non-dispatch)

**Status:** helper + tests committed; local verification pending.

There are two distinct identities in the existing design:

1. `CompiledQuery.intent_sha256`: a reproducible hash of the compiled
   statement and immutable physical publication binding. Changing a
   publication receipt or SQL changes this hash.
2. `warehouse_query_attempt_registry.request_token`: a
   cryptographically random **server-owned attempt token** that must be
   preserved when an existing handle is resumed. Two independently
   created attempts may compile identical SQL while retaining distinct
   request tokens.

The existing v2 compiler also produces a deterministic
`CompiledQuery.request_token`. The 414D4b helper records this original
fingerprint-derived value for diagnostics, then substitutes **only** the
server-owned request token into a newly compiled `CompiledQuery`.
SQL bytes, intent hash, physical table set and compiler version are
unchanged. This is intentionally a backend-only adapter, not a change
to the pure compiler or the reservation schema.

The tests cover same-SQL/different-attempt identity separation,
retry stability, physical receipt drift, invalid attempts, compiler
rejection, and a real PostgreSQL-issued token used with a reloaded
immutable logical draft.

## Critical remaining security boundaries

- **The helper is not an authorization or trust boundary.** Its input
  `WarehouseQueryAttempt`, `StoredWarehouseLogicalDraft`, and
  `InternalDraftBinding` values are in-memory Python objects and
  could be forged by an arbitrary caller. It does not itself fetch an
  authoritative physical binding or use the provider.
- A changed physical receipt may produce a new compiler intent under
  the *same* attempt token. An eventual admission boundary MUST use
  immutable reservation uniqueness to fail closed on this conflict.
- The eventual trusted gateway must resolve owned logical and physical
  state within a consistent transaction, compile internally, and
  reconcile the exact resulting intent with any durable reservation.
- The legacy compiler-derived token must not be interpreted as the
  authoritative retry identity or used to bypass server-issued
  attempt handles.
- Authenticated request context, BigQuery table retention, provider
  attestation, dispatch-time revocation and recovery remain blockers.

**Live BigQuery query execution stays disabled (NO-GO).**
