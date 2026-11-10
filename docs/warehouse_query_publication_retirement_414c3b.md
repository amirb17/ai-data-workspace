# Step 414C3b — Metadata-only publication retirement fencing

**Status:** PostgreSQL migration and regression tests committed; local tests pending.

Migration 0051 adds an immutable `warehouse_query_publication_retirement_fences`
record for a historical publication. A retirement fence insert rejects the
current head, and rejects a historical publication while any linked SQL
reservation is ACTIVE/unexpired or EXECUTING.

A companion trigger serializes *new reservation link creation* with
retirement using a resource-specific PostgreSQL advisory transaction lock.
If retirement commits first, a later link to that publication is denied.
If a link commits first, retirement observes its live dependency and is
denied. Fences are immutable. Publication-head advancement is permitted.

## Scope and remaining limitations

- This checkpoint **does not delete a BigQuery table**. It marks metadata
  ineligible for new query links; cleanup tooling is not wired to it.
- No cleanup operation may interpret fence existence as deletion approval.
  A future deletion protocol must revalidate provider job completion,
  recovery, retention windows, physical identifiers and current ownership.
- Guarded link creation still depends on the lower-level 0050
  ownership/token/current-head check.
- Publication head updates may occur after query pinning. The fence
  protects linked, live reservations against metadata retirement, but
  does not preserve physical tables against out-of-band cloud deletion.
- The query gateway is not authenticated or implemented for live execution,
  and recovery/lease expiration semantics require release review.
- This migration introduces no cloud calls and no user-facing routes.

**Live analytical BigQuery execution: NO-GO.**
