# Phase 7B: dataset AI understanding suggestions

Dataset is the reasoning boundary. Workspace scopes ownership and future collection only.
Suggestions never approve semantics, keys, event time, rules, sensitivity, relationships or KPIs.
Approved Phase 6A–6E processing and Phase 7A deterministic evidence remain unchanged.

## Reuse and input

Reuse the centralized Gemini client/configuration and the interactions structured-output API
already used by Ask Your Data. The new provider-independent protocol returns text plus optional
resolved model metadata. Gemini-specific response types stay inside the adapter. No second
credential configuration/client stack or new dependency is introduced. The client accepts optional
timeout/retry controls; existing callers retain their original behavior.

Only a current READY Phase 7A profile may be analyzed. Backend ownership validation happens
before evidence/configuration reads. Profile/state pins are checked again under a dataset row
lock before reserving a generation. No raw frame, CSV, source row, S3 artifact or hash is read
by this service. Dataset name, column names/tokens, physical type, missingness/distinctness,
configured policy facts, aggregate statistics and bounded pattern/sensitivity hints are sent
to the configured Gemini provider. Names themselves may contain business-sensitive metadata;
the UI explains this transmission. Enterprise/private-provider controls remain future work.

## No-value handoff and prompt safety

Default and V1 policy is **NO_RAW_VALUES**. All categorical labels remain redacted, even if
an upstream category object accidentally contains a value. Only rank/count/percentage is copied.
Sensitive hints additionally suppress numeric/time statistics in the handoff. There are no raw
examples, text extrema, identifier values, storage paths, source checksums or owner keys.

The versioned system instruction is separate from JSON user input. Every dataset/column name
is untrusted data, never an instruction. The prompt forbids relationships, KPIs, key/policy
changes, unsupported business facts/currencies/units, definitive PII and invented columns.
No tools, retrieval or raw-data access is configured.

Gemini's interactions transport rejected the full constrained Pydantic JSON Schema in live
testing. The adapter therefore inlines references and supplies a compatible structural schema.
The complete extra-forbidden, bounded Pydantic schema and contextual checks are still applied
deterministically before persistence. No malformed or partial response is silently accepted.

## Output and validation

Domain, subdomain and entity have a primary candidate, confidence/rationale and at most three
alternatives. A primary label below 0.5 confidence must be null; this is a successful uncertain
outcome. Confidence is an uncalibrated model assessment, displayed as qualitative bands.
Column roles use a controlled enum. Each supplied original column must appear exactly once.
Configured keys require IDENTIFIER; configured event time requires TIME_DIMENSION.
For a column configured as both key and event time, IDENTIFIER is primary and TIME_DIMENSION
is a required secondary hint. Numeric
ID/code/year/postal names or identifier evidence cannot be classified as measures. Type checks
also apply to secondary hints. Impossible Boolean/time/measure roles are rejected.

Unexpected fields (including relationships, KPIs, IDs or key changes), invalid confidences,
missing/invented/repeated columns, unsafe path/contact/secret-like prose and detectable certainty
claims are rejected in their entirety. These checks cannot prove every natural-language meaning
true or comprehensively defeat every possible prompt injection. Suggestions remain review-only.

## Persistence, pins and recovery

Additive migration 0019 adds only `semantic_suggestions`: owned workspace/dataset, source READY
profile/state/version, numbered suggestion version, semantic prompt/algorithm version, private
configuration hash, requested/resolved provider/model metadata, lifecycle/timestamps, bounded
request metadata and validated JSON suggestion. It is separate from future approved semantics.
There is no backfill or modification to prior migrations, dataset state, analytics or policies.
Successful records cannot be updated/deleted. Ownership/current-profile pins are validated by
insert trigger; publication rechecks the current profile/state under the dataset row lock.

Unique dataset/profile/algorithm/configuration identity reuses successful results. Failures retry
the same record/version. Changing the source profile, semantic version or model configuration
creates a new immutable version. No explicit force-regenerate control is included in V1.
Configuration pins provider, model and adapter strategy as well as semantic version. A floating
provider alias cannot guarantee provider-internal weights are identical; resolved model metadata
is recorded when supplied by the provider.

