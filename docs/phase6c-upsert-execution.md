# Phase 6C: UPSERT execution

Dataset is the processing boundary; workspace is ownership/orchestration scope. APPEND and UPSERT share the existing coordinator in `append_application_service.py` (now exported as `apply_incremental`, retaining the 6B compatibility name), delivery snapshots, application identities, immutable candidates, private artifact adapter and atomic publication. No parallel upload/application system, automatic migration, SNAPSHOT, hard deletion, cumulative Gold rebuild, new dependencies or commit.

## UPSERT semantics and accounting

The immutable policy must name ordered business keys present in the authoritative schema. Normalization/hashes are the existing version-1 tagged SHA-256 helpers, with canonical key/content comparison protecting collision ambiguity. Operational columns do not affect business content. Missing/null keys, unsafe large floating keys, and ambiguous event timestamps are conservatively rejected.

An absent key inserts; matching normalized content is unchanged; changed content updates its current representation. Identical incoming repeats perform one semantic operation and count remaining inputs as duplicates. Conflicting incoming content rejects every ambiguous row for that key. This follows the explicit Phase 6C request and supersedes the ADR's earlier proposal to reject identical repeats as well. Dataframe row order is not record identity or a conflicting-row winner.

Without event ordering, **latest applied delivery wins for current-state UPSERT**. This is publication order, not a claim about business-time correctness. With an explicit event column, typed dates or aware timestamps determine ordering: older input is STALE; equal-time identical content is unchanged; equal-time different content conflicts; newer changed content updates. No timezone is inferred. Date-only strings converted by existing Silver into naive timestamps remain ambiguous for this executor; live tests use explicit UTC `Z` values.

Silver input = valid + Silver rejected. Valid = inserted + updated + unchanged + duplicate + incremental rejected + stale. `conflict_rows` is a subset of incremental rejections (ambiguous key/equal-time conflicts), not another additive bucket. Invalid key/time rejections remain included in incremental rejected. `deactivated_rows` stays NULL. Real UPSERT zero updated/unchanged counts are meaningful; APPEND retains NULL for those fields.

## Lineage and private ledger

UPSERT state stores latest upload/application/policy/rule IDs, immutable original insertion upload/application/policy/rule IDs, and insertion/change timestamps. Updates retain original insertion lineage; unchanged/stale/conflict records retain their current representation/lineage. Configured business event time remains a business column. All operational lineage has reserved `_datarise_` names and stays out of business-schema hashing and frontend field displays.

Each immutable candidate has a private checksummed record-level outcome ledger covering every valid Silver input once: row index, outcome/reason, key/content hashes, previous content hash, application/upload/policy/rule IDs and application timestamp. It records inserts, updates, unchanged, duplicates, rejected conflicts and stale input without logging full row values. Candidate metadata links previous/resulting state; historical immutable state snapshots preserve representations needed to reconstruct changes. There is no CDC/SCD2 system or row-diff browser. Hashes remain private and can still represent sensitive identifiers, so they are stored in the private data plane rather than returned to UI/AI.

## Validation, publication and failure

Before publication, candidate readback verifies readability, all checksums, exact business/operational schema, owned upload/application/dataset/schema/policy pins, input outcome coverage/accounting, valid unique current keys and changed-record hash/lineage consistency. The engine also validates the entire previous/candidate key set, unchanged/retained representations, preserved original insertion lineage and exact change timestamps before serialization. Current trusted state is independently verified before indexing. Result rows equal previous rows plus insertions; updates do not increase row count. Candidate artifacts never overwrite current or historical objects.

Dataset advisory locks span reading, calculation, private S3 writes, validation and transaction COMMIT. Contending calls fail safely and retry against the latest head. Existing metadata CAS additionally rejects stale publication. Head, published state, application result/metrics and analytics STALE commit together. Pre-commit failures preserve the old head. Post-commit response loss retries return the original saved result without a second update/state version. Successfully archived applications remain historical/idempotent; archived unapplied deliveries cannot execute. Successful Silver and pinned manifests are reused during recovery.

Migration `0015_upsert_outcomes.sql` adds nullable nonnegative `conflict_rows` and `stale_rows` only. Existing history/success protection triggers cover them. No new control-plane table is needed: the immutable outcome artifact plus existing application/state lineage is the durable change ledger.

## CSV event configuration discovery

Existing Bronze profiles CSV datetime text as TEXT, while explicitly approved Silver `DATA_TYPE=DATETIME` can convert it. Policy/read models now expose event columns whose authoritative profile is date/time **or whose immutable approved rule snapshot explicitly converts to DATETIME**. This makes the existing conversion usable without parsing/inference in the policy editor or changing Bronze. The executor still validates actual typed Silver values and schema compatibility on every application.

