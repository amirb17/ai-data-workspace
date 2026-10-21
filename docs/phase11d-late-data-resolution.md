# Phase 11D - Late dimensions, inferred members and fact reference resolution

Implementation review, 2026-10-21. Baseline `e119f53ad26e29dfab93ff1a65dd316cbad64de6`, matching origin/master. Provider-neutral, bounded, in-memory reference semantics only. No production integration, migration, staging, commit, push or 11E work.

## 1. Pre-existing changes

All 240 unrelated baseline status entries are preserved. Their inventory/hashes and original tracked-file hashes are recorded in ignored `tmp/phase11d-files-before.json`. Historical deleted fixtures remain deleted. No older reports, screenshots, tools or validation evidence are edited.

## 2. Files changed

Extended:

- `app/warehouse/scd_types.py`: repair, inference and reference contracts/enums alongside existing shared types.
- `app/warehouse/scd_reference.py`: typed historical insertion, interval shortening and historical content correction.

Added:

- `app/warehouse/scd_repair.py`: historical repair planning and deterministic plan digest.
- `app/warehouse/inferred_members.py`: separate provisional member lifecycle and atomic completion.
- `app/warehouse/fact_resolution.py`: one pinned fact-to-dimension relationship lookup and resolution digest.
- `app/warehouse/fact_reresolution.py`: semantic affected ranges and atomic bounded reference rebinding.
- `tests/test_warehouse_late_data.py`: pure matrices, invariants, identity, inference and reference tests.
- `docs/phase11d-late-data-resolution.md`: this report.

## 3. Migration and runtime status

No migration, dependency or environment change. Development retains 56 public tables with 0030 already applied, an empty configuration table and six empty foundation tables. WAREHOUSE_ENABLED=false. No public route, startup import, provider/client, cloud credential, physical resource, worker or persistence layer is introduced.

## 4. HistoricalRepairPlan

The planner consumes existing `ModelContext`, `MemberState`, `DimensionEvent`, `Acceptance`, `Authority`, `Version` and V1 key/hash logic. No competing representation replaces these types. `HistoricalRepairPlan` wraps the existing `TransitionPlan`, adding explicit affected half-open ranges, the dimensional event digest and earliest authoritative valid_from.

Transition patches carry target member/version IDs, expected before rows and complete typed after rows. New actions are SHORTEN_INTERVAL, INSERT_HISTORICAL_VERSION and CORRECT_HISTORICAL_VERSION. The complete expected-state digest is the optimistic precondition. No SQL or arbitrary row-update dictionary appears in a plan.

## 5. Middle-interval split

Given January Pune / June Mumbai, accepted March Delhi produces:

| Values | Before | After |
|---|---|---|
| Pune | [Jan, Jun) | [Jan, Mar) |
| Delhi | absent | [Mar, Jun) |
| Mumbai | [Jun, infinity) | unchanged |

Pune's version key stays tied to January; Delhi gets March's deterministic key; Mumbai's boundary, attributes, current flag and key remain unchanged. The current member does not rewind to Delhi. Expected affected range: [Mar, Jun).

## 6. Before-first evidence

Default `BeforeHistoryPolicy.EXTEND_FROM_ACCEPTED_EVIDENCE` permits an actual earlier accepted event. If June is earliest known and March evidence arrives, insert [Mar, Jun). Earliest authoritative coverage becomes March; nothing before March is invented. This respects 11B FROM_FIRST_OBSERVED_EVENT without claiming complete history.

A caller may explicitly use typed `BeforeHistoryPolicy.REJECT` for a conservative local boundary, yielding BEFORE_HISTORY_REJECTED. This is a denial policy, not a force/acceptance override. No arbitrary declared or infinite historical start is introduced.

## 7. Gap rule

For January-to-March Pune and May-onward Mumbai, accepted April Delhi inserts [Apr, May). [Mar, Apr) remains a gap; neither predecessor nor new row is stretched backward. Facts there remain NO_VERSION_AT_EVENT_TIME. A known member's gap is not an entirely missing member and never automatically infers a placeholder. Events after a fully closed tail cannot implicitly reactivate/reopen it.

## 8. Multiple late events

