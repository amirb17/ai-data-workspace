# Phase 11C - Provider-neutral SCD transition engine

Implementation review, 2026-10-21. Baseline `769fe9ce56aff3165a15d47c7ef12b88fae636c5` on master, matching origin/master. Pure per-member planning and bounded in-memory reference application only. No application integration, migration, staging, commit or push.

## 1. Pre-existing paths

All 240 initial unrelated status entries are preserved. Inventory, original file hashes and tracked-file hashes are saved under ignored `tmp/phase11c-files-before.json`. Historical deleted tests remain deleted. No older reports, screenshots, fixtures, tools or generated evidence were modified. No existing tracked implementation file changes are required.

## 2. Files added

- `app/warehouse/scd_types.py`: immutable context, evidence, state, authority, decisions, errors, plans and patches.
- `app/warehouse/scd_hashing.py`: attribute/event/state fingerprints using existing V1 scalar canonicalization.
- `app/warehouse/scd_invariants.py`: schema/type/key/timeline validation and half-open reference lookup.
- `app/warehouse/scd_common.py`: shared evidence preparation, ordering, retry and plan construction.
- `app/warehouse/scd1.py`: SCD1 pure planner.
- `app/warehouse/scd2.py`: SCD2 forward/current-boundary planner.
- `app/warehouse/scd_reference.py`: atomic in-memory compare-and-apply.
- `tests/test_warehouse_scd.py`: compact matrices, corruption, golden and determinism tests.
- `docs/phase11c-scd-engine.md`: this report.

## 3. Migrations and dependencies

None added or changed. Development remains at 56 tables after previously applied 0030; `warehouse_model_configurations` and all six foundation tables remain empty. No cloud/provider dependency, credential/configuration file, frontend or public route change.

## 4. Context and accepted input

`ModelContext` consumes the actual 11B `DimensionConfiguration` and `SourceSchema`, alongside logical model/version IDs, stable UUID namespace, V1 algorithm version and `SourceContractPin` (dataset/version/policy/rule IDs). It validates the reviewed column roles through `validate_model`. No alternative FACT/DIMENSION/SCD configuration representation is introduced. FACT execution is rejected here; its 11B key/grain contracts are untouched.

`DimensionEvent` contains that exact context, immutable tuples of declared native typed business values, explicit source `Acceptance`, optional logical UUID event identity and optional `AcceptedOrder`. `from_mapping` copies only declared fields; unmodeled ingestion/run/file/physical metadata is discarded. Missing declared fields, wrong native types, null keys, forbidden null attributes, float/nested values and unresolved timestamps fail with safe typed codes. DATE remains a date; TIMESTAMP requires an aware, already resolved instant. No CSV parsing, cleaning or timezone inference is performed.

Source pin IDs and acceptance are attestations from a trusted upstream caller, not authorization or cryptographic proof. Future integration must obtain them from owned approved 11B configuration and pinned accepted source evidence. The pure module does not connect to PostgreSQL to verify those claims. No wrapper or route exposes it.

## 5. State and TransitionPlan

Frozen dataclasses and tuple values describe one member's state. `MemberState` pins context/business identity, versions, highest accepted business time or immutable source order and the last event's UUID/fingerprint. `Version` contains stable member identity, optional version identity, interval/current flag, typed tracked/nonhistorical values and tracked hash. SCD1 has one row with no version SK or interval; SCD2 has business-time rows.

`TransitionPlan` carries typed decision/code, context, member/event identity, complete expected prior-state digest, immutable result, ordered typed patches and changed tracked/nonhistorical field names. Each patch carries member/version target plus expected before and proposed after `Version` objects. Those rows expose version SKs, bounds, current flags and hashes directly; no provider SQL or arbitrary update dictionary is emitted.

## 6. SCD1 decision matrix

