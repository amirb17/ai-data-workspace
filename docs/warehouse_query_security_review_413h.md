# Step 413H — Warehouse analytical-query security gate review

**Scope:** reviewed warehouse SQL compilers, evidence, dry-run, PostgreSQL
admission, preclaim, claim rehearsal, BigQuery read provider, and job recovery.
**Decision:** **NO-GO for live warehouse query dispatch** as of this review.
The review is a code-level assessment, not a penetration test or operational
sign-off. Steps 413A–G have local integration tests; passing them does not
constitute end-to-end execution authorization.

## Verified contracts (code-level)

- SQL single-model/SCD1/SCD2 compilers produce immutable, bounded
  `CompiledQuery` v2 objects without provider dispatch.
- BigQuery estimates are dry runs that verify referenced physical tables,
  statement type, views/routines, and bound cost confidence.
- The existing PostgreSQL query-governance repository owns budgets, concurrent
  reservations, ACTIVE→EXECUTING claims, and settlement.
- Warehouse preclaim may release *only* an ACTIVE reservation. A claimed
  reservation must be retained for recovery.
- BigQuery job recovery looks up a deterministic reservation-scoped job ID.
  Step 413G also checks that identity before terminal settlement.
- Warehouse recovery settlement does not initiate a new provider query.

## Release blockers

### P0 — No authenticated dispatch entry point

The warehouse chain currently passes typed but forgeable values, including
`WarehouseCompiledEvidence` (with default true attestation booleans),
`WarehousePreclaimResult`, and callable `resolve_current`/
`compile_current` implementations. These types **do not establish identity
or provenance**. A production gateway must instantiate them only from a
trusted, authenticated server-side repository resolution tied to the owner and
workspace; never accept them from an API payload, browser, or arbitrary caller.

### P0 — Publication freshness not atomic with provider dispatch

The claim rehearsal validates before and after claim, but does not pin
publication state with an ownership/authorization fence that extends through
the actual dispatch decision. Changes between the last verifier call and a
future `provider.execute` remain possible. Implement a single approved
dispatch boundary with a demonstrated publication/ownership protocol
(e.g., immutable published snapshot plus an explicit read policy, or a
transactional authorization fence compatible with provider IO). Define the
guarantee precisely and test adversarial races before enabling it.

### P0 — Claim rehearsal is intentionally non-executing

`inspect_claim_transition_without_dispatch` creates a durable EXECUTING
reservation but deliberately does not submit a job. This can leave an
unsettled reservation until explicit recovery/operations intervention.
Do not expose this function as a user-facing execution endpoint. An
unsuccessful claim or recovery with no provider job must never trigger an
unverified retry.

### P1 — Explicit execution-request idempotency

Compiled templates are deterministic, while server-issued attempts are
random. A new token means a new reservation. A future API must define how
client retries map back to the *same* server-side attempt without minting
another token, and how the original attempt is resumed/recovered after a
timeout. The client must not be allowed to choose physical pins, SQL or
execution tokens.

### P1 — Strong provider trust and recovered-job evidence

Current warehouse settlement verifies the expected deterministic job ID,
but receives a provider object as an argument. In production, provider
construction must be server controlled. Confirm BigQuery job project,
location, labels, query configuration, SQL hash/table references, and
successful-job billing under the exact original execution policy.
A matching job ID alone is insufficient provider authorization.

### P1 — Isolation and resource lifecycle

Re-run whole-lifecycle tests across process crashes, ownership changes,
expired ACTIVE leases, stuck EXECUTING jobs, concurrent recovery workers,
and provider/network ambiguity. Define administrative reconciliation for
permanently NOT_FOUND jobs without silently releasing accounting.

## Release acceptance criteria

- An authenticated server-owned gateway re-resolves all current pins and
  authorizes the specific principal/workspace at dispatch.
- It accepts no untrusted SQL, arbitrary providers, evidence flags, permits,
  or claim rehearsals as authorization inputs.
- Every actual BigQuery request is bounded by a durable claimed reservation,
  fresh verified metadata, row/byte/time ceilings, exact referenced tables,
  and safe, deterministic recovery identity.
- Every failure pathway demonstrates no duplicate dispatch and correct
  permanent/temporary accounting.
- Race/fault injection and real provider integration tests pass under
  deliberately isolated development settings, with an explicit enable flag.
- A separate code review approves the dispatch path; execution stays disabled
  until then.

**This review does not change the existing execution configuration or
enable any live BigQuery queries.**
