# Phase 11E — Warehouse reliability protocol

Release status (2026-10-22): migration 0031 has been applied to the development
database under the separately approved release gate. Section 29 supersedes the
historical migration/release status in the implementation review below.

Implementation review on 2026-10-21, based on Phase 11D commit
`e8a658cb7cc113f2a13c1b20ef7d2c48b51b8d09`. This is opt-in internal metadata
and bounded reference execution. No startup hook, public route, worker, provider
client or consumer switch is installed. `WAREHOUSE_ENABLED=false` remains unchanged.
Migration 0031 is for disposable-schema testing and review only, not development
`ai_data_workspace.public`. No staging, commit or push is part of this phase.

## 1. Repository preservation and files

The 240 pre-existing unrelated porcelain entries and existing file hashes were
captured in ignored `tmp/phase11e-files-before.json` before implementation.
Historical deleted tests, reports, screenshots and tooling are excluded.

Eight files are new:

- `app/warehouse/accepted_changes.py`: versioned projected contracts and identities.
- `app/warehouse/source_evidence.py`: bounded verification/extraction and bootstrap classification.
- `app/warehouse/reliability.py`: receipts, reconciliation, recovery and reference target.
- `app/db/warehouse_reliability_repository.py`: transaction-owned SQL primitives.
- `app/services/warehouse_reliability_service.py`: authorized durable protocol operations.
- `migrations/0031_warehouse_reliability.sql`: additive metadata and invariants.
- `tests/test_warehouse_reliability.py`: pure and disposable PostgreSQL tests.
- This document.

One existing test, `tests/test_warehouse_foundation.py`, narrowly permits the three
existing pure source-engine imports in the explicit 11E read-only extraction adapter.
Its cloud/storage/route prohibitions remain; additional assertions verify existing
source/Gold services still import no warehouse layer. All existing production
implementation files remain unchanged. Total scope: nine paths.

## 2. Actual source publication boundary

`append_application_service.apply_incremental` prepares and pins successful Silver
bytes, reads the current trusted state, invokes the existing APPEND/UPSERT/SNAPSHOT
engine, writes immutable candidate data/outcomes/manifest objects, registers a
candidate state, and verifies it. A candidate alone is not accepted evidence.

Its final `repository_transaction` sets the candidate VALIDATED, records application
counts, and calls `incremental_application_service._publish_metadata`. That helper
locks the application/dataset, compares `previous_state_id` against the current
head, marks the state PUBLISHED, moves `datasets.current_state_id`, and marks the
application SUCCESS with `result_state_id`. The surrounding transaction also marks
state analytics STALE. **Successful commit of this transaction** is the acceptance
boundary. A response lost after commit remains a successful source application.

`processing_attempts` and `data_quality_runs` gate preparation; delivery manifests
pin successful input bytes. Neither processing start, Silver success nor delivery
manifest READY_TO_APPLY is warehouse-dispatch authority. The warehouse checks
PUBLISHED plus SUCCESS plus the exact result-state/application linkage.

## 3. Source ordering

Existing `state_version` can contain holes from abandoned candidates. Application
IDs, ingestion timestamps and file IDs are not a contiguous accepted stream.
Discovery recursively follows the published predecessor chain from the dataset's
current head. It excludes candidates and failed applications. Registration takes
the model/source mapping lock and allocates the next position only when the source
state's predecessor is the last registered publication. Its ordering domain is the
immutable source mapping/model version, anchored at the source-chain root. Published
states numbered 2, 4 and 6 legitimately become positions 1, 2 and 3.

Changing a source contract requires a reviewed new model/version/bootstrap decision;
the service does not skip incompatible predecessor evidence to start in the middle.

## 4. Accepted-change contract and stable event identity

V1 `SourcePin` identifies workspace, dataset/schema version, policy, approved rule
version/hash, application, logical upload, DQ run, state/predecessor, immutable state
manifest checksum, load strategy, snapshot authority/effective time/kind/outcome
and accepted audit timestamp. No physical artifact path is persisted in this contract.

Event ID is SHA-256 of the compact typed array:

`["datarise-accepted-event",1,workspace,dataset,application,state,evidence_key,outcome]`.

