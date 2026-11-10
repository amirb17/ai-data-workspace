# Step 414A — Authenticated warehouse dispatch design (NO-GO)

**Status:** Architecture proposal and non-dispatch contract only. This step does
not enable a BigQuery query, create a reservation, or grant authorization.
**Prerequisite:** Step 413H security review, including all P0 blockers.

## Security boundary

The future gateway is the *only* component permitted to assemble an execution
decision. A handler may supply the authenticated request context, a server-issued
attempt identifier, and a reviewed logical draft reference. The handler MUST
NOT supply SQL, physical tables, provider clients, compilation evidence,
publication pins, request tokens, execution permits or mutable query flags.

The gateway must get workspace + owner identity from the authenticated session
and confirm authorization against PostgreSQL in the same trusted request
context. A client-controlled `owner_key` or workspace ID is not sufficient.

## State machine and ownership

```text
authenticated logical request
    |
    v
server-issued stable attempt mapping (retry-safe)
    |
    v
fresh owned publication resolution + reviewed compiler
    |
    v
independent BigQuery metadata attestation + dry-run estimate
    |
    v
PostgreSQL admission (ACTIVE, budget held)
    |
    v
fresh independent authorization/publication checks
    |
    +---- any uncertainty --> ACTIVE-only release / human revalidation
    |
    v
atomic PostgreSQL claim (EXECUTING)
    |
    v
dispatch-fence / immutable publication read contract (P0 unresolved)
    |
    +---- verification uncertainty --> EXECUTING / recovery-only
    |
    v
bounded BigQuery SELECT with deterministic reservation-scoped job ID
    |
    +---- provider ambiguity --> EXECUTING / recovery-only
    |
    v
transactional terminal settlement (COMPLETED / FAILED)
```

The `ACTIVE -> EXECUTING` transition is not by itself an authorization
decision. Nothing after claim may re-admit, release or submit a fresh token
as a recovery shortcut. Repeated settlement is governed by the existing
transactional repository and must never cause another BigQuery submission.

## P0 publication-consistency decision to implement before dispatch

**Candidate design:** immutable published physical tables and an authenticated
versioned snapshot reference. PostgreSQL transactions verify that the same
principal can read the exact approved publication/receipt snapshot. The
system must guarantee that physically immutable tables remain queryable for
the duration of a claimed job (retention/ref-counting or equivalent). The
"current publication" pointer may advance without mutating those referenced
tables; whether old publications remain authorized must be an explicit
policy, not inferred. Revocation/deletion must be fenced against admission
and dispatch.

A post-claim freshness check alone is NOT atomic with network IO. Before
implementing, decide whether access to an *immutable authorized snapshot* is
allowed after publication advancement, and specify the ownership fence,
retention window, revocation semantics, table ACLs, and cleanup protocol.
Do not keep a PostgreSQL transaction open across cloud IO as an
undocumented substitute.

## API contract proposed for future implementation

Request may contain only a logical reviewed draft identifier and a
server-issued attempt handle (for retry/resume). Response may expose only
non-sensitive lifecycle status, bounded rows, usage and recovery handle.
Do NOT return table names, internal binding hashes, SQL, raw provider
credentials, execution permits, or provider job secrets.

Do not reuse forgeable `WarehouseCompiledEvidence`,
`WarehousePreclaimResult` or `InternalDraftBinding` instances as independent
authorization credentials. They are diagnostics, not capabilities.

## Mandatory failure/race scenarios

1. Owner loses permission after planning, before admission: no claim/dispatch.
2. Publication advances during metadata read: reject or use formally pinned
   immutable snapshot policy; never silently switch physical tables.
3. Publication revocation immediately before dispatch: deny under the
   explicit revocation fence, not a best-effort callback.
4. Lease expires before claim: no provider submission.
5. Network fails after claim, before provider job acknowledgement: retain
   EXECUTING; lookup deterministic job, never generate a new token.
6. Two concurrent workers retry same handle: at most one claim and one
   deterministic provider job; no duplicate budget reservation.
7. Foreign workspace/owner or forged evidence: reject before cloud IO.
8. Provider returns a mismatched job identity/labels/location: never settle.
9. Recovered terminal job and crash during accounting: complete/fail
   idempotently in PostgreSQL with one usage record.
10. Stale claim with permanently missing job: documented operator
    reconciliation; no unsafe automatic release.

## Implementation order

- **414B:** server-side attempt registry, authenticated ownership and durable
  retry mapping (no provider execution).
- **414C:** immutable publication read policy, revocation/retention fence and
  adversarial PostgreSQL concurrency tests.
- **414D:** gateway authorization preparation and mocked provider rehearsal.
- **414E:** full fault injection and security tests, isolated provider
  integration, independent approval and explicit production feature gate.

**Release criterion:** Until P0 gateway provenance AND atomic publication
consistency are demonstrably solved, dispatch remains **NO-GO**.
