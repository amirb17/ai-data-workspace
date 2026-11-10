# 414D3 — DB-grounded compilation consistency (non-executing)

**Status:** code and isolated PostgreSQL integration tests committed;
local verification pending.

An internal diagnostic compares a v2 `CompiledQuery` and
`InternalDraftBinding` against authoritative existing records:
owned attempt, linked reservation status/token/intent, its immutable
publication pins, and registered model/resource/generation records.

All checks are read-only. A positive result is deliberately labelled
`CONSISTENT_UNTRUSTED_COMPILATION` because **Python dataclass values can be
fabricated**. Comparing them with durable records does not establish that
the SQL came from an authenticated reviewed logical draft.

No endpoint, query execution, reservation claim, provider call, or
authorization permit is added by this checkpoint.

## Next P0 work

The future gateway must:
1. Fetch a reviewed logical draft from owned PostgreSQL records using a
   handle supplied by the authenticated session; never accept raw SQL or
   physical bindings from the client.
2. Resolve current trusted publication, materialization and schema metadata,
   build the exact binding, and compile internally with the v2 compiler.
3. Match the compiled intent, server token and physical scope against the
   reservation and publication pins.
4. Independently attest provider metadata and dry-run under bounded policy.
5. Establish a durable, revocable authorization/physical retention fence
   across the dispatch decision, and test failure/recovery before enabling IO.

**Live BigQuery analytical-query execution remains NO-GO.**
