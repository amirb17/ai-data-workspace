# Phase 5A.1: approved rule reuse

## Behavior and scope

The existing backend already resolves dataset versions by an exact schema fingerprint within a dataset. Different physical content produces a separate upload/delivery. Previously, Bronze initialized every new file association as AWAITING_RULES, even when that version already had approved rules. Active rule rows alone cannot prove approval: saved draft answers also create active rules.

Migration 0009 adds an approved-rule-version marker on dataset versions and applied-rule-version/reuse fields on file associations. Finalization records approval and pins the association. Bronze reuses approval under a transaction and row lock only when the physical schema matches the dataset version, the positive current version equals its approved marker, active rules and saved answers belong to that version, all current questions have valid answers with matching active configurations, and active rule columns exist in the current profiles. Existing converters and suggestion generation remain authoritative; no second rule engine was introduced.

Questions are generated on read, not persisted as question rows. Reuse does not clone or modify answers. Repeated Bronze preserves progressed association status and its original rule-version pin. Public processing context separates the applied version from the current version; readiness is withheld if the active policy no longer matches the pin.

Frontend Rules shows the applied approved version and a reuse notice with no answer/save/approve controls. Processing shows Bronze and Rules ready. Silver and Gold execution remain out of scope, and processing counts are still unavailable.

## Schema and ownership

Existing backend exact fingerprint semantics are unchanged. Additive columns, removed columns, and datatype changes can produce a new dataset version and require independent review. Frontend ALLOW_ADDITIVE permits upload acceptance; it does not authorize backend rule reuse. A new profile-generated question can also prevent reuse despite an unchanged fingerprint.

Public endpoints retain principal/workspace/dataset ownership checks. Version identity and schema joins scope approval within the owned dataset; similarly named columns in another dataset or workspace provide no reuse authority.

## Migration and historical policy

Migration 0009 was applied to the development database. Its conservative legacy backfill recognizes positive active versions with progressed associations and no pending AWAITING_RULES association. Ambiguous legacy versions require explicit finalization rather than inferred approval.

Association pins retain lineage when the current rule counter changes. The existing rule tables do not preserve immutable historical definition snapshots. Before future rule editing is enabled, edits must create immutable policy versions, apply a new active version prospectively, preserve historical pins, and replay history only through an explicit user action. This phase implements no editing or historical replay.

The existing physical-file/version association may be shared by logical uploads with identical content. This phase preserves that architecture; the live same-schema test uses different physical content and a distinct logical delivery. Broader partial Bronze recovery and downstream execution changes are deferred.

## Validation

- Backend: 110 passed, including PostgreSQL tests with RUN_DB_TESTS=1. uv was unavailable; the existing .venv Python was used. One existing Starlette/httpx deprecation warning remains.
- Frontend lint and production build passed.
- Lightweight contracts, ingestion-batches, file-content-duplicates, backend-integration, and rule-approval checks passed.
- git diff --check passed.
- Added real-PostgreSQL coverage for first approval, a second different-content same-schema upload, repeated Bronze, dataset/workspace isolation, changed schemas, missing question coverage, unapproved/stale/inactive rules, and historical linkage. Storage operations in those tests are mocked; control-plane behavior uses PostgreSQL.

## Live integration

Workspace 8 / dataset 9 was used with the previously user-approved id-required policy.

1. Original upload 32 / file 22: dataset version 8, approved rule version 1, READY_FOR_SILVER.
2. New upload 37 / file 26, phase5a1-compatible.csv: different rows, separate batch, dataset version 8 reused, rule version 1 reused, READY_FOR_SILVER. Rules displays no answer or approval controls. Refresh and repeated Bronze preserved the state.
3. New upload 38 / file 27, phase5a1-additive.csv: frontend WARNING under ALLOW_ADDITIVE; Bronze created dataset version 10 (version number 2), rule version 0, AWAITING_RULES, rules_reused=false. Questions remained unanswered; no business choices were invented.

No Silver or Gold calls were made. The reusable Rules result remains open for review. Screenshot: phase5a1-rule-reuse.png.

## Changed files

- app/db/dataset_repository.py
- app/services/business_rule_service.py
- app/services/processing_context_service.py
- app/services/processing_service.py
- app/services/rule_reuse_service.py
- migrations/0009_approved_rule_reuse.sql
- frontend/src/features/ingestion/components/DeliveryRuleBridge.tsx
- frontend/src/features/ingestion/components/RuleReviewForm.tsx
- frontend/src/services/api/rules.ts
- frontend/tests/rule-approval.cjs
- frontend/tests/phase5a1-compatible.csv
- frontend/tests/phase5a1-additive.csv
- tests/test_rule_approval_bridge.py
- tests/test_rule_approval_postgres.py
- tests/test_approved_rule_reuse.py
- docs/phase5a1-rule-reuse.md
- docs/phase5a1-rule-reuse.png

Phase 5A.1 is ready for approval within this scope. No commit, rule editing, or Phase 5B work was performed.
