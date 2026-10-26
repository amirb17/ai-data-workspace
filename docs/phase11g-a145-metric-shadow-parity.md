# Phase 11G A1.4 + A1.5: governed Metric shadow and differential parity

Implementation checkpoint: `bb1c5e55ef3550ac577abaf4c0277be3cd251c46` (HEAD and origin/master).
Final local implementation/validation is complete. Status: `A1.4_A1.5_FREEZE_READY`. Pandas remains authoritative and the shadow flag remains OFF.

## Execution and rollout

`MetricShadowService` receives the existing execution plan, authoritative Pandas result,
and trusted warehouse bindings. `execute` first uses the unchanged Pandas engine;
`compare` can observe an already computed trusted result. The refresh integration runs
after the FRESH Pandas result commits. Shadow outcomes and observer failures cannot
replace that output, alter freshness, or change any existing API response.
Cached FRESH result reuse retains its existing behavior and does not issue another shadow query.

`METRIC_BIGQUERY_SHADOW_ENABLED` defaults to `false`; only `true` opts in.
`BIGQUERY_ENABLED` does not enable this consumer. No provider/ADC construction occurs
when disabled. Evidence stays internal/service-level: immutable typed outcome,
plan/engine/compiler/comparison signatures, publication pins, reservation, job,
processed/billed bytes, duration and mismatch categories. Logs contain technical
identifiers and publication revisions, never raw SQL, business values, source rows or email.
There is no evidence migration, API field, queue, worker, scheduler or automatic retry.

## Eligibility, governance and recovery

All sources must pass the existing owned binding resolver: exact state/model/version,
registered resource/generation, current publication/revision, configuration/schema/source-vector
hashes, immutable serving checksum and materialization receipt. Physical tables must match
`m<model>_v<version>_g<generation>_p<publication>_r<revision>` in the owned workspace dataset.
Generation-only compiler inputs remain backward compatible for old generation-only compiler
fixtures, but the shadow service rejects them. Missing/stale receipt or publication fails closed.

The existing compiler feeds central `execute_governed_query` with consumer `METRIC`:
provider estimate -> durable admission -> fresh estimate -> ACTIVE revalidation ->
claim -> ExecutionPermit -> bounded provider execution -> durable usage completion.
Review, hard budgets and concurrency gates remain effective. SQL/Ask/Analytics identity is unchanged.
The requested transport bound is 101 rows, enabling detection of the existing 100-group truncation.
Payload and byte caps, exact table metadata/label verification and provider job identity remain enforced.

A response lost after claim yields RECOVERY_REQUIRED and retains the original reservation.
Repeated execution cannot release or claim an EXECUTING/terminal intent. Explicit recovery
uses central `recover_governed_query` and the existing provider `recover` contract with an
owned reservation lookup; it never admits, claims, releases or executes another job.
NOT_FOUND/RUNNING remain held; FAILED is conservatively accounted; SUCCEEDED settles
that original reservation. Changed pins/intent refuse recovery. Terminal reservations do not
provide a new execution permit; this seam does not add terminal-result replay caching.

## Semantic contract and compiler version

Pandas engine identity is `PANDAS_METRIC`, execution version remains 1, and plan signatures
are unchanged. Metric compiler version is deliberately 2 for the corrected arithmetic and decoder
contract. The generic compiled-query contract narrowly accepts native integer versions 1 and 2;
other existing compilers remain version 1.

SQL integer SUM/products use BIGNUMERIC intermediates to avoid INT64 overflow. Integer logical
results decode to exact Python integers and retain existing large-integer string serialization.
Pandas AVG intentionally produces its legacy FLOAT64 result: numeric SUM converted once to float,
then divided by non-null COUNT. The compiler reproduces that result contract; warehouse FLOAT
measures remain forbidden. Integer true DIVIDE likewise retains the existing float result.
Decimal DIVIDE retains safe NULL/zero handling. No comparison converts Decimal through float.

The existing Pandas evaluator applies formula scale even when scale=1, so Decimal result
values use the ambient Decimal context (normally 28 significant digits). Decoder values
reproduce that final canonical behavior. Decimal group keys retain their original precision.
Canonical numeric comparison removes insignificant zeros without rounding, distinguishes
boolean/NULL, and compares exact canonical MetricScalar values with no tolerance. Numeric-looking
string labels remain labels. Comparison also checks shape, keys/order, input coverage, total
groups, time buckets, display hint and truncation. Hashes include normalization version and
Decimal context precision/rounding. Raw business results are not included in comparison evidence.
Malformed aliases, metadata types/counts, transport truncation and missing join coverage reject
rather than becoming matches. Known completed query usage is recorded even if decoding fails.