January Pune / June Mumbai plus April Delhi, March Bangalore and May Hyderabad yields January-to-March Pune, March-to-April Bangalore, April-to-May Delhi, May-to-June Hyderabad, then Mumbai. Tests exercise all six arrival permutations and assert identical complete member state/digest. Forward observation metadata is retained during historical repair, so last-arrival bookkeeping does not change these results.

Unchanged late tracked payloads create no new boundary. Existing boundaries are never deleted/coalesced merely because adjacent payloads later become equal. Consequently no canonical minimal version graph across *different prior knowledge/boundary sets* is claimed: such histories can retain different redundant identities while their as-of business values agree. A durable accepted-event canonical rebuild is outside this phase.

## 9. Same-boundary conflicts and corrections

Exact tracked/nonhistorical payload at an existing boundary is ALREADY_APPLIED. A changed tracked payload under NORMAL/BACKFILL is CONFLICT. CORRECTION requires the exact prior state digest and can replace content at that boundary, preserving interval bounds and version identity. Re-evaluating the applied exact correction is ALREADY_APPLIED even with its original prior-state digest.

Nonhistorical-only historical changes under normal execution are DEFERRED_NONHISTORICAL, including interior observations; they do not create business versions or overwrite every historical row. A pinned same-boundary CORRECTION may explicitly replace those fields on that exact version. Source rejection always wins over operation mode.

## 10. Keys, hashes and intervals

Actual 11B `surrogate_key` remains the only member/version identity implementation. Member SK is stable across repairs and inferred completion. Existing valid_from and version SK never move; shortening valid_to does not rekey a predecessor. Each inserted boundary gets its existing V1 DIMENSION_VERSION key. The 11C tracked hash and scalar encoding remain unchanged.

`validate_timeline` checks complete result before emission and before application: native typed fields, deterministic keys, sorted unique starts, positive half-open bounds, no overlap, stable member key, unique version keys, at most one open interval, open last and is_current equivalent to open. Valid gaps stay gaps. Corrupted input is rejected rather than silently sorted/repaired; only the newly planned insertion is placed in sorted order.

## 11. Inferred representation

`InferredMember` is a separate immutable provisional **member handle**, not an authoritative `Version`. It has context/source pins, complete typed business keys, the ordinary stable member SK, INFERRED/RESOLVED status and an optional authoritative completion-state digest. It has no valid_from, valid_to, tracked attributes or version SK.

Its deterministic tagged digest identifies lifecycle/evidence state without using a fact's time as a dimensional effective boundary. The same missing member inferred by February and October facts has exactly the same placeholder. Facts retain their own event times and logical event identities separately. No Unknown member or random temporary identity exists.

## 12. Completion

`plan_completion` consumes accepted real dimensional evidence and delegates authoritative creation to existing 11C SCD1/SCD2 semantics. `apply_completion` validates both the placeholder and authoritative transition, compares preconditions, stages locally and returns both outputs atomically. INFERRED becomes RESOLVED under the same member key; no duplicate logical member or synthetic authoritative prehistory is created.

For SCD2, real June evidence starts an actual June version. Earlier facts are not retroactively mapped to it. Their stable provisional member handle remains available as explicit nonauthoritative evidence; a fresh lookup reports NO_VERSION_AT_EVENT_TIME until earlier accepted dimension history arrives. Completion does not turn NULL version references into authoritative success. Existing authoritative history may be repaired independently with the repair planner before resolving references.

For SCD1, the real one-row member retains the same SK and current-value semantics; no historical version is fabricated. A resolved completion handle cannot be reused as an alternate dimension update path. Retries of the same completion plan preserve both outputs.

## 13. Missing-key policy

Inference requires explicit INFER_MEMBER, complete valid/non-null keys and accepted fact evidence. REJECT policy, missing/NULL/invalid keys and rejected/quarantined facts cannot create a placeholder. `plan_inference` accepts an optional authoritative member state; when supplied, a known member or known gap cannot be reclassified as absent. None is the trusted caller's scoped missing-member lookup result, not proof derived by querying a database here.

The pure reference layer does not authenticate these attestations or query a full dimension registry. Future integration must supply owned approved model/source pins and the correct scoped lookup state.

## 14. FactReference contract

`FactReference` contains an existing V1 fact SK, logical relationship UUID, exact dimension context, ordered typed dimension business keys, native fact business/event time, source Acceptance, fact SourceContractPin and optional fact event UUID. No measures, raw rows, warehouse provider IDs or source paths are loaded. `from_mapping` selects declared keys; missing keys become explicit invalid-key outcomes rather than inferred Unknowns.

