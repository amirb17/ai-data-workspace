# Step 414D4a — Immutable server-owned logical-draft record

**Status:** migration 0052, service, and isolated PostgreSQL tests committed;
**local verification pending**.

Earlier 414D3 showed that matching a supplied CompiledQuery against a
reservation is not proof the SQL was actually reviewed. Step 414D4a adds
`warehouse_query_attempt_logical_drafts`: a single immutable bounded
logical SELECT draft associated with a server-issued query attempt.

At **registration**, the trusted backend:
- Requires current workspace ownership and approved model/version.
- Requires current PRIMARY source membership and active owned datasets.
- Uses the existing narrow SQL AST planner for logical SQL only
  (`SELECT` explicit columns, one `model_<id>`, bounded `LIMIT`).
- Requires the attempt's stored logical hash to match the normalized
  draft; refuses changed SQL under the same attempt.
- Persists normalized SQL, model/version, projections and limit
  immutably.

At **resolution**, the caller provides only an existing attempt handle.
The service loads logical SQL from PostgreSQL and reparses it against
the **current** owner-approved catalog. It rejects missing, stale,
unauthorized or mismatched draft metadata. No external SQL input or
physical table name is accepted by the resolution path.

## Boundaries and explicit blockers

- The registration function is internal and accepts *logical SQL*
  before review; the future authenticated API must permit only a
  reviewed draft-creation workflow and **must never use submitted SQL
  as an execution request**.
- The logical draft digest is **not** the v2 compiled physical intent
  hash. The existing compiler-derived request token differs from the
  server-issued attempt registry token. The next integration step
  must explicitly reconcile these distinct identities without relaxing
  the uniqueness/retry contract.
- This is single-model logical SQL draft support only. Reviewed
  joins/SCD2 need equally strict persisted relationship inputs.
- The resolver does not yet construct a trusted physical source,
  provider metadata attestation, or authoritative v2 CompiledQuery.
- No execution permit, HTTP route, cloud IO, claim or dispatch added.
  Physical BigQuery retention and revocation still need end-to-end
  operational verification.

**Live warehouse analytical SQL execution: NO-GO.**
