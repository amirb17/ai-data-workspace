# Scoped relationship locks — staged production rollout

## Why
The relationship API previously used direct `SELECT ... FOR UPDATE` on
`workspaces` and `datasets`, requiring temporary UPDATE privileges on
`workspace_name` and `dataset_name`. Those privileges permit actual edits
and must not remain in a hardened API role.

## Stage A — install (while current ECS revision keeps working)
Apply `migrations/0100_relationship_lock_prepare.sql` as a trusted migration
owner. It defines `public.lock_relationship_sources(bigint,text)` as
SECURITY DEFINER with a fixed pg_catalog search path, parent-before-child row
locks, a workspace owner-key check, no data returned, and EXECUTE restricted
to `datarise_api`. It also codifies relationship metadata write grants.
**Do not revoke the old UPDATE permissions yet.**

The function's owner must be a reviewed migration role with the required
row-lock privileges. Its owner-key argument comes from the authenticated API,
not from an independent database session identity; endpoint ownership checks
remain mandatory. Avoid granting EXECUTE to arbitrary database roles.

## Stage B — deploy and verify
Build/deploy a new API image containing the update to
`app/services/relationship_service.py`; keep ECS task environment, secrets,
networking, and worker roles unchanged. Check that the task is stable and that
the relationship page loads. Exercise discovery on a fresh, authorized test
workspace, verify evidence and explicit confirmation, and test rejection of
cross-workspace access. Existing READY discovery runs may be reused, so simply
clicking Discover in Workspace 3 is **not** proof of the new lock execution.

## Stage C — revoke after successful live validation
Apply `migrations/0101_relationship_lock_revoke.sql` as the migration owner.
This removes temporary source-name UPDATE grants from `datarise_api` and
asserts they are no longer effective, while preserving the scoped lock function.
Do not apply Stage C before Stage B; an old API revision could fail after revoke.
Run API read, discovery/review, SQL Join, and Gold/Analytics regression.

## Rollback
If Stage B fails, roll back only the ECS API image; Stage A is additive.
If Stage C has already been applied, do not roll back to the pre-capability API
without a reviewed forward fix. Never restore broad workspace/dataset UPDATE
privileges as a routine rollback. Review the function and ACLs with the
database security owner before production rollout.

## Caveats
The function restricts actions to locking rows, but `datarise_api` is shared
across authenticated end-users. The owner-key parameter is not cryptographically
bound to a PostgreSQL login. It cannot replace application ownership checks.
Relationship metadata grants remain table-level INSERT/SELECT, protected by
existing triggers and API authorization. A future role-isolated RPC would
provide a stronger boundary.