One primitive resolves one relationship, permitting customer/product/channel roles to compose independently. Duplicate (fact SK, relationship UUID) inputs in a bounded re-resolution set fail closed. Fact keys/grain are supplied using existing contracts, never regenerated by new rules in this phase.

## 15. As-of algorithm

Validate exact model/configuration/source/key pins and native time/key types. Validate complete authoritative member history. Select the one row satisfying valid_from <= T and (valid_to is NULL or T < valid_to). Return RESOLVED only with a real version SK. Never substitute current/nearest/next rows when time coverage is missing.

For SCD1, CURRENT_MEMBER_ONLY explicitly identifies a real current member SK and makes no historical as-of claim. SCD1 has no version SK. Untimed/current-value resolution cannot reconstruct business history.

## 16. Half-open boundaries

January-to-June Pune resolves May 20 to Pune; exactly June 1 resolves to Mumbai. A timestamp just before June belongs to Pune. DATE stays DATE, and TIMESTAMP requires an aware resolved instant. Mixed domains and naive/local wall times fail closed; there is no implicit DATE conversion or timezone reinterpretation.

## 17. Before-history facts

A March fact against a June-starting authoritative member returns NO_VERSION_AT_EVENT_TIME. INFER_MEMBER does not authorize the June row to cover March. No sentinel, infinite-before interval, Unknown row or nearest-version shortcut is used. An explicit unresolved result preserves the fact's business time for a later bounded repair/rebind.

## 18. Fact gaps

Facts in real gaps remain NO_VERSION_AT_EVENT_TIME. Placeholder inference applies to entirely missing members only; it does not fill interior gaps or before-history coverage for an existing authoritative member. If no member exists, REJECT yields MEMBER_NOT_FOUND and INFER_MEMBER yields INFERRED_MEMBER_REQUIRED. A created provisional handle yields INFERRED_REFERENCE, which is explicitly not authoritative RESOLVED.

## 19. Late facts

Arrival/processing time is irrelevant. A fact arriving after October with April business time resolves against April history using the exact pinned dimension model/configuration/source contract, never whichever model/version is latest now. Fact event ID, fact SK, member SK, version SK and result digest stay distinct.

## 20. Fanout and orphan protection

Overlapping/corrupt history produces AMBIGUOUS_HISTORY or INVALID_DIMENSION_STATE with a stable error code; no list of matches or duplicate fact is returned. Cross-model/source/config/version pins and wrong scoped members fail closed. Missing keys, missing members, unknown coverage, source rejection and provisional inference each have explicit statuses. No NULL version is represented as authoritative SCD2 success.

## 21. Bounded re-resolution

`plan_reresolution` consumes before/after member timelines and bounded prior `ResolutionResult` objects, optionally before/after inferred handles for completion. It recomputes each expected old resolution and compares its exact digest, then produces typed UNCHANGED, REBIND or UNRESOLVED entries with old/new references, reason and expected previous-resolution digest.

Entries sort deterministically by logical fact/relationship identity. Wrong old references, duplicate entries and mismatched contexts/members fail closed, even for empty fact sets when supplied state pins are incompatible. No production fact is mutated.

## 22. Affected ranges

Repair plans expose [Mar, Jun) for a March split into a June-bounded predecessor. Re-resolution independently derives semantic differences from the union of before/after interval boundaries, comparing version identity and canonical business content at each segment. Caller-supplied too-small ranges cannot hide changes. Adjacent changed segments may merge; unchanged gaps and predecessor segments remain excluded.

Use both the pinned member identity and these ranges to select bounded candidate references. The reference implementation validates only the supplied bounded set; it does not scan or claim to enumerate all production facts. Provider partition/query efficiency and complete candidate-set verification belong to later phases. SCD1 uses current-member comparison without pretending it has an authoritative business-time affected range.

## 23. Corrections versus rebinds

Inserting March Delhi changes the version referenced by an April fact, producing REBIND. A January content correction retains January's version SK, so facts retain identity (UNCHANGED/IDENTITY_STABLE), while their new resolution evidence may reflect corrected dimensional content. Facts outside affected ranges retain their prior reference object exactly. No fact identity, measures or grain are rewritten.