UPSERT/SNAPSHOT reuse the existing immutable ledger business-key hash as evidence
locator; deactivation uses its separately tagged key. APPEND has no accepted ledger:
the existing engine's verified candidate suffix proves inserts. `state-record:N`
locates a record in **already pinned immutable source bytes**. It is never a
warehouse ROW_NUMBER, an arrival-order allocator or a surrogate key. A changed
artifact or row order cannot be relabeled the same accepted publication because its
registered immutable source checksum must still verify. Retry timestamps, workers,
attempt numbers and generated process UUIDs do not participate in logical identity.

The same ID with different projected material produces a checksum conflict; a
second registration verifies bytes again and compares the complete existing body.
Model projections have separate manifest scopes while logical event identity is
independent of the worker and projection order.

## 5. Projected payload and checksum

Only approved roles are selected. DIMENSION uses business keys, tracked attributes,
nonhistorical attributes and effective time. Existing V1 FACT defines only grain
identity and optional event time; no measure/relationship roles are invented.
Extending FACT roles requires a later reviewed model-contract version.

Payload is sorted `[field_name, canonical_typed_scalar]` pairs. Existing 11B
`canonical_value` encodes exact integer/Decimal/string/boolean/NULL/DATE/aware UTC
timestamp values. Floats, nested arbitrary values and naive timestamps fail closed;
there is no float-to-Decimal conversion. Decimal scale normalization preserves
value, timestamps preserve instants, and strings preserve code points.

Payload checksum is SHA-256 of
`["datarise-accepted-payload",1,outcome,projected_pairs]`, compact UTF-8 JSON with
`allow_nan=false`. Dictionary insertion order does not define the wire format.
Metadata mappings recursively sort string keys before using the same SHA-256
helper, including configuration, source-pin and intent objects. Reference execution
recomputes event IDs/payload checksums and the complete manifest checksum before
accepting target input; a token retry with damaged material cannot reuse old proof.
Maximum 128 fields and 64 KiB encoded projected data per event. The whole V1
extraction is deliberately bounded: 32 MiB per artifact read, 1,000 rows per decoded
frame, 1,000 events per manifest and 1,000 manifests per operation. Inputs over these
bounds fail before registration; no partial prefix is accepted. This is not a
chunked ingestion implementation. Parquet reads are byte-bounded but not a general
defense against arbitrarily compressed hostile Parquet; trusted artifacts are used.

## 6. Manifest and zero-change semantics

One manifest represents one verified accepted publication for one pinned mapping.
It stores stable ID, positive contiguous position, predecessor manifest, exact
configuration hash, source pin, sorted individually identifiable events, event count,
material count and a SHA-256 covering the entire versioned ordered body.
Relational events mirror the manifest for individual lookup/constraint checking.
Event-ID ordering canonicalizes the manifest; it does not claim a new business-time
ordering between events. Authoritative progress is the accepted publication chain;
future SCD planners must still apply declared effective-time/conflict semantics.

UNCHANGED observations remain events with `material_count=0`; they never force SCD
updates. A publication with no accepted events has `event_count=0` and still receives
a manifest/outbox item. Successful reconciliation/publication of that evidence
advances normal progress. Empty APPEND and all-rejected keyed input are tested;
UPSERT unchanged observations retain their exact source evidence.

## 7. APPEND mapping

Re-run existing `append_rows` against immutable delivery input and pinned previous
state, compare candidate rows/counts/outcomes, and emit only verified accepted suffix
inserts. Exact duplicates and rejected conflicts create no material warehouse event.
The source outcome artifact only records rejects and aggregate duplicate counts;
11E does not claim detailed unchanged APPEND observation history.

## 8. UPSERT mapping

Re-run existing `upsert_rows`, verify the full immutable candidate and ledger, and
emit INSERTED, UPDATED and UNCHANGED using the existing ledger key identity. DUPLICATE,
STALE and REJECTED rows are excluded. The actual source names are STALE/REJECTED,
not assumed CDC enum names. Incoming projected values supply accepted unchanged
observation evidence; source stale/conflict decisions cannot be overridden.

## 9. SNAPSHOT mapping