| Accepted event relative to current state | NORMAL | Explicit CORRECTION | BACKFILL |
|---|---|---|---|
| First member | INSERT | INSERT with pinned empty-state digest | INSERT |
| Same accepted event/payload | ALREADY_APPLIED | ALREADY_APPLIED | ALREADY_APPLIED |
| Same order/time, changed business values | CONFLICT | UPDATE | CONFLICT |
| Newer, changed business values | UPDATE | UPDATE | UPDATE |
| Newer, identical business values | UNCHANGED plus observation patch | Same | Same |
| Older event | STALE_REJECTED | UPDATE current values if changed; retain high watermark | HISTORICAL_REPAIR_REQUIRED |
| No comparable order, changed values | CONFLICT / ORDERING_REQUIRED | Explicit current-value UPDATE | CONFLICT / ORDERING_REQUIRED |

Both declared tracked and nonhistorical fields participate in SCD1 value comparison. Neither changes member identity.

## 7. SCD1 stale and untimed behavior

With effective time configured, compare against the highest accepted observation, not processing time or merely the row's creation. An unchanged October event advances only observation/provenance metadata; a later-delivered June change is stale even if the current business values originated in January. UNCHANGED therefore means no business-value mutation, not that observation metadata must be discarded.

Untimed SCD1 can insert its first accepted member. Later changes require `AcceptedOrder(domain UUID, nonnegative position)` from a trusted immutable source chain. Different domains or missing order cannot establish newer truth. Identical values may remain UNCHANGED, but unprovable ordering does not advance the watermark. A first state without ordering cannot later silently adopt a new ordering domain through normal execution. No list position, clock, filename or ingestion timestamp is used as business authority.

## 8. Correction authority

`Authority` uses typed NORMAL, CORRECTION or BACKFILL modes; there is no force flag. CORRECTION requires the exact prior state digest. It can explicitly restate SCD1 current values from older accepted evidence, retaining the maximum accepted time/order so subsequent ordinary stale events cannot regress state. This is one member's current-value correction, not replay or backfill orchestration. Same-event correction retries are already applied, even with the original prior-state digest; changed content must pin the current state.

## 9. SCD2 decision matrix

| Accepted input | Decision / effect |
|---|---|
| No history | INSERT_INITIAL_VERSION: one open version at actual effective boundary |
| Exact last event or materialized boundary/payload retry | ALREADY_APPLIED; no patches |
| Forward identical tracked/nonhistorical values | UNCHANGED; observation only |
| Forward nonhistorical-only change | UPDATE_NONHISTORICAL; no new version |
| Forward tracked change | CLOSE_AND_INSERT; close current and insert new current |
| Same current boundary/observation time, changed tracked values | CONFLICT in NORMAL/BACKFILL |
| Same current version boundary, pinned CORRECTION | CORRECT_CURRENT; retain boundary and version key |
| Accepted historical event requiring different interval content | HISTORICAL_REPAIR_REQUIRED; no patches |
| Entire history already closed | HISTORICAL_REPAIR_REQUIRED; no implicit reopening |

No deactivation, inferred-member execution, late splitting or fact loading occurs. Newer events run the normal forward transition regardless of NORMAL/BACKFILL/CORRECTION context, with correction authority still pinned when requested.

## 10. Half-open worked example

Start with accepted C001 / Pune at January 1. This produces `[Jan 1, NULL)`, current=true, with no invented prehistory. Accepted C001 / Mumbai at June 1 produces exactly:

| Values | valid_from | valid_to | is_current |
|---|---|---|---|
| C001 / Pune | Jan 1 | Jun 1 | false |
| C001 / Mumbai | Jun 1 | NULL | true |

At exactly June 1, `version_at` returns Mumbai; the immediately preceding instant belongs to Pune. An October Bangalore change closes Mumbai at October and inserts Bangalore there. Normal forward transitions create no gap or overlap. Lookup is a local test/reference primitive, not fact resolution execution.

## 11. Tracked attribute hash V1

SHA-256, lowercase 64-hex, over this exact UTF-8 JSON payload:

```text
["datarise-warehouse-tracked-attributes",1,
 [[field_name, canonical_value(value)], ...]]
```

Fields sort by exact Python Unicode code-point name order. Serialization uses compact separators, `ensure_ascii=False`, `allow_nan=False`. Scalars use unchanged 11B `canonical_value`: exact strings/Unicode, separate bool/int/Decimal/date/aware timestamp tags, normalized UTC microseconds and exact finite Decimal precision. NULL attributes are allowed when the schema permits them; NULL keys are never allowed. Floats, nonfinite/nested/unresolved timestamp values fail closed, including FLOAT attributes rather than inventing a second lossy hash encoding.