## 24. Determinism, digests and retries

Existing canonical typed scalar/key/hash contracts are reused. New SHA-256 domains are `datarise-warehouse-historical-repair`, `datarise-warehouse-inferred-member`, `datarise-warehouse-fact-dimension-resolution` and `datarise-warehouse-fact-reresolution`, all explicitly V1, compact UTF-8 JSON using the same helper. Plans pin state digests, actions, affected boundaries, context/source metadata and relevant event/reference identities. Inferred fingerprints include no guessed effective time.

Golden generic vectors in tests:

| Evidence | Literal SHA-256 |
|---|---|
| March split plan | `7a83e19e13ae7c8aa14ffbaf1eb6754192db4d15344f89cb2bc9ed2e0120d26e` |
| Repaired member state | `aabf96cd8d8b06b0ad6682edf52d951bdbcba98a21543d52336652b4a4ed1e76` |
| April fact resolution | `4302717e3e85027f96c1f245c7e3e136725a5486f53c56b90da43df09f44f2fe` |
| Inferred C999 handle | `3decb5b423f50510022ea7ccac425513d469f35dbba72446cbbd49ab5a6c6c29` |

Tests also assert original C001 member/June version V1 keys, fresh-process plan/resolution digests and arrival permutations. Exact boundary retries are already applied. There is no historical global event-ID ledger; 11C's last-event reuse guard remains intact, and broader durable deduplication is deferred to 11E.

## 25. Source acceptance and authority

Source-rejected/quarantined dimension evidence produces SOURCE_REJECTED regardless of NORMAL/CORRECTION/BACKFILL. Rejected facts cannot resolve or infer; rejected real dimension evidence cannot complete placeholders under any mode. Accepted late events may split finite known intervals under NORMAL/BACKFILL; changing existing boundary content requires explicit CORRECTION and exact prior-state digest. No force flag, DQ resurrection or source-policy override is introduced.

## 26. Development database safety

Read-only count/SHA-256 snapshots of all 56 public tables are captured before/after validation in task-local `tmp/phase11d-db-before.json` and audit output. No 11D module connects to a database. Configuration/foundation remain empty and warehouse disabled. Any mismatch must be investigated rather than restored; final comparison is recorded below after tests.

## 27. Focused tests

Final focused late-data suite: 85 passed, including repair relation/payload/authority matrices; six independent-repair arrival permutations; gap/before-first/current-coverage handling; exact correction preconditions; SCD1/SCD2 inference; NULL and missing-policy rejection; completion/rebinding; half-open Date/Timestamp resolution; corrupt history; tampered/partial reference application; source rejection across modes; literal/fresh-process digests. Evidence: `tmp/phase11d-focused.xml`.

## 28. Existing warehouse validation

The combined warehouse run passed 273 tests (77 then-current late-data + 89 SCD + 74 modeling + 33 foundation), with one existing Authlib warning, in 83.42 s. Final source pin/guard refinements and eight extra tests were subsequently validated by focused and final full regression. Existing 11A/11B/11C integration fixtures remain unchanged and use disposable schemas. No deleted historical fixture was restored.

## 29. Full backend regression

Run the final frozen tree with RUN_DB_TESTS=1 and `uv run --no-sync pytest -q --tb=short`, workspace UV cache and `tmp/phase11d-regression.xml`. Expected increase from the approved baseline is exactly 85 new passing tests, retaining the known three missing historical-fixture skips and two dependency warnings. Final measured counts are recorded below.

## 30. Diff, secret and scope checks

Run git diff --check, explicit new-file whitespace checks, targeted private-key/AWS/Google/GitHub-token patterns, exact status-delta checks and baseline hashes. Only the two shared reference/type files may differ among previously tracked files. All source/test hashes are frozen before final regression; documentation is updated only with results. No staged files; HEAD stays at Phase 11C. The targeted scan is not a universal secret-free guarantee.

## 31. Existing pipeline compatibility

No upload/Raw/Bronze/Silver/DQ, APPEND/UPSERT/SNAPSHOT, trusted-state, profile, semantic, relationship, metrics, analytics, Ask or SQL change. No route, API, frontend or startup integration. Source policies and SCD history remain distinct. `DEACTIVATE_ON_COMPLETE` stays inert; no absence-driven deactivation, worker or source-scale work.