Re-run existing `snapshot_rows` with the exact previous snapshot boundary and pinned
coverage/effective time/kind. Emit INSERTED, UPDATED, UNCHANGED and REACTIVATED from
the verified ledger. Source deactivation is not a separate ledger row: verified
candidate inactive rows whose deactivation application equals this application
supply explicit DEACTIVATED evidence. The source engine already verifies COMPLETE
authority and matching transition counts. PARTIAL absence never deactivates anything.
Snapshot effective time/coverage remain explicit in the source pin; old business
row time is not misrepresented as a deactivation boundary. This phase preserves
these outcomes without adding deactivation behavior to 11C/11D.

## 10. Outbox atomicity and discovery recovery

Manifest + all accepted events + outbox intent are inserted in **one PostgreSQL
transaction** after verification. They are immutable and idempotent per mapping/state.
Normal dispatch has a scoped deterministic dispatch key independent of a caller's
rediscovery request key; replay/backfill include explicit request identity.

**Source publication and projected registration are separate commits.** There is no
dual-write atomicity claim. Projection requires approved model-specific roles and
bounded reads/verification of several immutable artifacts, potentially for several
models. Doing that I/O inside the existing source publication transaction would
couple source availability to optional warehouse work. No source hook is installed.

Explicit `discover()` computes verified successful published chain MINUS registered
manifests. A worker/service owner must call it and register candidates in chain order.
A crash after source success or during registration leaves the candidate discoverable;
registration failure rolls back manifests/events/outbox together. There is no
automatic scheduler, startup scan or historical enqueue. Operator opt-in discovery
is the liveness requirement; missing artifacts remain blocked, not silently skipped.
Explicit `pending(resource)` pages registered outbox manifests beyond that resource's
normal checkpoint. This recovers a crash after registration but before operation
creation; only reconciled published contiguous progress acknowledges consumption.

## 11. Historical bootstrap

Classification is explicit: verified complete chain + artifacts permits exact
accepted-evidence reconstruction using the existing algorithms; only verified
current state requires an explicit CURRENT_STATE_SEED intent/design; insufficient
evidence requires an explicit rebuild/backfill decision. The classifier does not
claim unavailable historical CDC. V1 executes the exact-chain category only.
Current-only seed execution is deliberately blocked/deferred rather than inventing
predecessors, observations or SCD history. Migration does not scan source rows.

## 12. Observation coverage

UPSERT/SNAPSHOT UNCHANGED evidence is retained with its publication, key, projected
typed values and effective time where declared. This improves future coverage
proof. It does not by itself prove every intervening business observation or enable
11D's deferred current-interval repairs. 11C/11D files and algorithms are unchanged.

## 13. Operations, source/model/resource pins

Reuse 11A `warehouse_operations` and `warehouse_runs`, with one immutable execution
intent per operation. Intent pins scope/model version, resource/generation, ordered
manifest IDs/checksums/positions, configuration hash, expected logical result,
prior publication head/revision, prior checkpoint position/version and explicit mode.
Nothing resolves a latest file, policy, source state or model configuration at retry.
Source extraction uses the exact application policy/rules and detects missing/drifted
pins. A newer current source policy does not reinterpret old evidence. Archived or
unavailable model/resource pins fail closed; they are never upgraded automatically.

## 14. Retry, replay, backfill and rebuild

Retry claims another run for the same immutable intent/token after a failed or
expired attempt. A completed published operation cannot be retried. Known target
failure terminates the attempt; retry creates a new attempt. A mere missing receipt
after PLANNED requires token lookup before any further apply.

REPLAY is a separate operation over already consumed immutable evidence, normally
bounded by the existing checkpoint. It preserves deterministic result/key semantics
and advances publication revision without advancing normal source position.

BACKFILL is a separate explicit approved source-publication range `[first,last]` in
this mapping's ordering domain, with exact evidence and checksums. Business-time
filtering is not guessed. Bounds must match selected contiguous manifests. Backfill
may publish separate provenance, but cannot change normal source position.

REBUILD requires positions starting at one and an unconsumed target generation.
It records a deterministic reconstruction intent; it does not provision or rebuild
physical tables. INITIAL_LOAD similarly starts at the anchored root. INCREMENTAL
must start at the next unconsumed position. Gaps block all normal advancement.

## 15. Checkpoints and contiguous progress

