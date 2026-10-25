# Phase 11G-A1.3b — immutable serving publication tables

Current status: **A1.3b_FREEZE_READY**. The final forty-item report and exact
staging allowlist are at the end; earlier interruption qualifications are historical
and superseded by that final decision.

Implementation continuation, 2026-10-24. Committed baseline:
`53baf77a9dd7d373391ad74b4875ea57e65b781b`. The starting local tree already
contained substantial complete-state encoding, request, and provider work. That
work was inspected and retained; the remaining orchestration and publication
pinning were completed against the actual local contents, not reapplied from Git.

## Behavior

The registered generation table remains a provisioning/schema contract. It is not
replaced during serving materialization. Each publication gets a separate TABLE:
`m<model>_v<version>_g<generation>_p<publication>_r<revision>` in the exact workspace
serving dataset. Positive native integer pins and backend-derived names are required.
A previously compiled Metric query retains its exact old publication table even
after a newer publication becomes current.

`rows(plan)` continues to encode affected candidate identities. The separate
`rows_from_states(context, states)` API encodes a complete trusted FACT or DIMENSION
snapshot using the shared physical mapping. It retains inactive FACTs, analytical
roles, exact Decimal/date/time scalars, state hashes, source positions and event
evidence. DIMENSION snapshots retain all SCD2 versions and observation metadata;
duplicate/corrupt members fail with provider errors rather than being deduplicated.
No FACT/SCD semantics are reimplemented in BigQuery.

The serving request validates scope, generation, context, immutable publication,
configuration/schema/source-vector hashes, materialization identity, physical name,
and exact typed → physical → typed round trip. Persisted FACT configuration hashes
remain authoritative for historical raw configurations. The intent discriminator
now describes `WRITE_EMPTY_PUBLICATION_TABLE`, not mutable replacement.

## Provider boundary

`BigQueryTarget.materialize_serving()` creates/verifies the exact publication TABLE
with the existing warehouse/schema labels plus publication and revision labels.
Schema, location, TABLE type, partitioning, clustering, expiration and all expected
labels are checked on existing tables; no ALTER or overwrite repairs drift.

Nonempty snapshots use explicit typed load configuration, CREATE_NEVER and WRITE_EMPTY.
The deterministic load job ID derives from the materialization intent. A lost load
response leaves no PostgreSQL readiness receipt; retry finds the same job and verifies
its ID, project, job type, destination, labels and Mumbai location before accepting it.
Async jobs are awaited before final success validation. No second load is submitted
when the deterministic job already exists.

An independent fresh SELECT, with dry-run admission, cache disabled, billed-byte cap,
row limit and payload-byte limit, reconstructs the exact typed state and canonical
serving checksum. It must match the request and exact row count. Empty snapshots
receive a verified empty TABLE and deterministic `empty_<token>` evidence without
fake business rows or a fake load job. Query telemetry remains separate from load
accounting: unavailable load-job query-byte counters are recorded as zero, not as
a claim that the verification query incurred no processing or billing.