## 32. Existing Gold compatibility

No current Gold writer, schema, artifact, freshness, pointer or consumer changes. Facts in this phase are bounded reference metadata only. Their local rebinding results are neither production fact loads nor publications. Database safety includes Gold and dataset pointer fingerprints.

## 33. Limitations and safety boundaries

The model/source/acceptance/order inputs are trusted caller attestations, not an authorization boundary. No full dimension registry or production fact-candidate enumeration exists. One member history and bounded references fit in memory; there is no source-scale claim, durable transaction, replay journal, checkpoint, publication or distributed recovery.

A late change inside the current open interval but older than a later accepted unchanged observation is REPAIR_NOT_AUTHORIZED/OBSERVATION_COVERAGE_REQUIRED: 11C retained only the highest observation, not a full observation history. Guessing the missing restoration boundaries could rewind current truth. Fully closed tail reopening/reactivation is also deferred. These explicit cases require richer pinned accepted evidence/lifecycle handling, not speculative splitting. Finite historical intervals, before-first and bounded interior gaps are supported as documented. Interior nonhistorical-only changes defer unless explicitly corrected at an existing boundary. No cross-model-version conversion, authoritative inferred history, Unknown member, full FACT loading or global historical event ledger is implemented.

## 34. Review readiness

Ready for 11D review once final frozen-tree regression, development fingerprints and scope/secret checks pass, with the explicit conservative limitations above. No new migration or integration is needed. Reference patch application, inference completion and re-resolution stage immutable local results and expose no partial mutation. No staging/commit/push is authorized in this implementation pass.

## 35. Exact recommended 11E scope

Design versioned pinned accepted-event evidence with actual source acceptance/outcome semantics, stable event identities, bounded projected payloads and checksums. Add an additive control-plane design for durable discovery/outbox, per-source contiguous checkpoints, model/source/resource-generation pins, writer fencing, failure-stage/retry decisions, reconciliation and atomic publication/checkpoint advancement. Preserve existing source successes and Gold consumers; define compatibility/bootstrap extraction and observation-coverage proof before extending deferred repair cases. Prove restart/idempotency, broken-chain and partial-failure behavior in isolated tests. Keep cloud loader/provider resources and consumer migration for later phases, with BigQuery integration in 11F. No 11E implementation is included here.

## Final validation outcome

| Check | Measured result |
|---|---|
| Final focused late-data suite | 85 passed, 0.94 s |
| Earlier combined warehouse suite | 273 passed, 1 existing warning, 83.42 s |
| Final frozen-tree full backend | 327 passed, 3 skipped, 2 existing warnings, 154.79 s |
| Existing 11C / 11B / 11A in final full run | 89 / 74 / 33 passed, respectively |
| Public development database | All 56 table counts/fingerprints unchanged against the immediate 11D baseline |
| Warehouse state | 0030 already applied; configurations/foundation empty; WAREHOUSE_ENABLED=false |
| Implementation freeze | All seven modified/new source/test hashes match `tmp/phase11d-freeze.json` |
| File scope | Two modified shared files and six new files; all other tracked hashes and 240 unrelated baseline entries preserved |
| Quality | Diff, explicit new-file whitespace, targeted secret and static purity checks passed |
| Git / integration | HEAD unchanged at `e119f53ad26e29dfab93ff1a65dd316cbad64de6`; index empty; no commit, push, migration, route or provider work |

The pass-count increase is exactly 85 new late-data tests over the accepted 242-pass baseline. Skips remain unavailable historical ownership-compatibility, semantic-deadline and PostgreSQL model-profile fixtures. The same Starlette TestClient/httpx and Authlib/httpx deprecations account for both warnings. No newly introduced failure or warning.

Final self-review verified as-of business time rather than current/ingestion time; source acceptance under every authority mode; stable predecessor/successor/member identities; no negative/overlapping intervals or duplicate current rows; explicit gap/before-history failures; inference without fake dates/Unknown members; bounded reference selection; pinned correction/rebind behavior; atomic application and idempotency; no pipeline/Gold, persistence, cloud or reliability-layer scope expansion. The implementation is **ready for Phase 11D review**, with conservative observation-coverage, nonhistorical and closed-tail limits in section 33. No 11E work was started.