Checkpoint primary key is resource generation binding + source mapping; workspace,
model/version and dataset scope follow validated parent keys. It stores latest
contiguous consumed position, publication reference and monotonic CAS version.
No MAX(updated_at) or source-state candidate number is used. Range 1,2,4 cannot
be planned as a contiguous operation; missing 3 blocks 4. Processing a side backfill
does not leap the normal head. Different model/source/resource scopes never share
progress. The Python service validates expected prior values and SQL updates use
those exact values with checked affected-row counts.

## 16. Writer lease and fencing

One durable lease per resource serializes normal/replay/backfill writers. Claims
lock the model/source scope, expire the abandoned open run, create the next existing
11A attempt and increment a monotonic fencing generation. Leases use database time;
there are no sleeps, process locks or wall-clock source ordering. Every plan,
receipt, reconciliation, manual block and publication checks current run/fence and
unexpired lease. A resumed old worker cannot finalize. No renewal API is included;
bounded workers must finish within a lease or reclaim with a fresh fence.

Expiry permits reclaim, but proves nothing about external target effects. It never
converts an uncertain execution into a known failure.
A different intent cannot bypass prior PLANNED, UNKNOWN or applied-but-unpublished
work on the same resource by requesting a new token. The original intent must first
be recovered/finalized. MANUAL_REVIEW blocks other intents on that resource too;
an explicitly approved separate generation remains a distinct rebuild boundary.
Planning also rechecks pinned head/checkpoint preconditions before target admission;
publication independently repeats the CAS checks against stale external candidates.

## 17. Durable run stages and unknown outcomes

Append-only journal stages are CLAIMED, PLANNED, TARGET_RECEIPT, RECONCILED,
PUBLISHED and BLOCKED. Receipts distinguish SUCCEEDED, FAILED, UNKNOWN and an
explicit lookup-proven NOT_APPLIED. Stable typed decisions include RETRY_SAFE,
RECONCILE_FIRST, ALREADY_APPLIED, FENCED, SOURCE_GAP, SOURCE_DRIFT, MODEL_DRIFT,
RESOURCE_DRIFT and MANUAL_REVIEW.

After PLANNED without authoritative response, first look up the deterministic
application token. Proven applied work can be reconciled under the new fence;
proven not-applied work may be planned/applied; unknowable work is durably
MANUAL_REVIEW with TARGET_UNCERTAIN, blocking automatic claims. Resolution of manual
review requires a future explicit reviewed operational action; no unsafe force flag
is present. Caller-provided receipts are trusted internal target-adapter attestations,
not client-authenticated proof. There is no public receipt endpoint.

## 18. Target receipt and reference executor

Receipt V1 pins operation/run, application token, intent hash, resource generation,
outcome and optional bounded logical result/opaque reference. It has no Google
project, SQL or provider-job fields. ReferenceTarget retains deterministic token
results, returns the same result after response loss/reapplication, rejects a reused
token with different evidence and supports a pure SCD/fact transition callback.
Its default bounded ledger checksum is reference evidence, not a physical warehouse
state checksum. Tests also execute the actual 11C SCD2 planner/application and prove
replay preserves version/member keys and history.

## 19. Reconciliation

Expected logical Result pins manifest/event counts, inserted/updated/unchanged/
reactivated/deactivated/rejected counts and deterministic result checksum. A trusted
planner supplies the expected result independently of the target response; the
intent freezes it before execution. For the default reference target these are
accepted-outcome ledger counts. A real loader must supply plan-derived physical
business-effect counts/checksums, not blindly equate source UPDATED with an SCD insert.

Publication requires target SUCCEEDED plus exact Result equality and exact
operation/token/intent/resource pins. Mismatch writes immutable failed reconciliation
and BLOCKED journal evidence without moving any publication/checkpoint. Provider
success alone is insufficient. A later receipt or reconciliation supersedes earlier
proof; an old successful reconciliation cannot publish after newer uncertainty.

## 20. Immutable revisions, current pointer and source vector

Each publication is an append-only revision bound to one successful reconciliation,
operation/run/resource and previous publication. Mutable head contains the current
revision ID/number. Historical revisions are neither overwritten nor deleted.
Source vector records mapping/dataset, normal contiguous position, manifest/source
pins at that position, exact processed manifest set, mode and backfill boundaries.
Thus a replay/backfill revision explicitly distinguishes processed evidence from
normal stream progress.