A separate dataset-scoped PostgreSQL session advisory lock coordinates generation. Readiness
derives NOT_GENERATED/STALE/GENERATING/READY/FAILED independently of profile status. If a generator
dies and its lock is absent, reads expose retryable INTERRUPTED without modifying history.
Profile/state/configuration changes hide historical suggestions; reads recheck current profile
after loading output. New trusted data may publish during inference. If the source changes during
inference, that response is rejected and its failed attempt retained without output. Previously
successful suggestions remain immutable history, never presented as current or installed as
trusted metadata.

Provider timeout, rate limit, refusal, unavailable model/server, malformed JSON, contextual
validation and publication failures persist safe failure categories. No raw exception, response,
prompt or secret is logged. Retrying affects only AI understanding. Timeout is 60 seconds per
provider request, transport retries disabled, one configured model, explicit user retry.
No fallback model is silently substituted or charged; model configuration changes create new pins.

## Bounds and public API

At most 40 columns receive full allowlisted evidence. Above 40, every column name/type and key
facts remain, richer context is limited deterministically to keys/event time/sensitivity.
If the serialized ASCII JSON exceeds 60,000 bytes, rich statistics are removed from all columns.
At more than 200 columns, names longer than 256 characters, or a still-over-budget request,
generation is refused before a provider call. No random column dropping. Responses are bounded
to 200 columns/250 KB and short prose, provider output to 16,000 tokens. Request bytes/column
count/compact mode are tracked; bytes are a proxy, not exact tokenizer usage. Large output may
truncate and fail safely. No streaming/queue/token-calibration benchmark is claimed.

`GET /workspaces/{workspace}/datasets/{dataset}/semantic-understanding` returns safe current
readiness/output and small deterministic evidence. `GET .../readiness` is a content-free status
read for polling, and frontend polling never overlaps its own requests. `POST .../generate` accepts an empty request
only; callers cannot select profile/state, prompt, raw evidence, credentials or model. Reuse
existing development principal/workspace/dataset access checks. Public model contracts omit
owner keys, configuration digest, provider keys and storage paths. Logging records only scoped
IDs, versions, model, input size/column count, compact mode, duration and safe status/category.

## Frontend

Dataset AI Understanding tab uses scoped abortable reads and reset-on-navigation content.
Stale profile links to Data Profile. Analysis is explicit, busy/duplicate clicks disabled,
readiness polled without fake percentages. Failed generation has a safe retry. Current suggestions
show domain/subdomain/entity alternatives, qualitative confidence, warnings/questions and column
roles/meanings. Physical evidence has a distinct neutral section; AI suggestions visibly require
review. Potential sensitivity remains a hint. No raw example, approval control, key promotion,
relationship target or KPI is shown. Navigation/cards wrap for mobile. Failed reads hide cached
suggestions. Historical suggestions remain persisted but have no history browsing UI.

## Future compatibility and limits

Owned READY suggestions have a workspace index for future current-profile gathering. No workspace
reasoning, relationships, KPI generation, orchestration, approval/versioning or Phase 7C is added.
V1 is synchronous, holds a live coordinator connection, and has no durable worker queue or
automatic regeneration. Provider cost is reduced through idempotency/bounds but not metered in
currency. The existing temporary development identity is unchanged.

## Validation and live evidence

The initial focused database-enabled suite passed 33 cases; the expanded suite passed 34 in
498.28 seconds. Final pure/provider checks passed 33 cases, covering all non-database cases in
the finalized 40-case file. The final readiness/transaction recovery case passed separately;
the final stale-source concurrency plus recovery checks passed two cases in 127.99 seconds.
Together these cover the finalized 40 cases (33 pure/provider plus seven database lifecycle
cases). Full database-enabled regression (`RUN_DB_TESTS=1 uv run --no-sync pytest -q`) passed
287 tests in 2922.48 seconds (48:42). That run collected before seven additional Phase 7B cases
and final refinements; the focused checks above cover the finalized 40 cases and final source
publication/readiness/provider/role behavior. Only the existing Starlette/httpx TestClient
deprecation warning remains. The sandbox could not persist uv's interpreter cache; uv ran
successfully outside the sandbox with approval, using disposable PostgreSQL schemas.

