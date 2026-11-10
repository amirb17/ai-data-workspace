# Step 414D4c — Server-resolved serving receipt for an owned logical draft

**State:** PostgreSQL-only resolver and tests committed; local verification
pending. No cloud execution was added.

The resolver accepts only `QueryGovernanceService` and a server-issued
attempt handle. In one read transaction it:

1. Resolves the existing attempt under current workspace ownership.
2. Requires an ACTIVE/EXECUTING linked SQL reservation and its immutable
   publication dependency.
3. Retrieves the stored logical SQL, revalidates current approved model
   and dataset source ownership, and verifies the attempt's logical digest.
4. Joins the existing resource, exact pinned publication, and immutable
   `warehouse_serving_materializations` receipt. Missing or ambiguous
   receipt fails closed.
5. Returns server-resolved publication/receipt IDs and checksums with
   `execution_authorized=False`. It does not expose an execution permit.

The integration suite exercises missing receipt, genuine PostgreSQL
receipt lookup, foreign ownership, terminal release, and static no-dispatch
checks. Test-only receipts are synthetic PostgreSQL rows, not proof of an
actual physical BigQuery table.

**Still required:** independently reconstruct and validate exact column
mappings, generate the physical table through the provider's trusted
naming contract, recompile the stored draft in the same trusted chain,
attest real provider metadata, and implement a revocable durable
authorization boundary + physical-retention protection. No HTTP execution
route, claim, or BigQuery query is introduced.

**Live BigQuery analytical-query dispatch: NO-GO.**
