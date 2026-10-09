# Phase 7C: workspace semantic discovery

Workspace discovery combines current dataset evidence and unapproved AI suggestions. It does
not process datasets, approve semantics, calculate relationships, run joins, invent KPIs,
or alter APPEND/UPSERT/SNAPSHOT. Dataset remains the processing boundary.

## Existing architecture and explicit scope

Reuse ownership/principal checks, current published dataset state, Phase 7A read/profile
contracts, Phase 7B current suggestion selection, transaction participation, structured provider
protocol, centralized Gemini client and compatible interactions schema adapter. No dependencies,
second provider client, raw/S3 reader, processing executor or authentication redesign is added.
The shared Gemini adapter now accepts class-level prompt/schema/input-label overrides; its
dataset defaults and configuration identity are unchanged.

## Coverage and source identity

An owned workspace transaction reads its complete ordered dataset membership. Archived datasets,
datasets without trusted state/current READY profile/current READY dataset understanding are
excluded with explicit ID/name/reason. V1 supports partial coverage from all eligible datasets;
zero eligible datasets block generation. An archived member remains an explained exclusion.
No random sampling or silent omission of eligible datasets occurs.

The private source signature covers workspace name, all dataset memberships/names/owners/statuses,
state/profile pointers and profile status, coverage/exclusions and exact eligible profile/state/
schema/suggestion IDs and versions. Model/provider/strategy and workspace algorithm version are
separately pinned. Changes to excluded membership also invalidate a previous partial result.
Returning to exactly identical sources/configuration reuses immutable prior successful output.
Changes to draft business rules do not reinterpret the approved lineage of current trusted state.

Snapshot reads use a single REPEATABLE READ transaction and borrow its connection through existing
repositories. Full reads recheck the signature before returning current output. Publication uses
a separate short READ COMMITTED transaction: lock workspace parent FOR UPDATE, then ordered
dataset heads FOR UPDATE, gather sources again and compare signature/configuration. Parent locks
conflict with membership inserts/moves through PostgreSQL foreign-key key-share locks; child
locks protect existing membership/status/current-head mutations. AI inference runs outside that
transaction. A late result after source change is failed without reasoning and cannot replace
current facts. A separate session advisory workspace lock coordinates generators; unique source/
config and workspace/version constraints provide a second idempotency boundary.

## No-value handoff and reasoning

Compact allowlisted JSON contains workspace identity/name/server coverage; eligible dataset
names/IDs/source references, trusted row count, configured load strategy/keys/event time,
current 7B domain/subdomain/entity assessments and unresolved questions, and every column's
original name/type/configured key/time flags/suggested role/confidence. It omits category values,
raw samples, numeric/time extrema, file hashes, owner keys, storage paths and secrets.

All names and upstream AI prose are untrusted JSON data, separated from the system instruction.
No tools or data retrieval are granted to the model. The output is strict bounded JSON with
domain/subdomain alternatives, business-process participation, canonical entity inventory,
exactly one controlled role per eligible dataset, confidence, warnings and questions.
Role enums are independent of load strategy. BRIDGE_CANDIDATE is merely a tentative role.
There are no edges, relationship targets, columns, cardinalities, formulas or KPI fields.
Extra fields, invented/missing/duplicate dataset IDs, invalid contributors/confidence, forced
low-confidence domain/roles and detectable unsafe/out-of-scope prose reject the whole response.
Natural-language correctness and every possible injection cannot be proved by structural checks;
all output remains AI suggested and requires review. Mixed domains are a valid uncertain result,
not an error, and confidence is an uncalibrated assessment rather than a statistical probability.

## Bounds, persistence and API

V1 permits at most 50 eligible datasets, 1000 total columns, 100 KB ASCII request JSON and 250 KB
response text. Above 200 columns (or the request budget), deterministic compaction retains all
column names/types/configuration/roles, classification labels/confidences (including alternatives),
and the first two unresolved questions. Extended rationales are omitted; omitted question count
is declared. If still too large, fail before the provider call.
The existing adapter timeout is 60 seconds, automatic retries disabled, output capped at 16000
tokens. Scoped logs record IDs/model/algorithm, total/analyzed/column counts, compact mode,
input bytes/duration and safe validation categories. No prompts/provider bodies/content are logged.

Additive migration 0020 adds only workspace_semantic_suggestions: immutable success history,
workspace/owner/version/configuration/source signatures, public source pins/server coverage,
provider/request/timestamps and validated reasoning. Trigger validates owned current eligible
source pins on insertion/publication and prevents changing pins/deleting history/altering READY.
Failures retry the same reserved header/version. No semantic approval or dataset pointer is added.
Lifecycle NOT_GENERATED/STALE/GENERATING/READY/FAILED is derived separately from profile/analytics.
An interrupted generator without its session lock reads FAILED/INTERRUPTED and is explicitly retryable.

GET /workspaces/{workspace}/semantic-understanding returns readiness/current safe output;
GET /readiness withholds suggestion content; POST /generate accepts an empty strict body only.
Server resolves membership and source evidence; client cannot supply IDs, prompts, data or models.
History is retained but no history browser is added. Provider failures leave datasets unchanged.

## Frontend and future handoff

Workspace Understanding tab shows explicit Generate/Refresh/Retry, current coverage/excluded
dataset review links, AI suggested/Review required, domain alternatives, processes, canonical
entities, named contributions and roles, warnings/questions/low-confidence state. Stale output
is hidden. Responsive cards/navigation wrap rather than expose a wide table/graph. Generation
and polling are scoped/abortable; readiness requests do not overlap; navigation resets content.
No optimistic READY, progress percentage, relationship editor or KPI control is shown.