## User interface and security

Contract explains UPSERT, ordered/composite business keys, change-ordering column and the no-event-time limitation. Invalid/missing/repeated key columns show a warning and prevent save. Published-state policy changes remain blocked until a future migration flow. Overview reads the current head, its actual policy and latest applied filename/operation counts; application creation order does not override publication order. Analytics freshness uses the backend STALE marker. Processing shows Dataset Update, insertion/update/unchanged/duplicate/conflict/older-update outcomes, rule/policy/state versions, application timestamps and contextual retries. Per-delivery dataset count is the result at publication; Overview is the latest head. Gold remains delivery-level output and may include rows excluded by incremental semantics.

APIs accept owned dataset/delivery IDs and resolve policy, manifest and state internally; no client-supplied paths/hashes/head. Public results contain safe IDs/versions/counts, never storage paths/row hashes/values. Logs include correlation/dataset/delivery/application/policy/source/target version, event-ordering presence, outcome counts and duration without source rows.

## V1 limits

Full-state Pandas is O(current rows + incoming rows) indexing/classification plus full Parquet read/write; current frame, row dictionaries, lookup and candidate coexist in memory. No workload maximum or large-scale benchmark is established. No streaming, partition optimization, Spark/Delta dependency or worker queue. Session locks need live connections; future distributed workers require leases/fencing. Staged/orphan artifacts are retained; cleanup and explicit replays/migrations remain deferred. Precision lost by upstream CSV/Silver conversion cannot be restored here. Existing development principal is temporary; this phase does not add production authentication.

## Files

New: `app/processing/upsert_engine.py`, `migrations/0015_upsert_outcomes.sql`, `tests/test_upsert_execution.py`, `frontend/src/features/datasets/contracts/policyValidation.ts`, `tools/phase6c_live_check.py`, this note, live result JSON and browser evidence.

Backend updated: `app/api/incremental.py`, `app/db/incremental_repository.py`, `app/services/append_application_service.py`, `app/services/delivery_execution_service.py`, `app/services/incremental_policy_service.py`, `app/storage/incremental_artifacts.py`, `tests/test_incremental_foundation.py`.

Frontend updated: API types `incremental.ts` / `rules.ts`; `IncrementalPolicy.tsx`; ingestion `ApplicationMetrics`, `IngestionBatchCard`, `ProcessingDetails`, `ProcessingResult`, `ProcessingStages`, `processingPresentation`; Dataset Overview/Analytics; `tests/incremental.cjs` and `tests/processing.cjs`. ADR 0001 updated with explicit Phase 6C semantics. Existing untracked Phase 6B live helper preserved.

## Validation

- Full backend suite: **198 passed** in 611.25 seconds, using `.venv/Scripts/python.exe -m pytest -q` with `RUN_DB_TESTS=1` because `uv` is unavailable. One existing Starlette/httpx deprecation warning. Final focused suite after strengthening transition lineage validation: **22 UPSERT cases passed** in 108.29 seconds.
- Frontend lint/build and all nine lightweight suites pass, using the installed Program Files npm CLI via Node because the existing npm launcher is broken. No dependency installation.
- Migration 0015 applied to development PostgreSQL. Existing policies/state/history are not backfilled or rewritten.
- Live workspace **12**, Customers UPSERT dataset **16**, Event Ordering dataset **17**, uploads **51–56**. First customer delivery: 3 inserts; second: 1 insert/1 update/1 unchanged, current count 4; retry: same application/result; conflict: 2 rejected, no C5 and count remains 4. Older UTC event: 1 stale, Pune preserved; newer UTC event: 1 update, Bangalore published.
- Six checksummed historical S3 state snapshots read back and verified, including actual synthetic customer values and original/latest update lineage. Safe evidence: `phase6c-live-results.json`. Existing APPEND datasets were not modified.
- Browser desktop Processing/Overview and 390px mobile Processing/Contract checked. Correct counts, rule reuse, source/result versions, late-update warning, current count, analytics STALE and disabled policy change persisted after refresh. Switching datasets selected the correct authoritative state. DOM width matched scroll width (375px), with no horizontal overflow. Viewport override reset. Evidence: `docs/screenshots/phase6c/`.
- Live business-rule answers are explicitly human-approved for these dedicated test datasets: customer_id required, updated_at DATETIME using UTC input, and Silver ALLOW if duplicate review asks. No existing business rules are changed.
- Diff reviewed for immutable artifacts, idempotency, stale-head safety, retained lineage, key uniqueness, metrics, private paths and phase scope. Git whitespace checks pass. No commit; no SNAPSHOT or Phase 6D.