V1 accepts **one fully modeled PRIMARY mapping**. Existing 11B has no typed
dependency projection contract, so additional mappings fail closed. This avoids
publishing independently selected dimension/fact latest heads as an allegedly
consistent vector. Cross-model atomic publication groups and reader APIs are deferred;
future consumers must pin one complete publication/group vector, never combine heads.
No current analytics consumer reads this metadata.

## 21. Publication/checkpoint atomicity and recovery matrix

One PostgreSQL transaction validates current fenced reconciliation, compares head
and checkpoint preconditions, inserts revision, CAS-updates head and checkpoint,
journals PUBLISHED and finalizes the run SUCCEEDED. A failure at any point rolls
everything in that transaction back. Deferred SQL constraint triggers require
checkpoint positions/publication references to match the final current vector and
require its successful finalized run. Publication cannot get behind its checkpoint.

| Failure boundary | Durable evidence and recovery |
|---|---|
| Source commit before registration | Published source remains discoverable. |
| Registration interrupted | No partial manifest/events/outbox commit; discover again. |
| Outbox before claim | Immutable intent is available for explicit dispatch. |
| Claim before plan | Expire/reclaim; next fenced attempt can plan. |
| Plan before target response | Token lookup required; no blind reapply. |
| Target known failed | Failed attempt; same intent, new run. |
| Target applied, response lost | Reference/adapter token evidence proves applied result. |
| Receipt before reconciliation | Recover receipt and reconcile with current fence. |
| Reconciled before publication | Reconcile/publish under the new fence; old worker fenced. |
| Revision/head/checkpoint transaction interrupted | Entire finalization rolls back. |
| Head/checkpoint CAS race | Losing candidate remains unpublished; re-read and create a separately reviewed new intent. |
| Lease lost/unknown outcome | Old fence rejected; explicit token lookup/manual review. |
| Publication committed, response lost | Existing immutable publication identifies completion; no new retry attempt. |

PostgreSQL cannot atomically commit with a future external target. Stable tokens,
immutable evidence, target lookup, reconciliation and fenced PostgreSQL finalization
are the protocol for exactly-once **logical effects**, not exactly-once network
execution. 11F must make token/effect evidence durable in its target and isolate
candidate effects from published consumer revisions; an unverified mutable shared
target cannot satisfy this contract merely by returning these receipt fields.

## 22. Migration objects and invariant ownership

Actual next root migration is **0031**; 0029/0030 remain unchanged. Ten new tables:
accepted manifests/events, outbox, execution intents, writer leases, run journal,
reconciliations, publications, publication heads and checkpoints. Scoped FKs reuse
existing model/version/resource/source/application/run lineage. Unique constraints
protect stream positions, publication/event IDs, intent dispatch and revisions.
New insertion guards require published source linkage, scoped intents, mirrored
events, complete outbox event sets and successful reconciliation evidence. History
is UPDATE/DELETE protected; all ten tables reject ordinary TRUNCATE. Mutable lease,
head and checkpoint guards protect identity and monotonic transitions.

SQL constraints backstop durable relational invariants. Canonical typed projection,
artifact verification, current ownership, historical source-contract validation,
explicit mode rules and compare-and-set expected values remain trusted service
responsibilities. This is not PostgreSQL RLS or an untrusted direct-SQL API.

## 23. Ownership, flag and isolation

Every service operation checks the feature flag before database access and begins
its transaction with current workspace/model/version ownership authorization.
Source ownership/active membership and resource environment/generation are checked.
Internal guessed operation/resource/source IDs are scoped; historical creators never
authorize access. Tests enable metadata only in disposable fixtures; no credentials
or real cloud resources are required. Disabled startup and current routes are unchanged.

## 24. Validation and development safety

The initial read-only fingerprint captures all 56 public development tables in
`tmp/phase11e-db-before.json`. Migration tests use generated disposable schemas,
preserving populated 0030 metadata plus every existing row/column/constraint/index/
user trigger/function definition. PostgreSQL-managed RI triggers added by new FKs
are expected. No synthetic source row or new migration is written to public.

