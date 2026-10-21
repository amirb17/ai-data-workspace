# Phase 11B â€” Typed warehouse modeling and deterministic keys

Implementation review, 2026-10-21. **Status update: the final migration/release appendix below supersedes historical statements about 0030 being unapplied and the no-commit review boundary.** Baseline: `f55597f85d7f5e9ebf2f40a5f3d7f869f5092190`. Provider-neutral metadata and pure key/time functions only. Migration 0030 is **not applied to development**. No staging, commit, cloud resources or 11C execution is included.

## 1. Pre-existing changes

The 240 initial dirty-path entries (239 historical changes plus generated Phase 11A validation evidence) are excluded. Deleted tests, old tooling, screenshots, fixtures and generated reports were not restored, modified, deleted or staged. Task-local `tmp/phase11b-review-evidence.json` records the initial inventory, unrelated file hashes and read-only development fingerprints; it is not part of the implementation scope.

HEAD matches the Phase 11A commit. Read-only preflight confirmed `ai_data_workspace.public`, migration 0029's six empty warehouse tables and `WAREHOUSE_ENABLED=false`. Existing 0029 and its rollback remain unchanged.

## 2. Migration number

Actual root forward migrations end at 0029; pending 0028 remains separately reserved/applied. The next safe migration is `0030_warehouse_modeling.sql`. Its optional empty-only rollback is under `migrations/rollback/`, outside the forward glob. Only disposable schemas receive 0030 in this implementation pass.

## 3. Database objects and representation

New `warehouse_model_configurations` has one immutable row per warehouse model version. Relational identity/pins: workspace, model/version, primary dataset, dataset schema version, load-policy ID, approved rule version, creation actor/time, configuration schema version, validation version and model type. Payloads: strict semantic `configuration`, server-derived `source_schema`, and approved source-rule SHA-256.

Composite foreign keys reuse 11A model/version and source-mapping scopes, the dataset policy's dataset/schema version, and the approved rule snapshot. The creator FK records provenance. Primary-key uniqueness permits one configuration per version. Explicit scope/policy indexes support reads; versioned JSONB shape/type checks and insertion guards reject unknown fields, invalid roles/policies and missing source columns.