## Differential parity matrix

The cloud-free oracle executes the actual generated GoogleSQL after SQLGlot translation in
existing DuckDB, through the real BigQueryReadProvider and its strict SDK fake. BIGNUMERIC uses
a deliberately bounded test decimal representation. DuckDB division is DOUBLE, so Decimal division
uses independently specified native-result fixtures (including 38 fractional digits); it never
copies the Pandas expected result into a provider response. Real PostgreSQL bridge tests read
actual fake-materialized immutable publication rows, not logical input frames. This establishes
local semantic/transport-contract evidence, not a claim that live BigQuery ran.

| Area | Cases / expected behavior |
|---|---|
| Scalar formula matrix | COUNT, COUNT_DISTINCT, SUM, AVG, MIN, MAX, DIVIDE, MULTIPLY composition; normal duplicates, NULL-heavy, empty and one row |
| COLUMN / MULTIPLY | COLUMN leaves compose the existing valid formula trees; exact integer/Decimal products, NULL propagation; no new row-valued Metric capability |
| Decimal | positive/negative/scale-equivalent inputs, exact integers beyond float precision, aggregate/product beyond INT64, independent Decimal divide references |
| NULL / empty | row COUNT, NULL-excluding COUNT DISTINCT, empty aggregates, empty scalar/grouped, NULL dimension/time |
| Groups | one/two/three dimensions, repeated/NULL keys, deterministic ordering, 99/100/101 groups and truncation |
| Time | DAY/WEEK/MONTH, UTC and Asia/Kolkata rollover, year/month boundaries, Monday-start week, DATE and TIMESTAMP |
| Relationships | one-hop dimension group and ENTITY grain; missing physical join coverage rejects |
| Publication integrity | duplicate current dimension rows rejected by existing physical decoder; exact serving receipt/pins; label drift rejects |
| Failure isolation | match/mismatch/ineligible/recovery/provider error/hook exception preserve committed Pandas response |
| Governance | METRIC accounting, review/hard/concurrency rejection, fresh estimate drift, lost response, one-job recovery, current ownership |

### Explicit limitations (classification H)

Legacy FLOAT measure inputs are ineligible; warehouse modeling is not loosened. Unresolved filters,
unsupported grain/relationship/time semantics retain existing rejection. Timestamp input timezone
must match the approved interpretation; no guessing. Numeric arithmetic outside the jointly
provable range/scale/Decimal-context envelope is explicitly `NUMERIC_PRECISION_UNSUPPORTED`.
For example, Pandas Decimal SUM of `[1e28,1,-1e28]` can be 0 while exact distributed SUM is 1.
A conservative digit/intermediate-bound check prevents claiming portable parity for that case;
Pandas still returns its unchanged result. MIN/MAX and COUNT DISTINCT do not require the same
reduction precision cap. No tolerance or Pandas rewrite hides this difference.
An enabled synchronous shadow adds latency/cost, so it remains off by default. No live cloud
validation was requested or performed; existing three live opt-in skips remain separate.

## Failure classification during development

- A: integer SUM/product overflow and AVG/integer divide result-contract differences corrected in compiler v2, with differential cases.
- B: native date bucket representation and strict result/metadata typing corrected in decoder, with calendar/malformed-result cases.
- F: SQL-oracle AST table replacement initially lost aliases; fixture corrected. New ownership test initially expected PermissionError instead of the existing ReservationConflict. Refresh isolation fixture initially rejected existing interrupted-attempt cleanup; fixture narrowed.
- G/H: existing Pandas Decimal context sensitivity preserved and explicitly classified nonportable; never forced into a match.
- No production lease, auth/CSRF, publication-validation or budget invariant was weakened.

## Exact later staging allowlist (16 files)

Modified:

- app/db/query_governance_repository.py
- app/processing/metric_execution.py
- app/processing/metric_warehouse_compiler.py
- app/query_governance/providers/base.py
- app/services/governed_query_service.py
- app/services/metric_warehouse_binding_service.py
- app/services/query_governance_service.py
- app/services/workspace_analytics_service.py
- tests/test_query_provider_contract.py

New:

- app/processing/metric_shadow.py
- app/services/metric_shadow_service.py
- tests/metric_shadow_fixture.py
- tests/test_metric_shadow_parity.py
- tests/test_metric_shadow_service.py
- tests/test_metric_shadow_postgres.py
- docs/phase11g-a145-metric-shadow-parity.md

Do not stage the 219 pre-existing dirty entries, tmp/, generated evidence, screenshots,
historical modified/deleted tests, old tooling, prior phase artifacts or any file outside
this allowlist. No staging/commit/push is authorized in this implementation task.
Recommended later commit: `feat: add governed metric shadow execution and parity checks`.