Final measured results are recorded below after the frozen implementation is tested.
Scope checks compare the complete baseline status/hash inventory, all tracked file
hashes, new-file whitespace and targeted secret/provider patterns. Generated evidence
under ignored `tmp/` is excluded from implementation scope.

## 25. Compatibility and limits

Existing APPEND/UPSERT/SNAPSHOT, processing attempts, trusted publication, delivery
and cumulative Gold writers, analytics freshness, Metrics, Analytics, Ask and SQL
retain their existing code, pointers and consumers. Source/Gold artifacts are read
only in explicitly invoked bounded extraction; no live S3 call is made during tests.

This phase does not implement a provider, physical tables, SQL compiler, target
loader, scheduler, cloud infrastructure, consumer migration, large-source streaming,
multi-model publication groups, current-only seed executor, FACT measures/relationship
model extensions or automatic manual-review resolution. Accepted DEACTIVATED evidence
is retained; SCD lifecycle execution is unchanged. Typed source float attributes fail
closed rather than silently losing precision. No deployment or migration to public.

## 26. Review gate and recommended 11F scope

Ready for review only after focused/final regression, migration preservation,
development fingerprints, frozen hashes and scope/secret checks pass. The review
does not authorize applying 0031 to development or enabling execution.

Recommended 11F scope: after separate migration/configuration approval, implement
the first bounded BigQuery target adapter behind this protocol: explicit provisioned
resource verification, deterministic token journal/lookup, idempotent application of
pinned reviewed SCD/fact plans, plan-derived expected counts/state digests, isolated
candidate/publication serving semantics, uncertain-response recovery and provider
integration tests. Keep source semantics/Gold/current consumers unchanged; do not
combine this with Analytics/Metrics/Ask/SQL migration or deployment. No 11F work is
included in this phase.

## 27. Requested review report