Frontend lint and production build pass. All 13 lightweight frontend suites passed after the
new suite stopped importing another suite's asynchronous fetch mocks. This was a test-harness
collision, not an application isolation failure. Final Git diff and all new-text whitespace
checks pass. No dependencies or test framework were added.

The user explicitly authorized migration 0019 and provider transmission for development
`ai_data_workspace`, workspace 15, Orders dataset 23 and Healthcare dataset 24, plus a synthetic
Orders delivery using previously approved rules. Before migration, verified database/public
schema and table absence. Added one table/index and two protection triggers; existing counts
were unchanged: 20 datasets, 33 state versions, four deterministic profiles. No earlier migration
or existing business rules/policies/contracts were altered. No manual pgAdmin change is needed.

Live Gemini returned Commerce / Order for Orders, with IDENTIFIER, ENTITY_REFERENCE,
TIME_DIMENSION, MEASURE and STATUS roles, and Healthcare / Patient Admission with uncertainty
about currency and record meaning. The Healthcare browser action showed disabled Analyzing
then READY, sensitivity review hints and no raw examples. Repeated generation reused successful
IDs. The adversarial metadata-only request preserved all original columns, with no relationship
or KPI output. The first transport attempt failed safely due to schema incompatibility; that
failed header remains for audit, and the working interactions schema adapter is documented above.

Approved upload 79 added one synthetic Orders record. State 4 made its profile and suggestion
stale, hid output in the browser and blocked generation. Browser Refresh Profile built profile
5 (six current rows); Analyze Dataset created suggestion 4 / dataset suggestion version 3,
pinned to profile 5 / state 4. Earlier successful suggestion 2 remains immutable history.
Healthcare suggestion 3 remains pinned to profile 2 / state 1. Reload and dataset/workspace
switching retained scope correctly; an older workspace was only read, never analyzed/changed.
Cross-workspace API context returned 403. Read-only final integrity found zero invalid ownership
or source pins, three READY synthetic suggestions and one historical FAILED transport attempt.

Mobile 390px: scroll width 375px. Tablet 768px: scroll width 753px. No horizontal overflow;
viewport override reset. Screenshots were visually reviewed. Safe milestones are recorded in
`phase7b-live-results.json`; screens are under `screenshots/phase7b/`. The helper never approves
new rules, and live changes were restricted to the approved synthetic scope.

## Changed files

Modified: `app/ai/provider.py`, `app/main.py`, `frontend/src/App.tsx`,
`frontend/src/pages/Dataset/DatasetDetailPage.tsx`.

New backend/domain: `app/ai/semantic_handoff.py`, `app/ai/semantic_provider.py`,
`app/ai/semantic_validator.py`, `app/api/dataset_understanding.py`,
`app/db/semantic_suggestion_repository.py`, `app/schemas/semantic_suggestion.py`,
`app/services/dataset_understanding_service.py`, `migrations/0019_semantic_suggestions.sql`.

New frontend: `frontend/src/services/api/understanding.ts`,
`frontend/src/features/datasets/understanding/DatasetUnderstanding.tsx`,
`frontend/src/features/datasets/understanding/UnderstandingEvidence.tsx`,
`frontend/src/pages/Dataset/Understanding/DatasetUnderstandingPage.tsx`.

New tests/evidence: `tests/test_dataset_understanding.py`, `frontend/tests/understanding.cjs`,
`tools/phase7b_live_check.py`, `docs/phase7b-ai-understanding.md`, `docs/phase7b-live-results.json`,
and `screenshots/phase7b/orders-ready.png`, `healthcare-ready.png`, `understanding-mobile.png`,
`understanding-stale.png`. Pre-existing untracked tools/screenshots were preserved.

Phase 7B is ready for approval within the documented V1 limits. No commit, trusted semantic
approval, workspace discovery, relationship inference, KPI generation or Phase 7C work.