## A1.6 recommendation (plan only)

Review the frozen local evidence and portability limitations; separately authorize opt-in
live validation if desired. Development public still lacks migration 0034; any application of that migration and real serving readiness require separate authorization before enabling shadow there. Then design a narrowly scoped Metric provider switch with explicit
readiness/type eligibility, deterministic recovery, observable fallback rules and unchanged
Metric definitions/signatures/API. Keep rollout off until that separate approval. Analytics,
Ask and SQL remain separate migrations. A1.6 implementation has not started.

## Final evidence / requested report

The count below separates overlapping focused evidence from the final broad count; focused counts must not be added together as independent test totals.

| Requested item | Result |
|---|---|
| 1. Preflight | Completed before implementation: status/log/HEAD/origin/diff check; baseline recorded. |
| 2. HEAD/origin | Both remain `bb1c5e55ef3550ac577abaf4c0277be3cd251c46`; no Git mutation. |
| 3. Dirty-tree preservation | Before 219 unrelated entries; after 235 entries (16 phase paths). All 219 original statuses and all 953 unrelated file hashes from the 962-file baseline are preserved. |
| 4. Modified files | Nine, listed in the exact allowlist above. |
| 5. New files | Seven, listed above, including this report. |
| 6. Architecture | Dedicated service + pure comparator; thin post-FRESH refresh hook. |
| 7. Feature flag | `METRIC_BIGQUERY_SHADOW_ENABLED`. |
| 8. Default | OFF; BIGQUERY_ENABLED does not opt in. |
| 9. Eligibility | Owned exact state/model/resource/generation/publication/receipt/config/schema/source-vector/checksum pins. |
| 10. Governed path | Existing central orchestration, fresh estimate, durable claim/permit and completion. |
| 11. Consumer | METRIC exclusively; existing other consumer identities preserved. |
| 12. Decoder | Existing decoder extended strictly; no service-level ad hoc row parsing. |
| 13. Comparison | Frozen typed comparison/evidence; deterministic safe signatures/categories. |
| 14. Normalization | Exact canonical numeric/NULL/shape/key/order/time/count/truncation comparison, no tolerance. |
| 15. Decimal | Portable exact cases match; scale canonicalization; conservative context/range limits explicitly ineligible. |
| 16. NULL | NULL-heavy, NULL groups/time, safe division, empty aggregate cases pass. |
| 17. COUNT | Existing row-count semantics (`len(frame)` / `COUNT(*)`), including NULL measure rows. |
| 18. COUNT DISTINCT | Exact duplicates/NULL/empty semantics; no approximate SQL. |
| 19. SUM | NULL/empty and widened integer arithmetic; nonportable Decimal reductions fail eligibility. |
| 20. AVG | Existing Pandas float result contract, including Decimal-object mean; warehouse FLOAT measures remain prohibited. |
| 21. MIN/MAX | Exact selection then existing Pandas final Decimal scale/context behavior. |
| 22. DIVIDE | Normal/zero/NULL numerator/NULL denominator and independent Decimal references. |
| 23. MULTIPLY | Existing valid COLUMN compositions, integer overflow avoidance and Decimal/NULL cases. |
| 24. Scalar | Eight-operation x four-input matrix passes. |
| 25. Grouped | One through three dimensions, repeated/NULL keys, empty input pass. |
| 26. Dimension join | Existing one-hop dimension grouping passes; no broader join support. |
| 27. Missing coverage | JOIN_COVERAGE rejects malformed warehouse result; Pandas unchanged and known usage accounted. |
| 28. ENTITY | Existing valid ENTITY case matches; existing grain validation stays authoritative. |
| 29. DAY | UTC/source timezone/calendar DATE tests pass. |
| 30. WEEK | Monday start and rollover tests pass. |
| 31. MONTH | Calendar/year rollover tests pass. |
| 32. Timezone | UTC/Asia-Kolkata and DATE vs TIMESTAMP covered; unresolved mismatch ineligible. |
| 33. Empty input | Scalar/grouped empty cases pass. |
| 34. Ordering | Exact key ordering tested; reordered groups classify ORDER_MISMATCH. |
| 35. Group limit | 99/100/101 groups; 100 visible groups with explicit truncation beyond limit. |
| 36. Admission | Review, hard budget and concurrency blocks tested without provider execution. |
| 37. Revalidation | Fresh estimate drift releases ACTIVE only; no execution. |
| 38. Recovery | One original claim/job/usage, lost response and finalization uncertainty, absent/running/failed recovery, current ownership. |
| 39. Table verification | Existing real provider verifies metadata/labels; drift rejects. |
| 40. Pins | Immutable exact table/source pins change intent; stale receipt/publication refuses query/recovery. |
| 41. Pandas compatibility | Actual refresh hook tests preserve committed output/public response and isolate hook exceptions; service and observer tests cover the typed shadow outcomes. |
| 42. Gold | Source loading unchanged; no Gold edits/migration. |
| 43. Analytics | Visible Workspace/Dataset Analytics behavior remains unchanged; all available backend/API regression tests passed. |
| 44. Ask | No Ask code or provider switch; existing consumer-governance contracts and available backend regression passed. |
| 45. SQL | No SQL code or provider switch; existing consumer-governance contracts and available backend regression passed. |
| 46. Focused | Pure 206 passed; final service-only recheck 46 passed after telemetry update; DB 38 passed; warehouse/governance 169 passed. These overlap. |
| 47. Dedicated parity | 71 passed: 69 matching cases (50 scalar, 19 grouped), one deliberate corrupted-result mismatch and one explicit ineligible precision counterexample. |
| 48. Query governance regression | 117 unique focused cases: core 21 + PG 17 + service 8 + orchestration 8 + contract 33 + BigQuery provider 30. |
| 49. Metric regression | 148 focused cases: parity 71 + shadow service 46 + new PG bridge 5 + compiler 18 + binding PG 8. |
| 50. PostgreSQL focused | 38 passed in the binding/governance/bridge gate; warehouse shard also covers provider/serving disposable schemas. |
| 51. Warehouse regression | 148 warehouse tests + 21 core governance tests = 169 passed; compiler/binding additionally covered above. |
| 52. Final broad regression | 1,172 passed and 3 skipped across the exact 1,175-test frozen collection. Completed outcomes were preserved through interrupted/disjoint continuations. One historical UPSERT handshake timeout in a parallel segment passed unchanged in the final isolated continuation; see incident note below. |
| 53. Skips | 3 existing live BigQuery opt-in skips. No new feature test was skipped. |
| 54. Warnings | 2 existing dependency warning types: Authlib httpx deprecation and Starlette TestClient httpx deprecation. Repeated worker imports produce duplicate occurrences of these same types; no new warning type. |
| 55. Public fingerprints | All 68 public table fingerprints exactly unchanged in the final read-only repeatable-read comparison. |
| 56. Catalog definitions | All public catalog definitions exactly unchanged; migration 0034 remains unapplied. |
| 57. Secret scan | Final phase-only targeted secret/provider-path scan passed; no credentials or live-cloud artifacts. |
| 58. Diff check | Final git diff --check and explicit new-file whitespace checks passed; changed Python modules compiled. |
| 59. Limitations | H cases and local oracle envelope above; no live BigQuery proof, terminal-result replay cache or provider switch. |
| 60. A1.4 complete | Yes, within the explicit shadow-only contract; rollout remains OFF. |
| 61. A1.5 complete | Yes, for the documented eligible semantics and cloud-free parity envelope; H limitations remain explicit. |
| 62. Freeze readiness | Yes. A1.4_A1.5_FREEZE_READY. This is readiness for review/freeze, not authorization to stage or switch providers. |
| 63. Stage later | Exactly the 16-path allowlist above, only after separate authorization. |
| 64. Never stage | All 219 unrelated entries; tmp/evidence/screenshots/historical tests/old tooling/prior phase artifacts; everything outside allowlist. |
| 65. Commit recommendation | `feat: add governed metric shadow execution and parity checks`. |
| 66. A1.6 recommendation | Separate approval and controlled-switch plan above; no implementation begun. |

No staging, commit, push, deployment, public migration, live cloud, frontend work or consumer switch performed.

## Final broad-gate incident and evidence

One existing `test_pg_concurrent_updates_reload_latest_head[False]` attempt failed at
`entered.wait(10)` during parallel PostgreSQL load. The three outstanding UPSERT cases
then passed in isolation with the exact frozen source/test bytes and original 10/15-second
synchronization limits. This is classified F, a historical load-sensitive test handshake;
the workload comparison supports that classification, rather than a change to UPSERT
semantics. The original failed attempt is retained, not erased. No production locking,
lease, auth, fixture timeout, or unrelated source was changed. Final successful outcomes
cover every original collected node exactly once; the failed node has an explicit
unchanged serial revalidation.

Ignored local evidence: `tmp/phase11ga145-broad-final.json`, per-segment durable logs/JUnit,
`tmp/phase11ga145-failure-resolution.json`, before/after DB inventories, and
`tmp/phase11ga145-freeze.json`. The freeze manifest contains raw SHA-256 and canonical Git
blob IDs for the 16-path allowlist. None of these generated evidence files should be staged.