| # | Item | Result / contract |
|---|---|---|
| 1 | Pre-existing dirty paths | All 240 baseline entries and their recorded file hashes are preserved; final check below. |
| 2 | Files changed | Eight new files plus the narrowly updated foundation architecture test; nine paths listed in section 1. |
| 3 | Migration | 0031; ten additive tables, indexes, five new guard functions and history/progress triggers. |
| 4 | Applied to development | No. Only disposable schemas receive 0031. |
| 5 | Source publication boundary | Commit of PUBLISHED state + dataset head + SUCCESS application/result-state metadata. |
| 6 | Accepted contract | SourcePin and V1 individually identifiable projected AcceptedEvent evidence. |
| 7 | Event identity | Versioned SHA-256 of scoped immutable application/state/evidence locator/outcome. |
| 8 | Payload/checksum | Declared roles only, typed canonical scalars, recursively canonical metadata maps, SHA-256 and strict bounds. |
| 9 | Manifest | One accepted publication/mapping, contiguous position, immutable source/model pins, sorted events and full checksum. |
| 10 | Zero changes | Empty or material-no-op manifests reconcile and advance without fake business mutations. |
| 11 | APPEND | Verified accepted candidate suffix inserts; no fabricated duplicate-observation ledger. |
| 12 | UPSERT | Verified INSERTED/UPDATED/UNCHANGED; rejects, stale rows and duplicate ledger rows excluded. |
| 13 | SNAPSHOT | Verified accepted ledger plus explicit candidate lifecycle evidence; PARTIAL absence cannot deactivate. |
| 14 | Outbox | Immutable one-per-manifest intent, deterministic scoped dispatch uniqueness, explicit pending pages. |
| 15 | Atomic registration | Manifest/events/outbox share one transaction; source publication is a separate earlier commit. |
| 16 | Discovery recovery | Published successful predecessor chain minus registrations; idempotent explicit registration in chain order. |
| 17 | Historical bootstrap | Exact verified chain can reconstruct; current-only seed needs explicit future execution; insufficient evidence blocks. |
| 18 | Observation coverage | Accepted UPSERT/SNAPSHOT UNCHANGED observations preserved; deferred 11D repairs stay deferred. |
| 19 | Checkpoint | Resource-generation/source-mapping scope, contiguous position, publication ID and CAS version. |
| 20 | Gaps | Missing predecessor/range blocks normal admission and publication; source candidate-number holes are harmless. |
| 21 | Checkpoint CAS | Exact expected prior position/version checked and updated with affected-row validation. |
| 22 | Retry | Same immutable intent/token, another existing 11A run; no retry of published completion. |
| 23 | Replay | Separate pinned-evidence intent, no normal-position advance beyond already represented evidence. |
| 24 | Backfill | Separate explicit contiguous publication-range intent/provenance, no normal-position movement. |
| 25 | Rebuild | Pinned evidence from root into an unconsumed generation; no physical provisioning. |
| 26 | Lease/fencing | Durable resource writer generation; stale writers rejected; another intent cannot bypass unresolved work/manual review. |
| 27 | Run stages | Append-only CLAIMED/PLANNED/TARGET_RECEIPT/RECONCILED/PUBLISHED/BLOCKED evidence. |
| 28 | Target receipt | Scoped operation/run/token/intent/generation, typed outcome, counts/checksum and bounded opaque reference. |
| 29 | Unknown recovery | Token lookup before reapply; proven applied/not-applied branches; unknowable outcome blocks for manual review. |
| 30 | Reconciliation | Exact expected logical counters/checksum and all target/intent pins; mismatch never publishes. |
| 31 | Publication revisions | Immutable ancestry, successful reconciliation reference and exact operation/resource binding. |
| 32 | Current pointer | Monotonic revision/head CAS after reconciliation only. |
| 33 | Source vector | Complete V1 primary mapping pin, normal progress plus explicitly processed replay/backfill provenance. |
| 34 | Checkpoint/head atomicity | Revision, head, checkpoint, journal and run finalization share one transaction; deferred DB consistency checks. |
| 35 | Crash matrix | Section 21 and deterministic durable-stage fixtures; no sleeps or provider infrastructure. |
| 36 | Ownership | Current owner authorization on every service call; scoped model/version/source/resource/operation lookups. |
| 37 | Migration preservation | Populated source, successful Gold/current pointers and existing catalog definitions preserved in disposable schemas. |
| 38 | Development safety | Read-only 56-table comparison and confirmation that 0031 is absent; final result below. |
| 39 | Focused tests | 48 new reliability cases; authoritative frozen-tree results below. |
| 40 | 11A–11D | Foundation 33, modeling 74, SCD 89, late-data 85; final results below. |
| 41 | Backend regression | Required RUN_DB_TESTS=1 uv run --no-sync pytest -q --tb=short; final results below. |
| 42 | Quality/scope | Frozen hashes, whitespace, targeted secrets, status delta and existing tracked-file hashes checked. |
| 43 | Pipeline compatibility | No existing source-processing or publication implementation changed; no automatic hook. |
| 44 | Gold compatibility | Existing writers/consumers/pointers unchanged; warehouse metadata has no consumers yet. |
| 45 | Limitations | Bounded legacy extraction, one PRIMARY, explicit discovery, deferred current-only seed/multi-model groups/FACT role extensions/provider execution. |
| 46 | Ready for review | Yes, with the explicit bounded/single-source/bootstrap/provider limits above; no migration/commit authorization inferred. |
| 47 | Recommended 11F | Section 26: separately approved bounded target adapter, durable tokens, candidate visibility, plan-derived proof, provider fencing/recovery/IAM and job budgets; keep current consumers unchanged. |

## 28. Final measured validation

| Check | Measured result |
|---|---|
| Final corrected frozen-tree backend | **375 passed, 3 known skips, 2 existing warnings**, 322.88 s; `tmp/phase11e-regression.xml`. |
| New focused reliability coverage in that run | **48 passed**; exactly +48 over the accepted 327-pass backend baseline. |
| Prior warehouse coverage in that run | **281 passed**: 33 foundation + 74 modeling + 89 SCD + 85 late-data. |
| Complete warehouse coverage | **329 passed**, including all 48 new reliability cases. |
| Targeted final recovery gate | **5 passed**, covering cross-intent uncertainty/manual-review bypass, stale plan admission, rollback and publication CAS; `tmp/phase11e-recovery-gate.xml`. |
| Frozen implementation/migration/tests | All **eight hashes match** `tmp/phase11e-freeze.json` after regression. |
| Public development data | All **56 table counts/fingerprints unchanged** from the immediate Phase 11E baseline. |
| Migration/flag | 0031 absent from development public; existing warehouse metadata empty and `WAREHOUSE_ENABLED=false`. |
| Preservation/scope | All **240 unrelated entries and recorded hashes preserved**; all existing production tracked hashes unchanged; exactly nine Phase 11E paths. |
| Quality | Git diff check, explicit new-file whitespace, targeted private-key/AWS/Google/token/provider-path scan, status delta and tracked hash checks passed. |
| Git | HEAD and local `origin/master` remain `e8a658cb7cc113f2a13c1b20ef7d2c48b51b8d09`; index empty; no staging/commit/push. |