Only configured tracked fields are passed to this function by planners. Business keys, event/effective time, nonhistorical attributes and undeclared metadata do not enter history-change detection. `changed` compares canonical typed scalars, so Python's bool/int or Decimal/int equality cannot collapse semantic differences.

Literal test values include:

| Value set | Literal SHA-256 |
|---|---|
| city=Pune | `43b46f6ad726157a60d90afdfa6fd217faa3e5ff9ee322e86025762bb2be2afc` |
| text=decomposed e + CJK | `e7094f95605914ca7d7732d6ffb796ec8cb886a4da388603291fc2d1e2018a83` |
| exact large Decimal | `db585c9171718fffa0de0eb1532b16dbb032eb8536d1ddb7c67b4973ee999796` |
| DATE + aware timestamp | `5413355b9ceecaf42c427db1a9e74070612462c44d8719b0b650866a938f0b8e` |
| bool + int + NULL | `f555f4a778e1b10f7f496e5ffacd68cb3f04fa921e8c7ddc6688d966c02660fb` |

Event/state digests have separate `datarise-warehouse-scd-event` and `datarise-warehouse-scd-state` V1 domains. They use the same scalar encoding plus model/source pins; they serve evidence comparison and optimistic preconditions, never surrogate-key allocation. Full state digest covers the complete bounded per-member timeline and observation metadata.

## 12. Nonhistorical attributes

SCD2 updates only the current open version's declared nonhistorical values through UPDATE_NONHISTORICAL_ATTRIBUTES. It preserves tracked values/hash, bounds, member/version identity and prior closed rows. Forward no-change observations also update only observation metadata. Historical nonhistorical corrections are deferred; there is no implicit all-history overwrite.

## 13. Member versus version identity

Actual 11B `surrogate_key` generates DIMENSION_MEMBER from ordered business keys and stable namespace, and DIMENSION_VERSION from those keys plus resolved valid_from. No algorithm is copied. SCD1 never has a historical version key. SCD2 forward changes retain the member key, retain the closed predecessor's version key and create the new boundary's deterministic version key. Compatible-context keys remain deterministic, but switching a state to a different model/config/source pin fails closed pending a future reviewed conversion.

Tests assert the existing literal C001 member key `b60ec522cb560ecc822384d9754a2ec9c6d283e7dda51abc98ad60f242dfc511` and June 1 version key `ab51609380916238099b5418f8ce8aaac3845f04728de20983a924aa3a0ef86d`.

## 14. Same-time conflicts

NORMAL cannot overwrite tracked values at the same SCD2 current boundary or latest accepted observation time. SCD1 also treats changed nonhistorical values at identical ordering/time as a conflict. Stable codes identify SAME_EFFECTIVE_TIME_DIFFERENT_VALUES and EVENT_ID_REUSED. Same-boundary SCD2 nonhistorical changes can use the explicit current-only patch when not stale. Exact repeated evidence returns ALREADY_APPLIED rather than creating duplicates.

## 15. Historical-repair boundary

January Pune / June Mumbai plus accepted March Delhi returns HISTORICAL_REPAIR_REQUIRED in all operation modes, without mutation. The same applies to accepted observations older than a later unchanged watermark when materialized state cannot prove an exact retry. No gap extension, splitting, deletion/coalescing of boundaries or dependent fact re-resolution is implemented. A materialized historical boundary with exactly matching tracked and nonhistorical payload can prove ALREADY_APPLIED; if later changes prevent that proof, return repair required conservatively.

## 16. Timeline invariants

Validate supplied state before planning or reference application: exact source-native field types and role membership; deterministic business/member/version keys; exact tracked hashes; tuple rows; sorted, distinct valid_from; valid_to greater than valid_from; no overlap; unique version keys; stable member key; at most one open interval and it must be last; is_current equivalent to open interval; valid observation watermark and safe last-event metadata. SCD1 requires one interval-free current row. DATE/TIMESTAMP domains cannot mix. Existing valid gaps or fully closed histories are not silently repaired/reopened.

