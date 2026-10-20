# Phase 10: authentication and ownership

This maintained guide describes the V1 authentication and ownership boundary.
Runtime manifests, database snapshots, logs, screenshots and JUnit output are
local acceptance evidence and are not source-controlled requirements.

## Authentication

Session authentication resolves the server-side user ID and immutable account
owner key. Opaque random session cookies are HttpOnly and SameSite=Lax, with
Secure host-prefixed cookies under HTTPS. Sessions expire after thirty minutes
idle or twelve hours absolute. Mutations require the configured Origin and a
signed CSRF cookie/header pair. Password reset/change revokes existing sessions.

Passwords use Argon2id. Signup and password recovery require fresh, expiring,
one-use email proof with request/verification limits and resend cooldowns.
Verified active Google accounts may establish a first password through that
same recovery flow. Google OIDC validates state, nonce, S256 PKCE and signed
claims; provider tokens are not persisted. Account linking retains the reviewed
verified-email and existing-account checks.

Public/auth pages are separate from protected `/app` routes. Sign-out and
account changes clear active views and recheck identity while retaining local
per-user product preferences. No roles, organizations or billing are added.
See [configuration](phase10-auth-configuration.md) and [environment inventory](../.env.example).

## Current ownership and history

`workspaces.owner` and `datasets.owner` determine current resource authorization.
Workspace membership and both owners must agree. Historical uploader, creator,
reviewer and executor fields remain provenance and never grant current access.
This applies to files/processing, profiles, understanding, relationships,
metrics, analytics, Ask and saved SQL/history.

Profile identity follows dataset, trusted source state and algorithm version,
not the mutable owner. Workspace/relationship v1 signatures preserve their
legacy owner slot as an exact-resource provenance-origin token. It is never
an authorization alias; genuine source, membership and configuration drift
still invalidates derived results. No historical output is rewritten merely
because the resource owner changes.

## Migrations and controlled adoption

`migrations/0027_authentication.sql` adds account/session/security records.
The additive, explicitly guarded migration
`migrations/pending/0028_ownership_adoption_events.sql` is the reviewed source
already applied to development. Its SHA-256 is
`5b062c6192640ddb90b9ac7b4a0dbab435b9c7f2b623ae61bd50b602323389c3`.
The pending-directory location is retained; do not blindly reapply or rewrite
an applied migration. Its companion rollback requires an empty provenance
table and restores the original compatibility functions. Never discard a
committed event to undo ownership.

`tools/ownership_adopt.py` is an operator-only command, read-only by default.
It requires an external, independently reviewed fixed manifest and a verified
active operator. The V1 command restricts database/schema, source, target 34965
and exact 12-workspace/30-dataset counts. `--execute` additionally requires the
exact approved manifest digest. No HTTP route or automatic invocation exists.

The service locks business writes, rechecks scope/owners and all business
fingerprints, refuses active/interrupted work, inserts one append-only event,
updates only direct owner values and verifies deferred consistency atomically.
Failure rolls back the entire transaction. Same-manifest replay is
`ALREADY_ADOPTED` with zero updates/inserts. Guarded reversal requires separate
approval, exact original scope and unchanged business fingerprints; it appends
provenance rather than rewriting history. This guide does not authorize execution.

## Accepted engineering evidence

The approved development transfer completed on 2026-10-20: 12 workspace owners,
30 dataset owners and one committed ADOPT event. Legacy ownership became 0/0;
other principals remained 3/3 and database totals remained 15/33. Business and
historical-actor fingerprints stayed unchanged. Real Google/session acceptance
covered all adopted workspace/dataset reads; the read-only resource matrix
passed 849 API checks and 204 historical-upload checks. Replay made zero writes.

Backend acceptance covered 398 distinct available cases and a 72-case focused
run. The pre-adoption 398-case run was uninterrupted. Post-adoption coverage
was resumed after an interrupted process and one PostgreSQL connection timeout;
the blocked case passed on retry without application changes. This is not a
claim of an uninterrupted post-adoption pass. Missing historical untracked
test sources were not invented or counted.

The accepted working frontend passed 31 scripts, lint and build. That checkout
also contained a separate data-model feature; it is excluded from this scoped
commit. Historical backend-test deletions remain local and are not committed.
Three reviewed fixture corrections accompany the ownership policy: matching
current owners, foreign current ownership for denial, and a controller-released
semantic interleaving barrier. No assertion is removed.

The isolated staged scope passed 44 lightweight backend tests, 30 frontend
scripts, frontend lint/build and the standalone operator CLI smoke check. The
separate model-feature test is intentionally absent from that 30-script scope.
Applied migration checksums, staged secret scanning and cached diff checks pass.

Run frontend checks with `npm test`, `npm run lint` and `npm run build` from
`frontend/`. Run backend checks with `uv run pytest`; use `RUN_DB_TESTS=1` against
a disposable PostgreSQL test environment for integration coverage. Synthetic
adoption/reversal tests operate in isolated schemas, not live business scope.
No deployment or Phase 11 implementation belongs to this milestone.
