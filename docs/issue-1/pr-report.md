# Issue #1 code delivery checkpoint

**Draft / overall live acceptance FAIL. Do not close Issue #1.**

## Implemented

- Connect CSV Start Process to existing asynchronous BRONZE/SILVER/GOLD admission,
  with stable rule/policy-aware idempotency keys, active-job polling, explicit
  rule pauses, failures/retries and first APPEND accepted-state publication.
- Prepare a restricted owner-fenced job metadata command in pending migration
  0068. No deployed privilege change has been applied.
- Add exact, independent, default-off Model read and Ask AI gates and truthful
  frontend release capabilities. Preserve Analytics/SQL behavior and gates.
- Preserve the existing page layouts, grouped navigation and visual styling.
  Make workspace cards clickable, shorten supporting copy, retain accessible
  loading/focus states, and identify the current workspace/dataset/subsection
  accurately in breadcrumbs. Analytics and Data Quality keep their old pages.

## Validation

| Check | Result |
| --- | --- |
| Frontend lint/build | PASS; existing bundle-size warning |
| Frontend complete suite | 42/43; one warehouse failure reproduced on baseline |
| Backend complete suite | 2,712 passed, 1,116 skipped, five warehouse failures reproduced on baseline |
| New local PostgreSQL integration | 2/2 PASS |

The integration tests use disposable local PostgreSQL databases, real processing
algorithms/worker fences, versioned in-memory objects and actual DuckDB. They
verify stage receipts/replay, current-owner metadata access, first APPEND
publication, fresh dataset Analytics, ready profile/model node, SQL count and
execution history. This is local integration evidence, not AWS/browser proof.

## Acceptance matrix

| Feature | Local | Live release |
| --- | --- | --- |
| CSV -> Start Process -> stages/rules | PASS tests | FAIL / not deployed |
| First accepted dataset publication | PASS tests | FAIL / not verified |
| Model read and owner boundary | PASS tests | FAIL / gate/ACL rollout pending |
| Ask AI disabled-state UX | PASS tests | FAIL / UI not published |
| Grounded provider-backed Ask | Unverified | FAIL / provider and DB authority unresolved |
| Analytics and SQL count/history | PASS real local execution | FAIL / revision not deployed/retested |
| Full-card navigation/shared design | PASS component checks/desktop preview | FAIL / complete responsive walkthrough pending |
| Development deployment | Not performed | FAIL / credentials and approvals needed |

## Release requirements and limitations

AWS CLI credentials are unavailable. No Terraform plan/apply, IAM change, deployed
DB migration, application publication or new live revision is claimed. Review the
pending metadata function/owner/EXECUTE grant before deploying the new API. Model
read must be explicitly approved and verified using the deployed restricted role.
Keep Ask AI off until provider costs/secrets, semantic prerequisites and bounded
immutable-answer publication authority are reviewed; do not add broad DB writes.
Keep public API dispatcher authority false. Warehouse/BigQuery work is deferred.

Stage continuation is explicit. Job metadata is capped at 200 entries without
pagination. Complete mobile/desktop accessibility and fresh-dataset AWS acceptance
remain required. Existing successful data does not substitute for acceptance.

Rollback restores the operator-recorded previous API image/frontend artifact and
disables new optional gates; retain immutable source, state and execution history.
No destructive migration or resource replacement is proposed.

Screenshots, raw logs and the full local delivery report were retained locally.
Automatic approval review rejected uploading that evidence payload to GitHub
without explicit authorization. They are deliberately excluded from this branch.