Invalid input produces `ScdError` with a stable `Code`, without raw business values in messages. Timeline/key/config failures are errors, not transition mutations. No sorting or repairing corrupt timelines occurs.

## 17. Retry and idempotency

Exact last-event fingerprint retries are ALREADY_APPLIED. A reused last logical event UUID with changed content conflicts. Exact older materialized SCD2 boundaries may also prove retries. Applying a previously applied TransitionPlan returns ALREADY_APPLIED if the complete intended result digest matches. Global historical event-ID deduplication is intentionally unavailable without later accepted-event/checkpoint infrastructure; no full event ledger is invented.

## 18. Correction/backfill boundaries

SCD2 CORRECTION may replace tracked/nonhistorical values only at the existing current version's exact boundary, with complete expected-state fingerprint. It preserves valid_from, interval identity and version SK. It never moves a boundary, rewrites closed history or splits an interval. SCD1 current-value correction is explicit as described above. BACKFILL never grants an override for stale SCD1 or middle-history SCD2 changes. No operation advances a source checkpoint or publication revision.

## 19. Source acceptance boundary

REJECTED and QUARANTINED evidence produce SOURCE_REJECTED/no patches even in CORRECTION/BACKFILL. Source policy's prior stale/DQ/load-policy rejection cannot be reversed here. Callers must not label such evidence ACCEPTED. INFER_MEMBER and DEACTIVATE_ON_COMPLETE metadata are retained but not executed; an actual supplied accepted member is not an inferred or absence-generated member.

## 20. Reference adapter

`apply_plan` works on one bounded immutable member in memory. It validates current/result states and model/member pins, compares the whole expected prior digest, stages typed patches in a local list, validates patch action semantics and checks the exact result. Closures can change only interval end/current flag; nonhistorical patches only those values; current corrections preserve identity/bounds. Observation watermarks cannot regress. A stale or incomplete plan raises PRECONDITION_FAILED; no partially applied state escapes. No distributed locks or production storage are introduced.

## 21. Determinism and golden evidence

Tests cover repeated planning, reversed mapping insertion order, caller mutation after event construction, immutable outputs, fresh-process result fingerprint, literal attribute hashes, original 11B keys, a 12-event forward-chain invariant/retry matrix, patch tampering, source rejection across all modes and timeline corruption. No random ID, wall clock, process hash or input list ordinal is used in implementation.

## 22. Development safety

Before/after read-only whole-table count/SHA-256 snapshots are captured for all 56 public tables, exceeding the requested critical subset. Preflight checks exact database/schema, applied 0030, empty configurations/foundation and disabled feature flag. No 11C database connection or persistence exists. Baseline and comparison evidence live under ignored `tmp/phase11c-db-before.json` and task-local audit output. Final comparison result is recorded below after validation.

## 23. Focused tests

`python -m pytest tests/test_warehouse_scd.py -q`: 89 passed after self-review. Final JUnit: `tmp/phase11c-focused.xml`. Pure tests run while warehouse functionality is disabled.

## 24. Existing 11A/11B validation

Run `RUN_DB_TESTS=1 uv run --no-sync pytest tests/test_warehouse_scd.py tests/test_warehouse_modeling.py tests/test_warehouse_foundation.py -q --tb=short`. Final counts are recorded below. Existing generated-schema fixtures remain unchanged; no deleted historical fixture was restored.

## 25. Full available backend regression

Run `RUN_DB_TESTS=1 uv run --no-sync pytest -q --tb=short`, with workspace UV cache and a new JUnit output. Final results are recorded below. Historical missing-fixture skips are not presented as complete historical-suite coverage.

## 26. Diff and secret checks

Run `git diff --check`, explicit new-file whitespace checks, targeted private-key/AWS/Google/GitHub-token scanning, exact status-delta validation and hashes of every original tracked/unrelated file. Verify no staged files and unchanged HEAD. A targeted scan is not a universal secret-free proof. Final results are recorded below.

## 27. Existing pipeline compatibility