SDK behavior was checked against Google's [LoadJob reference](https://docs.cloud.google.com/python/docs/reference/bigquery/latest/google.cloud.bigquery.job.LoadJob)
and [write-disposition reference](https://docs.cloud.google.com/python/docs/reference/bigquery/latest/google.cloud.bigquery.enums.WriteDisposition).
No mutable DELETE/INSERT serving strategy remains in the provider or fake.

## PostgreSQL orchestration

`ProviderService.materialize_current_publication(resource_id)` is explicit and
backend-only. Reliability publication does not automatically invoke BigQuery.

1. Transaction 1 authenticates current workspace/model/source ownership, validates
   the approved context and registered resource/environment/generation, locks the
   publication head, captures its exact publication/source vector and immutable
   lineage, and validates any existing receipt's metadata pins.
2. Outside PostgreSQL transactions, `_complete_published_state()` reads only that
   captured published candidate lineage through `read_published_candidate()`. Provider
   journal scope, intent and manifest vector, expected Result checksum and physical
   identities are checked. Newest FACT/member identity wins; unaffected identities
   remain, and selected DIMENSION members retain complete history.
3. The provider materializes and independently reconciles the publication TABLE.
4. Transaction 2 repeats ownership/context/resource checks, locks and compares the
   exact publication ID/revision, rechecks immutable pins, and records the receipt
   only if the head is unchanged. The existing 0034 database guard remains intact.

Missing predecessors/intents, cycles, wrong revisions, mismatched operation scope,
environment/generation, corrupt journals/checksums/physical rows, and lineage over
100 publications fail closed. Depth overflow explicitly requires compaction/rebuild.
The complete snapshot remains under the existing 1,000-row and 8 MiB provider caps,
enforced incrementally while merging selected identities as well as before loading.
There is no arbitrary candidate scan or source-event replay.

The affected-key `_prior()` is separate and retains its existing purpose. A narrow
indentation correction puts key selection inside its candidate-state loop; the
previous loop could inspect only the last state returned by a multi-member candidate.
This correction is covered by the multi-identity incremental tests.

Receipt insertion compares every immutable evidence field. A unique conflict rolls
back only its savepoint, rereads the committed row, and returns it only for exact
evidence. Conflicting proof is rejected; no blind ON CONFLICT DO NOTHING is used.
Retries also re-read physical state, so successful job lookup alone cannot hide
serving corruption. The returned durable receipt remains identical on success.

## Metric and governance safety

Metric resolution still verifies the registered base project/dataset/generation
reference exactly. Once the current serving receipt is validated, it returns the
publication-specific physical TABLE, publication/revision, receipt ID, checksum and
row count. Without a receipt for the current head it returns
`WAREHOUSE_SERVING_NOT_READY`. Old receipts remain historical; a newer head cannot
reuse them as current readiness.

CompiledQuery accepts only the existing DataRise generation grammar or its narrow
`_p<positive publication>_r<positive revision>` extension. Arbitrary suffixes, staging
and candidate names remain disallowed. BigQueryReadProvider independently compares
publication-name model/generation/publication/revision pins against TABLE labels,
while preserving project/workspace/Mumbai/TABLE/no-external-source verification.
SELECT-only dry-run source equality, no routines, no views and budget enforcement
are unchanged. Different publication/receipt pins produce different Metric query
identities.

No Metrics execution switch, Workspace/Dataset Analytics change, Ask or SQL consumer
change, Gold pointer change, frontend change, worker, scheduler or deployment is made.
Only compilation/resolution readiness is established for a later reviewed shadow gate.

## Validation and preservation

The starting checkpoint and local origin/master matched. The immediate development
baseline contains **68 public tables**. No migration was added or applied; 0034 is
exercised only by existing disposable-schema fixtures. No live BigQuery jobs or
resources were created for this task, and no normal environment settings changed.

Measured final checks are recorded below. Early focused gates passed 110 tests
before orchestration and 28 PostgreSQL/lineage tests after orchestration. The expanded
provider/compiler/governance gate passed **217 tests** after the final encoder checks.
Coverage includes FACT A/B → A/B(updated)/C, unaffected SCD2 members and history,
unchanged old TABLE contents, inactive FACT preservation, precise Decimal/Unicode,
legacy FACT hashes, lost responses with one physical load, corrupt table/job pins,
empty snapshots, head/ownership races, and exact/conflicting receipt recovery.

Generated inventories, database fingerprints, source freezes and JUnit evidence are
kept under ignored `tmp/phase11ga13b-*`; they are not proposed product changes.
No files are staged; no commit or push is performed.

The fresh public inventory confirms `warehouse_serving_materializations` is absent:
migration 0034 has not been applied to development public. This task deliberately
does not apply it. Development activation requires a separate reviewed migration
gate; successful disposable-schema tests are not a public-database readiness claim.

## Review scope

The complete local phase diff spans 22 paths, including the incoming unfinished
work. Production/service scope:

- `app/warehouse/fact_planning.py` (retained incoming configuration-pin work).
- `app/warehouse/providers/{base,bigquery,fact_physical,model_adapter,physical}.py`.
- `app/warehouse/serving_materialization.py`.
- `app/services/{warehouse_provider_service,metric_warehouse_binding_service}.py`.
- `app/db/warehouse_reliability_repository.py`.
- `app/query_governance/providers/{base,bigquery}.py`.

Test scope:

- `tests/bigquery_fake.py`.
- `tests/test_warehouse_serving_{materialization,bigquery,lineage,postgres}.py`.
- `tests/test_query_{provider_contract,bigquery_provider}.py`.
- `tests/test_metric_warehouse_{compiler,binding_postgres}.py`.
- This report.

The task started with 232 dirty entries: 12 belong to this unfinished phase and
220 are unrelated. Those 220 entries and all other existing file hashes are
preserved, including historical test adjustments and reports. Dependencies,
existing migrations, application routes, consumer execution paths and frontend
files are unchanged.

## Final validation continuation — 25 October 2026

The final affected pure serving gate passed **54 tests** after the aggregate
encoded-byte bound and strict fake cleanup. All 21 frozen source/test hashes
still match; the targeted secret and whitespace checks pass, the index is empty,
and all 220 unrelated dirty entries and other saved file hashes remain preserved.

The earlier full regression process ended during the usage interruption without
producing a final JUnit report. On resumption, a fresh broader run reported a
failure near the dataset-processing/understanding boundary. That run was stopped
for diagnosis, but its process termination did not produce a traceback or final
JUnit report. Three isolated boundary checks then passed (two existing dependency
warnings), so that failure has not been reproduced or explained. No unrelated
implementation or test was changed to suppress it.

The unfinished regression portion was continued with immediate, durable
per-test evidence in ignored `tmp/phase11ga13b-progress.jsonl`. The first 109
collection entries are excluded from that continuation because the prior run and
the three boundary diagnostics already exercised that prefix. This segmented
evidence must not be described as a clean, uninterrupted full-suite pass.

The continuation finished with **941 passed, 3 skipped, 2 existing dependency
warnings**, with zero failures or errors in its final JUnit report. The two
warnings concern Starlette/httpx and Authlib/httpx deprecations. This result is
for the remaining 944 collection entries, not an additional complete 1,053-test
run. The interrupted prefix and three passing boundary diagnostics are reported
separately above; the earlier unexplained failure remains a validation
qualification requiring review.

The final read-only development comparison passed: **all 68 public table row
fingerprints and all captured catalog definitions are unchanged**. The serving
receipt table remains absent from public, confirming that migration 0034 is
still unapplied. The final source/tree check also passed: all 21 frozen hashes
match, all 220 unrelated dirty entries and other saved hashes remain intact,
the 22-path secret/whitespace scan passes, HEAD remains at the approved baseline,
and the Git index remains empty.

Implementation is complete for review. Runtime activation is not claimed:
the unexplained interrupted-regression failure needs review and public migration
0034 requires its own approved gate. No live cloud resources or jobs were
created, no consumer execution was switched, and nothing was staged, committed,
or pushed.

## Final freeze validation: identified fixture failure

The original interrupted stdout did not retain a traceback or reliable test
identity. A new full-collection gate with `--maxfail=1` stopped after 103 passes
at `tests/test_dataset_pending_processing.py::test_dataset_scope_and_api_ownership[False]`.
The assertion raised `KeyError: 'operation'`. An isolated reproduction confirmed
that the POST returned HTTP 403 with only `detail: Refresh the page and try again.`
The test constructed `TestClient(app)` without the existing CSRF setup; the
production auth middleware correctly rejected it before the processing route ran.

Classification: **A — TEST/FIXTURE COMPATIBILITY**. This is a deterministic
historical API fixture incompatibility, not evidence of a warehouse publication
or serving invariant defect. The original incomplete stdout alone cannot prove
which test emitted its `F`; the newly reproduced blocker is now identified and
the final clean gate will supersede that incomplete evidence.

The only test change in this validation is in
`tests/test_dataset_pending_processing.py`: import `enable_csrf`, construct this
test's client through that helper, and assert HTTP 200 before reading the
operation payload. The exact failing parameter now passes. No production code,
auth rule, route or response contract was changed. A saved preimage proves that
the file's existing local work and line endings are preserved outside these three
replacements.

Consequently the review scope is **23 paths**: the preceding 22 paths plus this
historical test compatibility correction. All **21 original frozen hashes remain
unchanged**; the corrected test adds one separately frozen validation hash. Of the
220 formerly unrelated entries, **219 remain byte-for-byte unchanged** and this
one file has only the authorized fixture correction. No unrelated path was
deleted, restored or staged. Final containing-file, subsystem and full-collection
results follow when the corrected gate completes.


## Freeze-readiness decision — 25 October 2026

**A1.3b_FREEZE_READY**

This final decision supersedes the earlier interrupted-run qualification and
previous 941-test continuation report. The original log did not preserve the
historical `F` identity, so no identity is invented for that particular stdout.
The new gate deterministically identified and corrected the CSRF test fixture
blocker and completed the full corrected-tree collection. No current reproducible
failure remains.

One subsequent attempt in
`test_warehouse_reliability.py::test_stale_reconciliation_cannot_publish_after_new_receipt`
returned `FENCED`: its recorded call lasted **119.11 seconds**, exceeding the
unchanged **60-second** writer lease. This is classification **E — environment /
test-harness elapsed-time interruption**. The precise host delay source was not
confirmed; Windows sleep is not claimed as a proven cause. The exact rerun passed
in **10.81 seconds**, with all observed real lease checks active, and the entire
reliability file passed **50 tests in 208.99 seconds** without any production or
test modification. The failed case subsequently passed again in the final tail.
No lease extension, clock mocking, fencing relaxation, skip or xfail was used.

The final regression was resumed after the usage interruption in accordance with
the user's instruction to avoid repeating completed work. Immutable evidence
records every final outcome and verifies the original full collection and frozen
hashes at each continuation. The audit proves **1,050 passed and 3 skipped across
exactly 1,053 unique tests**. The three durable segments preserve 102 passes,
then 758 passes/3 skips, then 190 passes. The timing-failed attempt is retained
as diagnostic evidence; it is not counted as a successful outcome. Its test had
to pass later before the final audit could succeed. This is complete audited
regression coverage, not an assertion of an uninterrupted single process.

Exact gate commands (from repository root):

```powershell
Remove-Item Env:RUN_DB_TESTS -ErrorAction SilentlyContinue
$env:RUN_DB_TESTS='1'
$env:RUN_BIGQUERY_INTEGRATION='0'
$env:RUN_BIGQUERY_QUERY_INTEGRATION='0'
$env:UV_CACHE_DIR='D:\Project\ai-data-workspace\tmp\uv-cache'
uv run --no-sync python tmp/phase11ga13b_freeze_gate.py focused
uv run --no-sync python tmp/phase11ga13b_freeze_gate.py broad
# After interruption/diagnosis, preserve completed outcomes; used twice:
uv run --no-sync python tmp/phase11ga13b_freeze_gate.py resume
```

The runner invokes pytest with `-q --tb=short --maxfail=1` and JUnit output.
Focused gate: **304 passed in 255.69 seconds**. Completing broad tail:
**190 passed in 451.62 seconds**; a single uninterrupted full-gate wall duration
is not claimed. The 304-test evidence remains applicable because all its frozen
source/test files are unchanged; the historical fixture correction was validated
separately and is included in final coverage.

Final evidence: `tmp/phase11ga13b-freeze-final-evidence.json`,
`tmp/phase11ga13b-freeze-broad-complete.json`, the three immutable per-test logs,
focused/diagnostic JUnit files, `tmp/phase11ga13b-lease-resolution.json`,
`tmp/phase11ga13b-freeze-quality.json` and
`tmp/phase11ga13b-freeze-validation-hashes.json`. Generated evidence stays ignored.

| Requested item | Final result |
|---|---|
| 1. Preflight | Expected checkpoint, uncommitted phase scope, empty index and clean diff checks confirmed. |
| 2. HEAD | `53baf77a9dd7d373391ad74b4875ea57e65b781b`; local `master` and `origin/master` match. |
| 3. Preservation | All original entry paths retained. 219 unrelated entries and their saved bytes are unchanged; one historical test received the explicitly scoped compatibility fix. |
| 4. Earlier failure identified | The original interrupted stdout could not recover an exact identity. A new clean gate identified the deterministic blocking API fixture failure below; final complete validation supersedes the original incomplete result. |
| 5. Exact reproduced failure | `tests/test_dataset_pending_processing.py::test_dataset_scope_and_api_ownership[False]`: `KeyError: operation` after a CSRF-rejected HTTP 403 POST. |
| 6. Reproduction command | `uv run --no-sync python tmp/phase11ga13b_csrf_diagnostic.py`; this runs only the exact parameter above with `-q --tb=short --maxfail=1` and observes HTTP error metadata without changing behavior. |
| 7. Reproduction result | Failed deterministically before correction. After correction: exact parameter 1 passed; containing file 9 passed; adjacent API shard 27 passed; both parameters also pass in final regression coverage. |
| 8. Classification | A — TEST/FIXTURE COMPATIBILITY: missing existing CSRF helper. A subsequent reliability attempt is classified E — elapsed-time/test-harness interruption, as documented below; both are resolved in current validation. |
| 9. Production changes in this validation | None. No auth, fencing, provider, route, consumer or business behavior was modified. |
| 10. Test-only changes | `tests/test_dataset_pending_processing.py`: import `enable_csrf`, wrap the test client, assert HTTP 200 before accessing the operation. Saved preimage proves all other local content and line endings are retained. |
| 11. Immutable architecture | PASS. Generation resource remains `m<model>_v<version>_g<generation>`; serving TABLE is `m<model>_v<version>_g<generation>_p<publication>_r<revision>`. Candidate tables remain internal. |
| 12. Lost-response recovery | PASS. Fake commits rows and deterministic load job before raising response loss; retry discovers the job, independently reads back, and performs no second load. |
| 13. Publication isolation | PASS. Later publication uses another TABLE; earlier TABLE rows remain unchanged. |
| 14. Head race | PASS. Second owned PostgreSQL transaction rejects changed head; stale TABLE gets no readiness receipt and current Metric resolution remains unavailable. |
| 15. Receipt idempotency | PASS. Same immutable receipt, one load/receipt; conflicting evidence and conflicting concurrent inserts fail closed. |
| 16. FACT full state | PASS. A/B then B-update/C produces A/B-updated/C with stable FACT keys/checksums, unaffected A and old publication retained; inactive and precise Decimal values preserved. |
| 17. DIMENSION full state | PASS. Unaffected member and complete updated SCD2 history retained, stable member/version keys, validated intervals/current state and old publication isolation. |
| 18. Query governance | PASS. Narrow generated grammar; malformed suffixes, wrong labels/project/workspace/location/type, external tables, views, routines and extra references rejected. |
| 19. Metric binding | PASS. Not ready before receipt; eligible after exact receipt; resolver returns immutable publication TABLE and compiler identity changes with publication pins. |
| 20. Migration/rule regression | PASS. Foundation migration preservation and rule-approval fixtures pass; sorted migration sequence including 0034 is exercised only in disposable schemas. No separately named migration-sequence test is invented. |
| 21. Focused counts | A1.3b/provider/governance/Metric/foundation/rule gate: 304 passed. Corrected exact case: 1 passed; containing file: 9 passed; API shard: 27 passed. Timing follow-up: exact reliability case 1 passed; containing reliability file 50 passed. These layers overlap and must not be summed. |
| 22. Final broad result | 1,050 passed across exactly 1,053 collected identities. Corrected-tree durable segments: 102 passed; 758 passed + 3 skipped; 190 passed. All identities have final outcomes, no unresolved failure and no completed test was unnecessarily repeated across those segments. |
| 23. Skips | 3 explicit opt-in live BigQuery cases; live execution was disabled. |
| 24. Warnings | 2 existing warning types: Starlette/httpx and Authlib/httpx deprecations. The completing invocation emitted 2 warnings. |
| 25. Public fingerprints | All 68 development public table row counts/fingerprints unchanged from the saved pre-implementation baseline. |
| 26. Public catalog | All captured catalog definitions unchanged; no persistent test rows introduced into public. |
| 27. Frozen hashes | All 21 original source/test hashes unchanged; 1 intentionally corrected test has a separately recorded hash, for 22 validated source/test hashes. Report is excluded from source freeze. |
| 28. Secret scan | PASS for all 23 allowed paths, including private-key/token/credential-JSON/ADC-artifact patterns. No credentials or cloud-output secrets introduced. |
| 29. Whitespace | `git diff --check` and new-file whitespace checks pass. Existing unrelated source whitespace is preserved. |
| 30. Unrelated dirty count | 219 untouched entries; the 220th formerly unrelated path is the intentionally corrected historical test, with its pre-existing work preserved. |
| 31. Staged | No; index empty. |
| 32. Commit created | No. |
| 33. Pushed | No. |
| 34. Migration 0034 applied to public | No; serving receipt table remains absent from development public. |
| 35. Live BigQuery contacted | No. |
| 36. Consumer switched | No Metrics, Analytics, Ask or SQL execution switch; no A1.4 or deployment started. |
| 37. Final status | **A1.3b_FREEZE_READY**. |
| 38. Files eligible for explicit staging | Exactly the 23-path allowlist below, including the narrowly required historical CSRF fixture correction. No staging was performed. |
| 39. Files that must not be staged | Every path outside that allowlist. The exact 219 unrelated paths are recorded in `tmp/phase11ga13b-freeze-do-not-stage.txt`; also exclude all `tmp/` evidence/tooling, screenshots, old tooling and prior-phase reports/artifacts. |
| 40. Recommended commit message | `feat: materialize immutable warehouse publication snapshots` |

### Exact staging allowlist — no staging performed

- `app/db/warehouse_reliability_repository.py`
- `app/query_governance/providers/base.py`
- `app/query_governance/providers/bigquery.py`
- `app/services/warehouse_provider_service.py`
- `app/services/metric_warehouse_binding_service.py`
- `app/warehouse/fact_planning.py`
- `app/warehouse/providers/base.py`
- `app/warehouse/providers/bigquery.py`
- `app/warehouse/providers/fact_physical.py`
- `app/warehouse/providers/model_adapter.py`
- `app/warehouse/providers/physical.py`
- `app/warehouse/serving_materialization.py`
- `tests/bigquery_fake.py`
- `tests/test_warehouse_serving_materialization.py`
- `tests/test_warehouse_serving_bigquery.py`
- `tests/test_warehouse_serving_postgres.py`
- `tests/test_warehouse_serving_lineage.py`
- `tests/test_query_provider_contract.py`
- `tests/test_query_bigquery_provider.py`
- `tests/test_metric_warehouse_compiler.py`
- `tests/test_metric_warehouse_binding_postgres.py`
- `docs/phase11g-a13b-serving-materialization.md`
- `tests/test_dataset_pending_processing.py`

Only those paths are eligible for a later explicitly authorized commit. The
219-entry exclusion inventory is exact; all other files, including every ignored
task artifact and the runner scripts, must remain outside staging. Migration 0034
requires its own later approved public-application gate. Freeze readiness does
not activate any consumer or create cloud resources. Stop here; no A1.4 work.
