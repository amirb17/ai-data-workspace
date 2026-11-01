# Artifact publication and interruption recovery contract

**Recommendation: NO-GO for real Bronze/Silver/Gold in the EC2 worker.**
This is a safety design and offline reference-model verification, not a live
publication migration. Shared execution remains fail-closed. No processing
algorithm, advisory lock, Exact V2 gate, migration or production service changes.

## Verified stage map

| Stage | Storage changes outside SQL | Current metadata publication / recovery |
| --- | --- | --- |
| [Bronze](../app/processing/bronze_processor.py) | Raw GET; lexical Parquet PUT, then ordinary Bronze Parquet PUT to fixed file-scoped keys. No conditional write. Both happen before service metadata registration. | [`start_processing`](../app/services/processing_service.py#L62) separately commits attempt creation, schema hash, profiles, summary, dataset/version association, rule initialization and success. Hash reuse is an early-return condition, not proof all these writes completed. File-scoped abandoned Bronze attempts do not have the delivery-lock recovery used for Silver/Gold. |
| [Silver/quarantine](../app/processing/silver_processor.py) | Full Bronze GET; conversion/validation; Silver PUT and optional quarantine PUT to fixed version/file/rule-scoped keys. Exact Parquet writes additionally read back/validate. These PUTs are outside both service transactions. | Transaction 1 creates attempt + SILVER_PROCESSING. After writes, transaction 2 registers DQ/issues, success and READY_FOR_GOLD. Exact Silver computes stored-byte SHA-256 before DQ registration; ordinary Silver normally leaves it NULL. Existing DQ/rule-pin reuse does not reread artifact bytes. |
| [Gold base/marts](../app/processing/gold_processor.py) | Full successful Silver GET; base PUT, then each mart PUT at deterministic version/file/rule keys. Attempt ID is data lineage, not a key component despite an “attempt-specific” comment. | Transaction 1 records attempt + GOLD_PROCESSING. Transaction 2 records/repairs run, artifacts, semantic models/columns and success. Existing completeness checks catalog metadata, not stored object bytes. A zero-valid-row result is an explicit SUCCESS_WITH_WARNINGS skip, not a failed/missing artifact. |
| [Incremental](../app/storage/incremental_artifacts.py) | Delivery snapshots, candidate data, outcomes and manifests are PUT before their corresponding SQL registration/publication. Writes use `IfNoneMatch='*'`, read-back and checksums; candidates use unique prefixes. Validation rereads bytes. | [`apply_incremental`](../app/services/append_application_service.py#L438) commits RUNNING, delivery manifest and STAGED metadata separately. Final transaction validates/publishes candidate, updates head/application and marks analytics stale. `_publish_metadata` locks rows and checks expected predecessor/policy/schema. A successful retry does not rewind the head. No job-token guard is present in this transaction. |

Relevant code: [processing services](../app/services/processing_service.py),
[incremental publication](../app/services/incremental_application_service.py),
[repository transactions](../app/db/database.py),
[delivery recovery lock](../app/services/delivery_execution_service.py).
No object deletion/abandoned-candidate cleanup was found in these stage paths.

Existing [0036](../migrations/0036_exact_delivery_provenance.sql) permits exact
delivery manifests and nullable Silver checksum evidence. [0037](../migrations/0037_protect_silver_checksum.sql)
protects successful non-null Silver checksum/DQ-attempt evidence against SQL
mutation/deletion. These protect evidence, not the bytes at a mutable object key.
Historical NULL checksums must not be fabricated or silently treated as verified.

All findings above are from current code inspection and prior/current offline
contract probes. No live storage state, infrastructure policy, corruption,
performance or crash recovery was observed or tested against AWS.

## Critical risks, ranked

1. **Critical: stale writes can change visible bytes.** Fixed-key PUTs occur
   before SQL publication and cannot be rolled back by a SQL transaction.
   A check before PUT is not a fence against lease loss during the call.
2. **Critical: publication transactions do not fence the processing job.**
   Existing `finish_job` only fences later job metadata. Session advisory locks
   coordinate live sessions but cannot prevent an old process finishing storage
   I/O after losing its lock connection or lease.
3. **High: completion evidence is incomplete.** Bronze hash alone, matching DQ
   metadata and Gold catalog completeness cannot establish full byte integrity.
   Job records do not yet have durable stage-intent/receipt associations.
4. **High: recovery and cleanup can race publication.** Age/lease expiry does
   not prove a writer has stopped. A list-and-delete sweep can remove a candidate
   another worker is adopting unless retirement and publication serialize.
5. **High: historical readers and records need compatibility.** Silver and exact
   lexical readers currently derive Bronze keys. Existing DQ/Gold rows retain
   artifact paths, and historical/warehouse references must remain readable.

## Minimal compatible publication design (proposed)

### Durable intent and candidate registry

Add a separately reviewed additive metadata design, not edits to migration 0041:

- A server-owned **stage intent** pins stage, input/source receipt or Raw hash,
  file/association/schema, approved rule version and relevant DQ/policy/planner
  version. A new policy/input creates a new intent. A retry preserves the intent.
- A **candidate** has a random UUID, intent, writer attempt/claim identity,
  manifest key/hash and lifecycle PREPARING/VERIFIED/COMMITTED/RETIRED. Multiple
  attempts can have different candidates. Never put lease tokens in object keys,
  manifests, public receipts or logs.
- A unique **committed receipt** per intent links a selected candidate and the
  corresponding DQ/Gold run/attempt or Bronze publication. Receipt history is
  immutable. Selection changes are explicit new intents/generations, not an
  overwrite of an old receipt.

Bronze's reusable artifact identity is physical file + immutable Raw identity +
processor/schema version, while job authorization remains dataset/workspace
scoped. Dataset association/rule initialization is a separate logical obligation;
do not turn the entire workspace or every upload of that physical file into a
different physical Bronze source. The precise additive table layout/uniqueness
and lock order require schema review; this task creates no migration.

### Write, verify, publish

1. Under existing authorized scope and dataset → lifecycle → delivery locks
   where applicable, admit a bounded chunk with the current lease. Keep the
   physical-file active-attempt protection for Bronze. Do not add blind recovery
   that clears another upload's live file-scoped attempt.
2. Write to a new candidate-specific namespace using write-once objects. Candidate
   UUIDs differ across attempts/reclaims; no stable-key overwrite, token-derived
   key, “latest” object, dual-write alias or copy back to a historical key.
3. Candidate objects themselves become final when referenced by committed
   metadata. There is **no required storage rename/promotion step**. Optional
   local/multipart temporary work remains unpublished until all required uploads
   finish; interrupted responses are ambiguous and must be verified, not guessed.
4. Compute byte SHA-256/size, verify read-back and validate Parquet schema, row
   counts, exact-decimal/lexical requirements, DQ accounting, lineage and the
   full expected role set. Record explicit “no quarantine” or zero-row Gold skip
   evidence where applicable. A missing required artifact is never a valid skip.
5. Write a canonical manifest last, then verify its stored bytes/hash. It pins
   intent/input, candidate, artifact roles/keys/SHA-256/sizes and semantic counts/
   schema/catalog contract. A manifest is evidence, **not publication**.
6. In one short PostgreSQL transaction, acquire a reviewed consistent job →
   candidate/intent → scope/attempt/publication row-lock order. Check current
   RUNNING token and PostgreSQL-time expiry, current ownership/archive state,
   immutable input/policy pins, candidate not retired, receipt uniqueness and
   expected current head/generation. Do not perform network I/O in this transaction.
7. Publish receipt, existing-compatible metadata pointers, DQ/catalog/lineage,
   attempt success and lifecycle changes atomically. For Bronze, move its success
   metadata out of the current separately committed sequence. Preserve rule gates:
   Bronze can end AWAITING_RULES; Silver ends READY_FOR_GOLD; Gold can explicitly
   end SUCCESS_WITH_WARNINGS. Recheck expiry before transaction completion; keep
   the job row locked through commit so a newer claim cannot pass publication.
8. Complete the job with its current token and ACK only after durable completion.
   Receipt publication and job completion may remain separate: completion failure
   is reconciled from the exact receipt without reprocessing. The repository's
   `finish_job` supports the existing borrowed-transaction contract, but combining
   completion and publication still requires reviewed lock/state ordering. Do not
   wrap the shared worker's `execute_job` in a publication transaction and assume
   it joins: that execution boundary deliberately starts an independent Context.

Immutable writes/read-back are an application contract, not a claim that existing
bucket policy enforces immutability. Future deployment must restrict conflicting
writers and define read/version verification. Out-of-band mutation remains an
integrity incident. No AWS policy is inspected/configured here.

### Stage-specific compatibility

- **Bronze:** add a receipt-first resolver for both normal and lexical Bronze
  readers, with unchanged fallback to historical fixed keys when no new receipt
  exists. Never fall back from an existing but invalid new receipt to old bytes.
  Stage/profile completeness must be reconciled, not inferred from schema hash.
- **Silver:** retain existing `silver_path`/`quarantine_path` columns as pointers
  to the selected immutable candidate and attach receipt/checksums to the DQ
  identity. Preserve successful exact checksum evidence and NULL legacy history.
  Explicitly pin upstream Bronze receipt plus approved rule/schema contract.
- **Gold:** publish the complete base/mart/catalog set together. Keep existing
  artifact IDs/paths/history readable. A new committed generation must not repair
  or overwrite a historical successful run's referenced bytes/lineage. Reuse an
  unfinished candidate only after full validation under the same intent; do not
  accept catalog-name equality as physical completeness.
- **Incremental:** reuse its immutable artifact/read-back pattern and existing
  lineage/head CAS; add a job-aware fence to its existing publication transaction
  only in a separately approved task. No Exact V2 gate or manifest is repurposed.

## Failure scenarios: existing behavior versus required contract

| Failure | Current behavior/gap | Proposed safe outcome |
| --- | --- | --- |
| 1. Crash before artifact creation | PROCESSING/RUNNING metadata may remain; delivery lock can recover abandoned association attempts, but not equivalent Bronze file recovery. | No committed receipt/pointer change. Reconcile expired ownership, then create a fresh candidate; no success/ACK. |
| 2. Crash during writing | A subset of fixed-key objects may already be changed; SQL cannot undo them. SDK response uncertainty does not establish stored bytes or full artifact-set completeness. | Candidate stays unpublished. Verify complete stored objects/manifest or abandon it; do not assume a readable half-object or a completed upload. No receipt from an incomplete set. |
| 3. Objects written, SQL publication fails | Silver/Gold/Bronze objects can have replaced existing bytes. Incremental candidates remain orphaned while trusted head is preserved. | Old published pointers remain unchanged. Objects remain unreferenced; retry may adopt only verified, non-retired evidence for the same intent, or produce a fresh candidate. |
| 4. SQL commits, crash before ACK | Job can remain RUNNING despite successful stage metadata; current generic worker lacks durable exact-stage receipt reconciliation. | Resolve committed receipt, verify its pinned evidence and complete the job under the current claim. Do not rewrite artifacts or republish an older incremental head. |
| 5. Duplicate delivery/retry | Job claims reject active duplicates and ACK durable terminal jobs; individual stage reuse varies. | Reuse exact intent receipt; one committed winner. ACK a durably completed job, otherwise safely retry/reconcile. |
| 6. Lease expires during processing | `finish_job` rejects stale completion, but current stage PUTs/publication do not consult that token. | No new chunks; in-flight candidate writes may finish but stay invisible. A publication transaction with invalid/expired ownership must roll back. |
| 7. Old worker finishes after reclaim | Old job token cannot finish/renew; it can still overwrite current fixed keys through a stage call. | Old candidate never replaces published bytes. Old token cannot publish or clear the newer worker's status. A current worker may independently verify/adopt eligible old evidence, never old authority. |

On ambiguous SQL commit, first query the receipt by immutable intent after
reconnecting; do not mark a published attempt failed or delete its artifacts
because the response was lost. This also requires fencing failure-status updates:
an old exception handler must not mark a newer attempt/publication FAILED.

## Abandoned artifact handling

No cleanup is implemented against real objects. Proposed cleanup is opt-in and
only for the new registered candidate namespace:

1. Preserve all Raw, legacy keys, committed receipts/artifacts and every current,
   historical or downstream reference (DQ, Gold/catalog, incremental and warehouse
   lineage). Unknown references mean retain, not delete.
2. After a reviewed grace period and owner/lease checks, lock the candidate row
   shared with publication and atomically mark it RETIRED only if uncommitted and
   unreferenced. Publication/adoption must reject retired candidates. An age test
   or an object listing alone is insufficient.
3. Sweep only that retired candidate's registered prefix/objects. Keep a durable
   tombstone and retry idempotently after cleanup failures. Lease expiry does not
   kill writers: a late orphan can appear after a sweep and needs a later sweep;
   it must never resurrect publication.
4. Define a separate abandoned multipart/local-temp policy and bounded client
   timeouts. Final grace duration, tombstone retention, downstream reference
   inventory and read-retention requirements remain review decisions.

## Narrow helper and verification limits

[`artifact_contract.verify_candidate`](../app/processing_worker/artifact_contract.py)
is a pure, opt-in evidence verifier. It checks a supplied manifest's SHA-256,
version, trusted intent/candidate/stage/input pins, exact required artifact roles,
candidate scope, duplicate fields/roles/keys, declared sizes and stored-byte
SHA-256. It has no SDK, database, publication, authorization, deletion or worker
integration. Required pins/roles must come from a trusted server-side intent,
not from the untrusted manifest itself. The namespace/format is a proposal, not
a replacement for current artifact formats or Phase 11G manifests.

It does **not** validate Parquet semantics/row counts/schema/lineage, prove object
immutability, implement transaction fencing or attest current lease ownership.
Its returned dataclass is not an unforgeable authority. A real publisher must
bind verification evidence to trusted persisted candidate state and perform its
own transactional checks. Download limits must also be enforced by the injected
reader before allocation; the verifier checks already-returned byte lengths.
Empty artifact sets, including a future typed Gold skip receipt, are deliberately
not accepted by this byte-artifact helper and need a separately reviewed outcome.

[`test_artifact_publication_contract.py`](../tests/test_artifact_publication_contract.py)
includes a test-only reference model. Its “atomic commit”, fake time/token fencing,
receipt uniqueness and retirement are **assumptions requiring real SQL design and
isolated PostgreSQL tests**, not implemented production guarantees. Its store
models write-once objects and ambiguous responses, not actual SDK/multipart
behavior or storage permissions. All deletion in the tests is of in-memory data.

## Minimum implementation sequence and review gates

1. Approve immutable stage intent/receipt/candidate identities, new-only namespace,
   reader compatibility and cleanup ownership. Author additive migrations only in
   a separately approved task; do not rewrite historical references or 0041.
2. Implement/review the transactional publisher and recovery lookup with isolated
   PostgreSQL tests: real row-lock races, claim/fence/publication order, rollback,
   ambiguous commit, duplicate receipt, stale failure updates and GC retirement.
3. Add storage writer/read-back abstraction and failure-injected offline tests;
   semantic Parquet validation must precede receipt eligibility. Keep production
   consumers on legacy behavior until receipt-first reads are compatible.
4. Migrate one stage at a time: Bronze completeness + both readers; Silver DQ/
   quarantine pointers; then Gold full artifact/catalog publication. Keep rule
   approval, stage state transitions, Decimal/lineage and Exact restrictions.
5. Only after storage/publication fences, durable receipt reconciliation, SQS
   visibility/redrive, crash recovery and bounded processing/renewal are verified,
   consider explicitly approving a real EC2 executor. EC2 placement alone is not
   a safety fix. Current recommendation remains **NO-GO**.

## Validation and exact change scope — 2026-11-01

Initial offline run: **135 passed**, 0 failed/errors/skipped, 1.17 s.
Final offline run after adding truncated-byte rejection: **136 passed**,
0 failed/errors/skipped, 1.73 s. Runs overlap and must not be summed.
The final run includes 24 new artifact-verifier/reference-model cases, 33
container-worker, 25 Lambda handler, 18 adapter, 20 boundary, 12 execution-plan
and 4 existing rule-gate cases.

```powershell
uv run --no-sync python "$env:TEMP/run-lambda-plan-offline.py" tests/test_artifact_publication_contract.py tests/test_container_worker.py tests/test_lambda_worker.py tests/test_lambda_worker_service.py tests/test_execution_boundary.py tests/test_lambda_processing_plan.py tests/test_processing_rule_gate.py -q --tb=short -ra -p no:cacheprovider --junitxml="$env:TEMP/artifact-publication-final-results.xml"
```

The retained runner blocks actual SDK/session/storage and PostgreSQL connections
before collection, disables DB tests and removes Exact overrides from its process
environment. It modifies no application configuration. No database tests or live
storage tests were run in this task; no real publisher was implemented.

Added: this document, `app/processing_worker/artifact_contract.py` and
`tests/test_artifact_publication_contract.py`. Changed only the navigation link
in `docs/processing-jobs-foundation.md`. Existing production services, worker
defaults, repository, locks, algorithms, gates, migrations and unrelated files
remain unchanged by hash. No real cleanup, stage execution, schema migration,
staging, commit, push, resource provisioning or deployment occurred.