Two new trigger functions protect configuration and new approvals. Row insert/update/delete protection, TRUNCATE protection (reusing 11A's guard), and an approval trigger on `warehouse_model_versions` enforce the boundary. No existing source/Gold table, pointer, row or processing function is altered. The approval trigger deliberately strengthens future warehouse approvals only.

Strict typed JSONB keeps FACT/DIMENSION union contracts together without creating many speculative SCD child tables. Relational IDs/FKs provide ownership and source identity; frozen Pydantic contracts and SQL field/enum/role checks prevent arbitrary JSON dumping. The server constructs source snapshots; callers cannot submit their own schema evidence or provider resource IDs. Full validation is a trusted service responsibility, not a PostgreSQL JSON-schema implementation or an RLS boundary.

## 4. FACT schema

`schema_version=1`, explicit `model_type=FACT`, `grain`, optional `event_time`, and `identity_null_policy=REJECT`. Classification is mandatory; semantic/entity suggestions never choose it. Fields from dimension or provider configuration are forbidden.

## 5. FACT grain

`grain.description` must be nonblank and `grain.identity_columns` must be a nonempty ordered list of exact source field names. Retail names are not embedded anywhere. A composite business-event identity can describe an item/event/attempt grain without treating a physical file as an entity.

Grain supplies the future uniqueness constraint and determines what a count or additive measure represents. It prevents duplicate facts only when the future loader enforces uniqueness. Avoiding fanout additionally requires approved many-to-one dimension lookups; avoiding incorrect aggregations requires metric grain/additivity checks. 11B declares and validates grain, but does not claim to have implemented runtime fact uniqueness, joins or metric aggregation.

## 6. FACT identity

One to 32 explicit ordered columns; no duplicates, missing names, technical fields, role overlaps or floating-point identities. Source schema order and input dictionary order are irrelevant. Reordering the **configured** identity is a semantic change that requires a new model version and changes the key encoding. Key components must be non-null at generation time, regardless of source nullability.

## 7. DIMENSION schema

`schema_version=1`, explicit `model_type=DIMENSION`, ordered `business_keys`, `scd_type`, tracked/nonhistorical attribute arrays, optional/required `effective_time` according to SCD type, history coverage, missing-member/null-key and snapshot-absence policies. At least one declared business attribute is required.

## 8. Business-key contract

Business keys are exact ordered source names, scoped and versioned with configuration. Single and composite keys are supported. No inference, sorting, case conversion or cleaning is performed. The validation report exposes each key column's known/unknown source nullability and an unconditional normal-key null rejection policy. An unknown nullability value is not a non-null guarantee.

## 9. SCD1 metadata

`SCD_TYPE_1` declares one logical member with a stable member SK and latest accepted attribute values. Both declared attribute lists can describe mutable fields; neither causes historical versions for SCD1. Optional effective time declares ordering context for a later stale-event algorithm. Attribute contents, application IDs and processing timestamps do not enter the member key. No row update algorithm is implemented.

## 10. SCD2 metadata

`SCD_TYPE_2` requires effective time and at least one tracked attribute. Version identity includes a typed effective boundary. Future validity is half-open `[valid_from, valid_to)`; version closure, current flags, overlap prevention and correction/rekey operations are not executed here. Runtime dimension tables and rows are not created.

## 11. Tracked/nonhistorical attributes

Tracked changes will create SCD2 business versions; nonhistorical changes will not create history solely due to those fields. Keys, tracked fields, nonhistorical fields and the effective/event-time field must be disjoint, including duplicates within each list. A time or ingestion field cannot accidentally become a tracked attribute. 11B validates role declarations but does not compare or mutate rows.

## 12. Time policy

`TimePolicy` contains `column`, `interpretation=UTC|SOURCE_TIMEZONE`, and a required IANA timezone only for SOURCE_TIMEZONE. UTC cannot carry a contradictory zone. The source column must be typed DATE or TIMESTAMP by the pinned source contract/rules; a plausible column name or text sample is insufficient.

`normalize_time` is a pure interpretation helper. A naive UTC input is explicitly interpreted as UTC; a naive SOURCE_TIMEZONE input uses the configured zone and rejects ambiguous/nonexistent DST wall times. Offset-aware instants normalize to UTC; DATE stays DATE rather than an invented midnight instant. This is time interpretation, not SCD execution.

## 13. History coverage

Only `FROM_FIRST_OBSERVED_EVENT` is supported. No declared start, fabricated prehistory or inferred history start is stored. `history_start_known=false` means 11B has no execution/evidence from which to compute the reconstructable range. A future worker must determine coverage from pinned accepted evidence; the oldest date in a current-only file is not automatically proof of complete prior history.

## 14. Missing-member and snapshot policies

`missing_member_policy=REJECT` by default; explicit `INFER_MEMBER` describes a future known-business-key lookup miss. `null_business_key_policy` remains REJECT and UNKNOWN_MEMBER is not supported. There are no inferred/Unknown rows or lookup operations.

`snapshot_absence_policy=KEEP` by default. Explicit `DEACTIVATE_ON_COMPLETE` is allowed only when the pinned source policy is SNAPSHOT with COMPLETE authority. PARTIAL never authorizes absence deactivation. Even a configured COMPLETE policy will need actual accepted deactivation outcomes in a future phase; 11B does not reinterpret source delivery decisions or implement deactivation. APPEND/UPSERT/SNAPSHOT remain independent of FACT/DIMENSION/SCD classification.

## 15. Surrogate-key algorithm V1

`app/warehouse/keys.py` supplies pure `canonical_value`, `canonical_key`, `surrogate_key` and `model_surrogate_key`. SHA-256 over canonical UTF-8 bytes yields 64 lowercase hexadecimal characters. The model's stable UUID namespace comes from 11A, not a new UUID per run/version. Role tags separate FACT, DIMENSION_MEMBER and DIMENSION_VERSION.

SCD1 member keys include only the ordered business identity. SCD2 version keys additionally include the typed `valid_from` identity. FACT keys use declared grain identity. Attributes and ingest/run/file order are excluded. Compatible model versions retain keys because model version numbers and definition hashes are not hash inputs; changing identity columns/order/types or effective boundaries intentionally changes identity and needs future reviewed migration handling. Unknown algorithm versions fail closed.

## 16. Canonical encoding format

The exact array wire format is:

```text
["datarise-warehouse-key", 1, canonical_uuid, role,
 [[field_name, [type_tag, canonical_value]], ...], boundary_or_null]
```

Serialization is JSON with no separator whitespace, `ensure_ascii=false`, `allow_nan=false`, then strict UTF-8. Nested arrays eliminate delimiter collisions. `canonical_uuid` is Python UUID's canonical lowercase hyphenated form. Composite field names and order are part of the bytes. `DIMENSION_VERSION` requires a date/timestamp boundary; other roles reject one. Lists/maps as scalar components and unordered identity sets are rejected.

| Python value | V1 scalar encoding |
|---|---|
| None | `["null"]` for scalar canonicalization; rejected by normal identity generation |
| empty string | `["string", ""]`, a valid distinct identity |
| bool | `["boolean", true|false]` |
| int | `["integer", base_10_text]` |
| Decimal | `["decimal", exact_fixed_point_text]` |
| string | `["string", exact_code_points]` |
| date | `["date", "YYYY-MM-DD"]` |
| aware datetime | `["timestamp", "YYYY-MM-DDTHH:MM:SS.ffffff+00:00"]` |
| float / nonfinite / unsupported scalar | rejected |

The new warehouse format has its own domain/version; it does not change `processing/row_identity.py`. That existing source identity code intentionally groups some numeric types together. Warehouse V1 conservatively distinguishes integer and Decimal tags, so a source type change cannot silently collapse identity. The future loader must enforce the pinned source types before key generation.

## 17. Unicode policy

V1 normalization is **NONE: preserve exact Unicode code points**. No NFC/NFKC, casefold, trim or whitespace normalization is applied to business values. Canonically equivalent composed/decomposed strings remain distinct; so do full-width/ASCII strings and `C001`/` c001 `. This matches the existing source row identity's exact-string policy, not the logical display-name normalization used elsewhere. Invalid surrogate code points are rejected by strict UTF-8 encoding. Changing this rule requires a new key algorithm version.

## 18. Decimal/float policy

Finite Decimal encoding uses exact fixed point, removes insignificant fractional trailing zeroes and normalizes signed zero. It never calls context-sensitive Decimal normalization; precision remains stable under a changed ambient decimal context. Decimal `1` remains type-distinct from integer `1`. All floats, NaN and infinities are rejected for identity.

Bounds protect canonicalization: at most 32 fields, 255 characters per field name, 64 KiB UTF-8 per string value, integer bit length at most 13,600 and Decimal coefficient/exponent bounds of 4,096. These are serialization bounds, not data-quality cleaning rules.

Source integration is deliberately conservative: legacy `schema_fingerprint.normalize_dtype` labels pandas float as DECIMAL. The source-contract adapter maps that ambiguous label to FLOAT for warehouse validation, so ordinary source DECIMAL candidates cannot become keys without precise future type evidence. Exact Decimal keys are supported by the pure typed-schema/key library; no lossy float-to-Decimal conversion is invented.

## 19. Timestamp and timezone identity

Raw timestamp identity values must be offset-aware. Equal instants with different offsets encode identically in UTC with six fractional digits. DATE and TIMESTAMP are distinct domains. No machine-local timezone, current clock or processing timestamp enters a key.

For SCD2 UTC policy, the model wrapper can resolve an explicitly declared naive UTC boundary. For SOURCE_TIMEZONE historical keys it requires a **pinned offset-aware instant**, even though the separate interpretation helper can preview a naive local time. Named-zone rules can change with tzdata updates; recomputing an unresolved local wall time during a later rebuild must not silently change an SK. Future accepted evidence/loaders must retain the resolved instant before key generation/replay. That persistence is outside 11B. Supplying an explicit offset also resolves DST ambiguity without guessing.

## 20. Composite ordering and retry

`(country, id)` and `(id, country)` have different encodings even if they refer to the same dictionary. Reordering source dictionary fields or processing rows does not affect a key. Repeating a call, rebuilding in a separate process, changing attributes or changing ingestion metadata preserves the key when namespace, role, ordered typed identity and SCD2 boundary are unchanged. No sequence, `ROW_NUMBER`, process hash or row ordinal is used.

## 21. Literal test vectors

Durable fixtures: `tests/fixtures/warehouse_keys_v1.json`. Every entry stores typed input, exact canonical UTF-8 text and a literal expected SHA-256. Namespace is `00000000-0000-0000-0000-000000000001`; ordinary entries use DIMENSION_MEMBER.

| Vector | Literal SHA-256 |
|---|---|
| `id="C001"` | `b60ec522cb560ecc822384d9754a2ec9c6d283e7dda51abc98ad60f242dfc511` |
| large integer | `282cff04206b7bd75babb03ca86655d829e095473be0ccfc4b3f16982bf6f301` |
| country + id | `0b82f49f353f36aed93d6c8936dbff9cffad6d38e456541873afe285e24419c1` |
| decomposed Unicode + CJK | `0d81bd6da838caa6c196877a0fc0fffc857247dedebe512f3ab12d843a8f52a0` |
| date | `79c764fcc1e23b38116a65977f19820242adbc72456da07f4fa9cc6a23de3ed8` |
| UTC timestamp | `aed4c5126a81980055109a58741e94095b24b6a1acbab6a8191c3799afd48a60` |
| same instant, +05:30 | `aed4c5126a81980055109a58741e94095b24b6a1acbab6a8191c3799afd48a60` |
| precise Decimal | `ec5de7ac8c233ad2469698e367e913dfe4ab12ebc395af923312960b2fe3fc65` |
| empty string | `367bb3ffad8dd6856cdabb1b7413cdd250a158a1113768c5b3f8c7d76ba3bea9` |
| boolean | `f3a1e94ce7f65b79a6ec2b46b93bb75118eed40ef49f26801c4be3e8bb7a135c` |
| SCD2 C001 / 2026-06-01 UTC | `ab51609380916238099b5418f8ce8aaac3845f04728de20983a924aa3a0ef86d` |

Tests additionally cover NULL/float rejection, field/value delimiter collisions, scalar type distinctions, composite reordering, namespace/role separation, exact Unicode behavior, low Decimal precision contexts, DST gaps/overlaps and a fresh-process literal reference.

## 22. Validator and source pinning

`validate_model` checks typed classification, exact names, uniqueness/disjoint roles, supported identity types and typed time. `validate_source_policy` checks snapshot authority without merging source and SCD semantics.

Services resolve the model version's explicit PRIMARY source, then an exact immutable `dataset_load_policies` ID and approved `source_rule_version`. Dataset/schema scope and schema hash must match; missing/cross-dataset/foreign policy IDs fail. Approved DATA_TYPE rules can supply explicit processed time types; NOT_NULL rules supply known non-null schema declarations. Other nullability remains unknown. The server snapshots resolved source columns/types/nullability and a deterministic rule hash. No S3 reads, profile regeneration or latest-policy lookup is needed.

Before approval, stored configuration is revalidated and the pinned source contract/rule hash checked for drift. Reads use the stored typed snapshot rather than reinterpret old meaning from a new policy/schema head. This also detects the existing rule snapshot table's lack of a universal update guard without modifying that legacy table.

## 23. Immutability and 11A compatibility

The entire semantic configuration is immutable immediately on insertion, including DRAFT. Change configuration by creating a new warehouse model version and explicitly configuring its mappings/pins. Model/version configuration and review history retain 11A protections. The source mapping FK prevents deleting the configured PRIMARY mapping even in DRAFT; it does not implement multi-source transformations.

0030 invents no configuration for existing versions. Old APPROVED 11A rows remain valid and readable as `configured=false`; they are not execution-ready typed models. Once 0030 is installed, new approval requires a typed configuration both in service validation and a database trigger. The service detects 0030 availability so the still-deployed 0029-only metadata foundation continues to operate as before while disabled. No startup migration requirement is introduced.

## 24. Ownership and isolation

All service methods inherit current workspace ownership checks and locks from 11A; model/version IDs are parent-scoped. Primary dataset membership and current dataset ownership are checked before schema resolution. Historical `created_by`/`reviewed_by` are audit identities, not authorization. Transfer preserves old configuration/actor metadata while the new owner gains access and the old owner loses it. Foreign user, guessed cross-model version, foreign dataset and actual foreign source-policy tests cover these paths.

No public API/UI or provider physical references are added. Pure key functions provide no authorization; callers must already own the logical model through backend scope checks.

## 25. Feature flag

`WAREHOUSE_ENABLED` stays false by default and in the actual development environment. Semantic metadata reads/writes require the existing feature gate before database access. Pure schema/validation/key/time functions can be tested while disabled. Tests temporarily enable metadata only within their process/isolated fixtures. No environment file, Google dependency or application router changes.

## 26. Migration verification

0030 is exercised only in generated disposable schemas. Tests preserve populated pre-0030 application/warehouse rows, keep old approved versions unconfigured, verify new table/index validity, enforce configuration/approval guards and test empty rollback/reapply. A nonempty rollback is rejected. Existing 0029 files are unchanged.

The 11A test fixture now applies only the 11A migration prefix before 0029; it previously tried every later root migration before explicitly applying 0029. This sequencing fix keeps 11A baseline tests independent. 11B's fixture then adds 0030 explicitly; the normal full-schema fixtures still apply root migrations in order. No historical deleted fixtures were restored.

Final read-only development checks confirm all six warehouse tables remain empty and `warehouse_model_configurations` is absent. Seven of 55 application table fingerprints differ from the earlier baseline: `auth_sessions` (7 to 8 rows), `auth_rate_limits`, `users`, `datasets`, `dataset_gold_runs` (15 to 17), `auth_oauth_states` (6 to 7), and `auth_security_events` (18 to 19). Their cause is unverified; no rows were restored or removed. Therefore application-data immutability cannot be certified. Hash/count evidence is preserved locally without row values. Migration 0030 remains pending review.

## 27. Focused tests

Run with `RUN_DB_TESTS=1`: `uv run --no-sync pytest tests/test_warehouse_modeling.py -q`. The focused suite passes 74 tests, including verification after the final temporal-key safety change. An earlier combined foundation/modeling run passed 105 tests. The final suite covers the 11 explicit areas requested plus direct database corruption attempts, source rule drift, ownership transfer and snapshot authority. No Google/BigQuery calls occur.

## 28. Backend regression

Run with `RUN_DB_TESTS=1`: `uv run --no-sync pytest -q --tb=short`. The available working-tree regression passed 153 tests with 3 skips and no failures/errors. It includes authentication, existing model behavior, 11A and 11B. This run preceded the final temporal-key safety change, which was subsequently verified by the 74-test focused suite. Known baseline gaps remain the deleted-fixture ownership-compatibility/semantic-deadline modules and one Model PostgreSQL profile test. Counts and JUnit artifacts are recorded in `tmp/phase11b-review-evidence.json`; skips are not represented as full historical-suite coverage.

## 29. Secret, diff and scope checks

`git diff --check`, explicit new-file whitespace checks, targeted private-key/AWS/Google/GitHub-token pattern scanning and unchanged unrelated-file/status hashes are required at completion. No repository-specific scanner was available in 11A. Evidence stays in ignored task-local files; no existing generated evidence is edited. The Git index stays empty and HEAD remains the 11A commit.

## 30. Files changed

Modified:

- `app/db/warehouse_repository.py`: scoped source/configuration metadata SQL.
- `app/services/warehouse_model_service.py`: typed approval guard when 0030 exists.
- `tests/test_warehouse_foundation.py`: deterministic 11A fixture migration prefix.

Added:

- `app/schemas/warehouse_modeling.py`
- `app/services/warehouse_modeling_service.py`
- `app/warehouse/keys.py`
- `app/warehouse/model_validation.py`
- `app/warehouse/time_policy.py`
- `migrations/0030_warehouse_modeling.sql`
- `migrations/rollback/0030_warehouse_modeling.sql`
- `tests/test_warehouse_modeling.py`
- `tests/fixtures/warehouse_keys_v1.json`
- `docs/phase11b-warehouse-modeling.md`

## 31. Pipeline compatibility

Existing upload, Raw/Bronze/Silver/DQ, APPEND/UPSERT/SNAPSHOT, trusted state, profiles, relationships and consumers are untouched. Only the inert warehouse metadata approval path changes after installation of 0030. No frontend files, dependencies, source processing modules, routers or current pointers change. No source data is regenerated.

## 32. Gold compatibility

All existing delivery/cumulative Gold tables, artifacts, freshness/pointers and consumer code remain unchanged. The new configuration does not participate in metric, analytics, Ask or SQL execution. The audit includes existing Gold metadata; `dataset_gold_runs` changed during the validation window as recorded in section 26, so unchanged development Gold data cannot be certified. No cloud/storage operation is necessary for this phase.

## 33. Remaining limitations

Metadata models one PRIMARY source schema with optional existing dependency mappings; no multi-source transform/relationship compilation. No runtime data-row schema validation, grain uniqueness, fact joins, SCD mutations, late/inferred handling, loader, checkpoint, source CDC, publication, replay/backfill or consumer migration. History starts and actual coverage remain unknown until accepted evidence is processed. UNKNOWN_MEMBER is intentionally unavailable. Existing ambiguous DECIMAL source labels cannot be keys. SOURCE_TIMEZONE keys require previously pinned resolved instants. Canonical scalar input is native typed Python values, not arbitrary pandas/NumPy objects or nested structures.

No TB-scale claim, production migration, deployment, cloud resource or warehouse enabling is made. The full historical deleted-fixture suite remains outside available working-tree coverage.

## 34. Review readiness

11B code is ready for review with passing focused/regression tests and file-scope checks. The development-data fingerprint discrepancy in section 26 remains unresolved and must be reviewed before a migration gate can claim unchanged application data. The implementation stops with 0030 unapplied to development, the feature off, no staged files and no commit. The architecture document needs no correction: these contracts refine its provider-neutral model/version/source boundaries without changing the approved architecture.

## 35. Recommended exact 11C scope

Implement provider-neutral, local/reference SCD1/SCD2 transition planning over already validated, pinned typed inputs: insert/update/unchanged classification, tracked-attribute hashes, stable member/version keys, half-open interval closure/new-version patches and conflict/retry invariants. Require resolved immutable business-time evidence and expose explicit correction decisions; never resurrect source-rejected events. Validate no overlapping versions and at most one open current member in the reference result.

Keep late-arriving interval repair/inferred-member execution for 11D and checkpoints/publication/replay/backfill for 11E. No BigQuery loader, physical mutation, consumer migration or frontend work is implied. No 11C implementation was started in this phase.


# Final audit appendix - migration 0030 gate (2026-10-21)

This appendix supersedes sections 26/28/34 on the unresolved discrepancy and final regression status. No implementation, test or migration file was edited during this audit. Only this report and new ignored local audit artifacts were written. Original baseline/final fingerprints and focused/regression XML files were preserved.

## A1. Timeline

Times are IST (+05:30), 21 October 2026 unless explicitly UTC.

| Event | Time / evidence |
|---|---|
| Original preflight | Exact start unavailable; initial status inventory persisted 09:47:21.791. Persistence time does not prove preflight start. |
| Development baseline | Original evidence file created 09:47:26.830, during earlier full regression. |
| Earlier focused / combined runs | Task history records 54 passed/6 skipped, 60 passed, combined 105 passed (89.17 s), then focused 74 passed (38.51 s). Exact timestamps unavailable; do not infer them from overwritten focused XML. |
| Earlier full regression | 09:46:19.639 to 09:49:14.550; preserved phase11b-regression.xml; 153 passed/3 skipped. |
| Final temporal-key change | keys.py mtime 09:48:24.476; matching test mtime 09:48:24.803. Filesystem corroboration, not a separate commit. |
| OAuth / login | State consumed 09:55:58.566; session/user/event at 09:56:00.652. |
| Gold run 16 | 09:56:57.132 to 09:56:58.680. |
| Gold run 17 | 09:58:29.220 to 09:58:30.436. |
| Final focused after resume | 13:47:33.105 to 13:48:07.421; preserved phase11b-focused.xml, 74 passed. |
| Previous final fingerprint | Evidence last written 13:48:43.259; exact preceding SELECT start not recorded. |
| New exact-tree full regression | 2026-10-21T13:52:46.558712+05:30 to 2026-10-21T13:55:24.081712+05:30; phase11b-gate-regression.xml. |

The identified login and Gold mutations happened after the earlier full regression finished and before the resumed focused run. The new session was last seen at 10:16:19.968. Rate limits have no exact write timestamp; bucket 1991729 covers 09:45-10:00, so precise SQL timing cannot be recovered there.

The user directly confirmed in this chat: "Yes, I performed those actions", answering the question specifying the 09:56 login and dataset 16/17 analytics refreshes around 09:57-09:58. This establishes manual attribution independently of writer inference. No request log was available to corroborate the initiator separately.

## A2. Counts and fingerprints

These are original aggregate SHA-256 row fingerprints, not credential/token hashes. Current public fingerprints match the previous final across all 55 tables.

| Table | Baseline count | Baseline SHA-256 | Previous final count | Previous final SHA-256 | Delta |
|---|---:|---|---:|---|---:|
| `auth_rate_limits` | 7 | `866136c2cee3f917c59a6a1d5ea1dc199354aaaec50c01ecd8125b1854ef7c02` | 7 | `6007298c686b39ca4a70a637bc508424674599790c70a546ace4724432ef2143` | +0 |
| `auth_sessions` | 7 | `bd66274bbe4d359a22a0b28dadbd8c928b172cb5730a203207cabce637f6e779` | 8 | `9390aa9d1c24ab7d3e383f8af9957fd232f1a86d9c78d6e30f59c93a9a62e659` | +1 |
| `auth_oauth_states` | 6 | `08ae2bf0c0b55ecde235f1e2d1c1ab54788eb32dc54d5234291394bde9cfbb8d` | 7 | `f0d3d87850f0d8db6c5845c301fb1066e1ef0c5d9c9627b596a94f898c722fb9` | +1 |
| `users` | 2 | `e006e26f760ac5e23a27db16f9d1bd742f50ea2bfacd7d08f8fe3f18872aed5f` | 2 | `46510ee580b68f238dce5625debdfb3b54d4735c98510aff924410c141a2e616` | +0 |
| `auth_security_events` | 18 | `99c274db10bc44aee7b45b93498c6dbbef78feda09e99d4328806dcfbfbb679c` | 19 | `cb020ce9350bf595caa3ca594736ee56dac9bde6e33da5779dd9a543ccd83ebd` | +1 |
| `datasets` | 33 | `d9e4e4d897d9efc4fcd2d4b8bd6ab22b9aead068a7763cc1c3de88f38a7ae28a` | 33 | `e1d030a55258b71421a7063abc70a5cd7dd6c6f72d351f498eb8f1868fd88cd5` | +0 |
| `dataset_gold_runs` | 15 | `5915652153cf7f33cde6fccfb6107f5fe631bf2e87b130030a9d5948b48f1339` | 17 | `7f7f4d2a54fc4b7bb4864b882012c6582fadc4b0d0734294852ba2ef36c00da1` | +2 |

## A3. Changed-table classification

| Table | Observed change | Likely cause | Phase 11B capable of this public change? | Evidence / disposition |
|---|---|---|---|---|
| auth_sessions | Added one session for user 34965 at 09:56:00.652, last seen 10:16:19.968; seven older rows unchanged. | B: confirmed interactive login. | NO on audited paths. | SELECT excluding new session reproduces baseline count/hash exactly. Existing issue_session/session writers; explained. No token/digest exposed. |
| auth_oauth_states | Added one state consumed 09:55:58.566, expires 10:05:52.323; six older rows unchanged. | B: confirmed Google login sequence. | NO. | SELECT excluding new dated state reproduces baseline hash. Existing Google start/callback writer; explained. No state value exposed. |
| auth_security_events | Added event 19, user 34965, LOGIN_SUCCESS at 09:56:00.652; prior 18 unchanged. | B: confirmed login. | NO. | Excluding event 19 reproduces baseline hash; timestamp matches session/user; explained. |
| auth_rate_limits | One existing bucket window 1991656 to 1991729, hits remains 1; no rows added/removed. | B: login rate-window rollover. | NO. | Projecting just that window to 1991656/hits 1 reproduces baseline hash. Existing rate() resets count on new window; Google start uses 900 s. Old/new windows start 20 Oct 15:30 / 21 Oct 09:45; explained. Bucket identity withheld. |
| users | Count 2 unchanged. Only user 34965 last_login_at and updated_at changed from 20 Oct 15:43:53.515 to 21 Oct 09:56:00.652. | B: login plus auth_user_updated_at trigger. | NO production writer; isolated fixture inserts synthetic users. | SELECT projecting those two timestamps to prior session time reproduces entire users baseline hash. All other fields unchanged; explained. |
| datasets | Count 33 unchanged. Dataset 16/17 current_gold_run_id NULL to 16/17 and state_analytics_status STALE to FRESH only. | C: confirmed manual analytics refresh. | NO production writer; isolated fixtures insert/transfer synthetic datasets. | SELECT projecting exactly those four values reproduces entire datasets baseline hash; explained. |
| dataset_gold_runs | Added SUCCESS runs 16/17; prior 15 rows unchanged. | C: confirmed manual analytics refresh. | NO; no Phase 11B Gold invocation. | Excluding IDs 16/17 reproduces entire baseline hash; existing pinned states and pointer changes match refresh writer; explained. |

All projections were SELECT-only JSON expressions within READ ONLY transactions, never restoring or altering rows. Exact full aggregate matches, subject to SHA-256 collision resistance, isolate the changed fields without baseline row payloads. No evidence of removed/replaced rows remains. Sanitized evidence is in tmp/phase11b-gate-safe-metadata.json and phase11b-gate-projection-extended.json; prior evidence was not rewritten.

## A4. Write paths and schema isolation

`tests/test_warehouse_modeling.py` imports pg/draft from `tests/test_warehouse_foundation.py`. Every database test uses that fixture directly or through modeling_pg. The fixture creates a UUID-derived test_warehouse_<hex> schema. Its original-URL connections perform only CREATE SCHEMA and generated identifier-quoted DROP SCHEMA CASCADE, never application-row DML.

make_conninfo overrides options with `-c search_path=<generated_schema>` and no public fallback. It applies at connection establishment, before migrations/DML, on every fixture connection. The fixture monkeypatches app.db.database.DATABASE_URL for its lifetime. Imported repository_transaction functions resolve that module global when called, so independently opened service connections and threaded 11A connections retain the test schema. No pool or pre-created reusable connection exists. ContextVar borrowing reuses the scoped outer connection and resets in finally. Repositories receive caller connections and open none themselves.

modeling_pg applies 0030 and inserts versions/rules/policies using pg['connect']. Ownership-transfer DML updates only generated-schema workspaces/datasets. Migration/rollback preservation tests also operate there. No 0030 public qualification/search-path reset was found. Pending 0028 functions capture the current generated path. Cleanup targets only the generated schema; no public cleanup or sequence reset occurs.

Production Phase 11B changes write warehouse configuration only and add approval validation. Scope checks read/lock dataset/workspace metadata. No production writer for users, datasets, Gold or authentication is added/reached. Pure contracts/keys/time/validators and the subprocess literal test have no database access. Phase 11B tests invoke warehouse services directly, not HTTP/auth/Gold processing. app.main imports register routes/middleware; no migration, authentication seed or processing runs on import.

Other available regression paths were inspected: authentication context opens generated test_phase10_* connections and patches loaded get_connection aliases; populated migration tests explicitly select their generated schema. The denied analytics HTTP test cannot publish Gold. Workspace-model unit tests stub transactions; unavailable historical integration fixtures remain skips.

Isolation result: no Phase 11B application-row escape to public found. Original-URL schema-management connections are not public application writers. Static tracing, isolated migration tests, exact fingerprint projections, mutation timing and unchanged public fingerprints across the new full regression support this conclusion. No implementation instrumentation or fix was needed.

## A5. Datasets, Gold and ownership safety

| Run | Dataset / workspace | Existing published state | Dataset version / policy | Started / completed IST | Outcome |
|---|---|---|---|---|---|
| 16 | 16 / 12 | 8 | 18 / 3 | 09:56:57.132 / 09:56:58.680 | SUCCESS, current pointer 16, FRESH |
| 17 | 17 / 12 | 11 | 19 / 4 | 09:58:29.220 / 09:58:30.436 | SUCCESS, current pointer 17, FRESH |

States 8/11 were already PUBLISHED on 6 October. Entire dataset_state_versions, delivery_applications and load-policy fingerprints are unchanged from the original baseline, so no new ingestion/trusted-state publication explains this difference. Existing dataset_analytics_service.refresh_analytics inserts a pinned REFRESHING run; publish_gold sets SUCCESS and the current pointer/FRESH if the state is still current. Observed fields match that writer and the user-confirmed manual refreshes. Prior 15 Gold rows are unchanged; these are newly created legitimate runs. Only the two intended dataset Gold pointers/freshness changed. Invalid current Gold heads/freshness count is zero. No Phase 11B test executes Gold processing.

Dataset/workspace owner mismatch count is zero. Three datasets retain legacy owner keys with no corresponding users row. This is pre-existing compatibility state: dataset ownership/identities/status/creation/archive fields match the original baseline by full projection, workspaces retain their original fingerprint, and all user fields except two login timestamps match baseline. No new user/dataset, archive, transfer or identity change occurred. Phase 10 current-owner invariants remain intact; legacy ownership was not adopted or rewritten.

## A6. Exact implementation freeze

Frozen before regression at 2026-10-21T08:22:41.286329+00:00; all 12 implementation/test/fixture/migration hashes remain identical afterward. The report is excluded because this appendix was explicitly requested.

| Path | SHA-256 |
|---|---|
| `app/db/warehouse_repository.py` | `1738244703ea679b294f4c6a0c8b2b740031742f63ba774cc5dc187f7f98e3c5` |
| `app/services/warehouse_model_service.py` | `3a5f418c52c221ec7a621b20c0aaae22a0eb42f73880eb8955dde25ccb346a52` |
| `tests/test_warehouse_foundation.py` | `d81677432df8a64d7d35c8d9d27a8650cba109a92433a49e30e919bb8c073bfc` |
| `app/schemas/warehouse_modeling.py` | `a5477b815c4cbb610bf4a79ed6bf563b759abffa094e69818821b7f22a55258f` |
| `app/services/warehouse_modeling_service.py` | `4bf85b7f7f74937f36300479bc664e78742ba5ff9767a8fb5083b95df0336be5` |
| `app/warehouse/keys.py` | `ea1d8a16f5a73c258f131ce7d96bd444b2bd92a69f27f2ae66636fef97855aba` |
| `app/warehouse/model_validation.py` | `26d75d63870c867f2bb960e6eb7f98effbaf0b7b08f88801a236dba664efb28f` |
| `app/warehouse/time_policy.py` | `032c79515632fbabf8ab92101e5e0e91ecffae2638a5b5ff09444c56db20c047` |
| `migrations/0030_warehouse_modeling.sql` | `7ff6c5535f0503992bfc7167d8e585b0001046b62d1cebf28df251e0da0bbe2a` |
| `migrations/rollback/0030_warehouse_modeling.sql` | `b413f03eb5f2605e1aa10246314dc7f5ec3a495f94c1ece5065f7e96fe6887b4` |
| `tests/fixtures/warehouse_keys_v1.json` | `6cab2f89fb52289b26cac9e445ed37c70a6f54e2c95d68652b8b7cbe7271f472` |
| `tests/test_warehouse_modeling.py` | `e35015af9864ba1c118fbe1b6b5f4839a4323acbf6e02e27038298efa9508d63` |

## A7. Final regression and safety checks

Requested established environment: RUN_DB_TESTS=1; `uv run --no-sync pytest -q --tb=short`, with workspace UV cache and new JUnit output `tmp/phase11b-gate-regression.xml`. Exact final tree result: **153 passed, 0 failed, 3 skipped, 2 warnings**, 157.53 s. Warnings are existing Starlette TestClient/httpx and Authlib/httpx deprecations. Skips remain ownership-compatibility and semantic-deadline missing historical modules plus the PostgreSQL model-summary profile fixture. No Phase 11B failure.

The valid 74-pass focused evidence after the temporal-key change is preserved. No separate focused rerun was necessary: no implementation/test file changed and isolation audit found no uncertainty requiring one. Full regression includes those tests.

`git diff --check`, explicit new-file whitespace checks and targeted private-key/AWS/Google/GitHub-token scans passed. This is a targeted scan, not a universal secret-free guarantee; no repository-specific scanner is configured. Git status was inspected; 240 unrelated baseline entries and their file hashes are preserved. Index empty, HEAD unchanged at f55597f85d7f5e9ebf2f40a5f3d7f869f5092190. No BigQuery/google-cloud-bigquery dependency in pyproject.toml or uv.lock. No frontend/dependency change.

Read-only public checks: ai_data_workspace.public, warehouse_model_configurations absent, WAREHOUSE_ENABLED=false, six 0029 tables empty. All 55 public table fingerprints match the previous final snapshot across this audit/new regression, including the seven now-explained original differences. No public rows restored/deleted/updated, migration applied, staging/commit, cloud resource or 11C work.

## A8. Recommendation

**READY_FOR_0030_DEV_MIGRATION**

All changed development tables are accounted for by exact read-only projections, existing writer tracing, timestamps and direct user confirmation. No Phase 11B pollution or implementation defect was found. Exact-tree full regression and safety checks pass. Migration 0030 remains unapplied; this completes the audit only.


# Final migration and release verification - 2026-10-21

User approved migration 0030, scoped staging, commit and push. No implementation/test/migration file changed after the audit freeze. This appendix supersedes migration-pending statements. New evidence stays in ignored tmp/phase11b-release-evidence.json and release JUnit files; older evidence is preserved.

## Database verification

Immediate read-only pre-migration baseline: ai_data_workspace.public, 55 tables, 15 workspaces, 33 datasets, 17 cumulative Gold runs, captured 2026-10-21T08:30:55.800511+00:00. This baseline includes the confirmed interactive activity; no immutability claim uses the older Phase 11A baseline.

Applied exactly the reviewed migrations/0030_warehouse_modeling.sql after verifying its SHA-256. No unrelated migration or rollback. Result: 56 tables; warehouse_model_configurations exists with zero rows. Six foundation tables remain empty; no operations/runs/resources were created.

All original 55 table counts and safe fingerprints match the immediate baseline. Column/default/nullability definitions, constraints and indexes match. The sole intended existing-table trigger addition is warehouse_modeling_approval; all prior triggers/functions are intact. No source-state/Gold pointer/freshness, ownership, identity or business-row changes.

Verified new primary key, five validated foreign keys (including scoped composite references), model/schema/validation-version, JSON structure and rule-SHA checks. All three indexes are valid/ready. Enabled immutable insert/update/delete guard, TRUNCATE protection integration and approval guard are present.

Development has zero historical model versions, so real legacy compatibility is vacuous here; isolated populated migration tests verify old approved versions remain readable/unconfigured and new approvals require typed configuration. No fabricated development configuration or permanent synthetic smoke rows were created.

Real-schema smoke: configuration readable/empty; disabled mutation fails before DB access; WAREHOUSE_ENABLED=false; application import succeeds. No BigQuery package/dependency or GCP setup was added or required by import. No cloud resources.

## Tests and release scope

Focused post-migration: 74 passed, 0 failed. Full available backend regression: 153 passed, 0 failed, 3 skipped, 2 warnings. New XML: tmp/phase11b-release-focused.xml and tmp/phase11b-release-regression.xml. Counts match baseline; historical missing-fixture skips and existing Starlette/Authlib httpx deprecations remain.

git diff --check, scoped whitespace and targeted secret checks passed. All 12 implementation/test/fixture/migration hashes match the reviewed freeze. No frontend, dependency, existing processing/Gold or Phase 11C implementation change. No runtime SCD/late-data/checkpoint/publication work.

The 13-file commit scope is exactly section 30; this report is the only post-approval content update. All 240 unrelated baseline status entries and files remain untouched, including historical deleted tests, Phase 10 reports, screenshots, old tools and phase11a-validation.json. Stage those paths explicitly and review cached name-status/stat/full diff/check before commit.

Approved message: feat: add typed warehouse modeling and deterministic keys. Remote master was confirmed at f55597f85d7f5e9ebf2f40a5f3d7f869f5092190 before commit. The intended push contains one Phase 11B commit. Its hash and confirmed push/origin status are recorded in the final task response, since a commit cannot contain its own hash.

Phase 11B is complete once scoped commit/push succeeds; the repository is then ready for separately authorized Phase 11C. No Phase 11C was started.