WorkspaceSemanticEvidenceBundle exposes current typed deterministic profiles, current dataset
suggestions, current workspace suggestion (including entity inventory/roles) and coverage to a
future service. It checks freshness before handoff. It does not label AI semantics approved or
calculate overlap/cardinality/relationships. Phase 7D has not been started.

## Limits and validation

Synchronous V1 needs a live coordinator connection; no worker queue, cancellation of a provider
request already in flight, streaming, token/currency metering or automatic refresh. Readiness
currently gathers profiles and suggestions internally; one transaction avoids connection churn
but large workspaces incur multiple metadata queries. Request bounds prevent unbounded inference,
not a full workload benchmark. Historical suggestions have no browsing/approval UX.

Validation and explicitly approved development changes are recorded in phase7c-live-results.json
and the final report. No commit. Pre-existing untracked tools/screenshots are preserved.

The initial live algorithm 1 result included a foreign-key review question, without any edge
field. Self-review tightened singular/plural/prose boundary checks and the prompt. Algorithm 2
with its own configuration identity supersedes that immutable history; current output contains
only business-scope/units questions. Invalid responses fail safely and explicit retries reuse
the reserved header. This illustrates why review-only AI output is never authoritative.

The existing dataset-processing concurrency regression imposed a 10-second deadline before
entering the held Silver stage. It failed both in the full run and separately on this host.
The test now allows 60 seconds for coordination and releases its hold in finally even when
setup times out; overlap/refusal/output-count assertions are unchanged. Isolated rerun passes.
No processing implementation or lock semantics were changed.

## Live development verification

With explicit approval, migration 0020 was applied to ai_data_workspace/public after verifying
the table was absent. It added only workspace_semantic_suggestions and its history protection
function/trigger; existing dataset/state/profile/suggestion counts were unchanged at apply.
Post-apply verification confirms the trigger and constraints. No manual pgAdmin change is needed.

Dedicated workspace 16 contains Customers 25, Products 26, Orders 27 and Payments 28.
Approved synthetic identifier/date/amount rules were used; order_id and payment_id uniqueness
were saved only after separate explicit approvals. Four current sources produced a commerce
suggestion with separate Customer/Product/Order/Payment entities and sensible dataset roles.
Repeated generation reused the same suggestion. Adding dataset 29 without 7B understanding
made the result stale, then generation explicitly covered 4/5 and named the exclusion.
An additional Orders delivery changed state/profile pins and hid the old workspace result.

After explicitly refreshing Orders evidence, Patient Admissions 30 added a different domain.
Suggestion 4 analyzes 5/6, keeps dataset 29 excluded, leaves primary domain/subdomain null at
0.4 confidence, and presents Commerce/Healthcare alternatives plus scope questions. The
provider's overall confidence is separate from its domain confidence. No output is approved.
An adversarial workspace/dataset-name handoff passed strict live validation with five roles
and no relationship or KPI fields. Stored names were not changed by that test.

Read-only verification found zero cross-workspace source ownership mismatches and validated
the typed future handoff. Workspace 15's current source IDs are disjoint. Browser switching
to workspace 15 showed its own not-generated state without workspace 16's output. Returning
and reloading workspace 16 restored its persisted version. No generation was run for 15.

Desktop, 390px mobile and 768px tablet checks passed without horizontal overflow. Saved proof
includes ready, mobile, partial, stale and mixed-domain screenshots. No user server was restarted.

Final focused database-enabled Phase 7C suite: 29 passed (433.90s). Frontend lint and production
build pass; all 14 lightweight suites pass. git diff --check and whitespace checks of all 14
new text files pass. Full backend regression results are recorded in the final report.

The DB-enabled full backend run completed with 315 passed and one failed in 3799.98s.
Its only failure was the original 10-second concurrency setup test, loaded before the
timing repair. The repaired isolated test passed, and the final Phase 7C suite's 29 cases
passed separately after the final validation changes. One existing Starlette/httpx
TestClient deprecation warning remains; no new dependency was added for that warning.
Final full-run failed-test rerun (`RUN_DB_TESTS=1 uv run --no-sync pytest --lf -q`):
1 passed in 29.08s. No unresolved test failures remain from the completed regression run.

## Changed files

Modified: app/ai/semantic_provider.py, app/main.py, frontend/src/App.tsx,
frontend/src/pages/Workspace/WorkspaceDetailPage.tsx, tests/test_dataset_pending_processing.py.

Added backend: app/ai/workspace_semantics.py, app/api/workspace_understanding.py,
app/db/workspace_semantic_repository.py, app/schemas/workspace_semantics.py,
app/services/workspace_understanding_service.py, migrations/0020_workspace_semantic_suggestions.sql.

Added frontend: frontend/src/services/api/workspaceUnderstanding.ts,
frontend/src/features/workspaces/understanding/WorkspaceUnderstanding.tsx,
frontend/src/features/workspaces/understanding/WorkspaceEvidence.tsx.

Added tests/evidence: tests/test_workspace_understanding.py,
frontend/tests/workspace-understanding.cjs, tools/phase7c_live_check.py,
docs/phase7c-workspace-discovery.md, docs/phase7c-live-results.json,
and screenshots/phase7c/ proof images.