The warnings remain the existing Starlette TestClient/httpx and Authlib/httpx
deprecations. The same three unavailable historical-fixture skips remain; no new
failure, skip or dependency warning was introduced. Earlier exploratory runs are
superseded by this corrected frozen-tree regression, which includes the resource-wide
uncertainty admission guard and independent planning/publication precondition checks.

The populated migration fixture preserves an accepted source state, its successful
cumulative Gold run and FRESH/current Gold pointer. Repeated migration fixtures also
compare every pre-existing row, column, constraint, index, user trigger and function
definition. No historical dataset is automatically bootstrapped. Task-local final
evidence is `tmp/phase11e-final-evidence.json`; generated evidence is not implementation
scope. No frontend lint was needed because no frontend file changed.

**Phase 11E is ready for review**, with the limitations in sections 11, 20 and 25.
Migration application, enabling execution and release remain separate approval gates.
No Phase 11F implementation or BigQuery resources were created.

## 29. Approved development migration and release gate

Migration `0031_warehouse_reliability.sql` was committed to development public.
The immediate release baseline is `tmp/phase11e-release-before.json`; the public
table count increased from **56 to 66**. All original 56 row counts and SHA-256
fingerprints remained unchanged, including trusted source state and Gold data,
ownership, successful runs and current pointers. Existing catalog definitions
were preserved; 30 additional internal referential-integrity triggers on existing
parents belong to foreign keys from the new tables.

All ten new tables have **zero rows**: `warehouse_accepted_manifests`,
`warehouse_accepted_events`, `warehouse_outbox`, `warehouse_execution_intents`,
`warehouse_writer_leases`, `warehouse_run_journal`, `warehouse_reconciliations`,
`warehouse_publications`, `warehouse_publication_heads`, and `warehouse_checkpoints`.
No bootstrap, discovery, execution or publication work was created automatically.

The new catalog matches an independently instantiated disposable-schema inventory:
67 columns, 139 catalog constraints (including NOT NULL entries), 21 valid/ready
indexes, 28 enabled triggers, five functions and three sequences. Both deferred
consistency triggers were verified. Twenty invalid direct SQL/TRUNCATE probes
against public were rejected and rolled back without committed synthetic rows.

Disabled import/startup, health, actual development database health and OpenAPI
checks passed with cloud settings removed. No BigQuery package or warehouse routes
were present. Actual cloud API and source-artifact calls were blocked by the smoke
harness; the existing legacy Gold import constructs an S3 client but issued no
cloud request. Disabled services rejected work before database access.
`WAREHOUSE_ENABLED=false` remains unchanged.

The eight frozen implementation/migration/test hashes match the accepted tree.
Release scope is exactly nine paths, including this report. The 240 unrelated
dirty entries and recorded hashes, historical deletions and existing production
file hashes are preserved. No frontend, dependency, route, production pipeline,
Gold implementation, provider or Phase 11F implementation change was introduced.
No BigQuery resources were created.

Post-migration release validation completed successfully: focused reliability
**48 passed** in 160.20 s (`tmp/phase11e-release-focused.xml`); full backend
**375 passed, 3 known historical-fixture skips, 2 existing dependency warnings**
in 336.97 s (`tmp/phase11e-release-regression.xml`). The final read-only public
comparison after regression again confirmed all original 56 tables unchanged,
66 total tables and all ten new tables empty. Targeted secrets/provider-path,
scope, whitespace and frozen-hash checks passed. Release uses the approved
commit message `feat: add warehouse reliability protocol`; the resulting commit
hash and remote verification are recorded in the release evidence and final report.
Phase 11F remains a separate task and has not been started.