No upload, Raw/Bronze/Silver/DQ, APPEND/UPSERT/SNAPSHOT, trusted-state, profile, semantic, relationship, metric, analytics, Ask or SQL module changes. Source load strategy never appears as SCD policy or an execution switch. No public routes, service wrappers or startup imports added.

## 28. Existing Gold compatibility

Current Gold writers, schemas, artifacts, pointers, freshness and consumers are untouched. The only reference execution output is a returned Python object; it is not published or connected to production analytics. Final public-table comparison includes dataset_gold_runs and datasets.

## 29. Limitations

One member per call; complete in-memory timeline hashing/validation, no scale claim. Native typed scalars only, no float attributes. Context/acceptance/order attestations require a trusted future authorized integration. No automatic schema/model-version conversion, authoritative historical event ledger, global event-ID deduplication, source CDC, batch conflict resolution, inferred lifecycle, snapshot deactivation, late repair, fact resolution, persistence, provider/compiler, distributed locking, checkpoints or publication. No local reference output is an accepted production result.

## 30. Review readiness

Ready only after final focused, existing warehouse, full backend and scope/database checks pass. Implementation remains disabled/unintegrated. No migration, staging, commit, push or 11D work is authorized by this phase.

## 31. Recommended exact 11D scope

Implement provider-neutral late accepted dimension interval-repair planning with exact predecessor/successor bounds, immutable existing boundary identities, explicit same-boundary corrections and conflict handling. Add missing-member/inferred lifecycle and as-of fact-reference/re-resolution planning against the same pinned accepted evidence; prevent orphan/fanout and keep member/version identity stable. Include matrices for before-first, gap, middle, multiple late changes and unchanged payloads without resurrecting source-rejected events. Keep checkpoint/reconciliation/publication/replay infrastructure for 11E and BigQuery/provider integration for 11F. Do not implement 11D in this review pass.

## Final validation and review outcome

| Check | Result |
|---|---|
| Final focused SCD suite | 89 passed, 1.02 s; `tmp/phase11c-focused.xml` |
| Earlier combined warehouse run | 193 passed, 1 existing warning, 84.47 s: 86 SCD + 74 modeling + 33 foundation |
| Final frozen-tree full backend suite | 242 passed, 3 skipped, 2 existing warnings, 173.83 s; `tmp/phase11c-final-regression.xml` |
| Final full-suite warehouse coverage | 89 SCD + 74 modeling + 33 foundation, all passing |
| Development data | All 56 public table counts/fingerprints match the immediate Phase 11C baseline after validation |
| Warehouse state | 0030 remains applied; configuration/foundation tables empty; WAREHOUSE_ENABLED=false |
| Existing files and dirty paths | All original tracked-file and unrelated-file hashes preserved; all 240 baseline status entries preserved |
| Scope | Exactly nine new files listed in section 2; no existing source, dependency, migration, frontend or pipeline edits |
| Diff, whitespace and targeted secret checks | Passed; includes new untracked files explicitly |
| Purity inspection | Seven implementation modules: no DB/service/provider/network, random IDs or wall-clock calls |
| Git | HEAD unchanged at the Phase 11B checkpoint; index empty; nothing committed or pushed |

The combined run preceded the final correction-retry and closed-history/observation-guard refinements; the final full suite ran after all implementation changes, with eight source/test hashes frozen in `tmp/phase11c-freeze.json`. The increase from the old backend baseline of 153 passing tests is exactly the 89 new SCD tests. The three known missing historical-fixture skips remain ownership compatibility, semantic deadline safety and PostgreSQL model-summary profile coverage. Warnings remain Starlette TestClient/httpx and Authlib/httpx deprecations. No new failure or warning was introduced.

Final self-review covered independent source/SCD policy boundaries, stale overwrite protection, no ingestion/nonhistorical fake history, interval/current/key integrity, same-time conflicts, retries, stable scalar hashing, source acceptance, atomic reference patches, scoped correction authority and deferred historical repair/provider/reliability work. The implementation is **ready for Phase 11C review**. No 11D work was started. Task-local final evidence: `tmp/phase11c-final-evidence.json`.
