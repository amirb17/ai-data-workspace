# 414F0-A — Physical publication cleanup inspection, no deletion

**Status:** committed for local verification; production release remains NO-GO.

This is a deliberately small first manual checkpoint. It does not add a
cloud cleanup/deletion operation, execution permit, HTTP route, schema
migration, or runtime-role grant.

## Current controls and gap

- Migration 0051 serializes publication retirement fencing with new query
  reservation links, and refuses retirement while a current head or query
  dependency exists.
- Migration 0053 extends query dependencies with **indefinite** holds from
  dispatch checkpoints, even after revocation, expiry, or query settlement.
- Neither mechanism by itself prevents an out-of-band BigQuery table deletion,
  nor establishes cloud-side resource retention, runtime IAM or cleanup actor
  privileges.

The internal inspection acquires the same resource advisory transaction lock
as the 0051 retirement trigger, checks the exact publication identity, current
head, existing dependency function and retirement fence, and classifies it:

| Classification | Meaning |
| --- | --- |
| CURRENT_PUBLICATION_HEAD | Current head cannot retire |
| QUERY_RETENTION_DEPENDENCY | Query reservation/checkpoint still holds publication |
| RETIREMENT_FENCE_MISSING | Historical unheld publication has no retirement fence |
| RETIRED_METADATA_ONLY_CLOUD_ENFORCEMENT_UNVERIFIED | PostgreSQL retirement is recorded, but cloud deletion is still forbidden |

All responses set `physical_deletion_authorized=False`. They are observations
inside an ephemeral transaction, **not** durable permits. A later cloud-delete
implementation must independently enforce cloud IAM and deletion/retirement
concurrency fences at its own safe boundary. No stale inspection result may be
used to authorize deletion.

## Test gate

`RUN_DB_TESTS=1`, disposable PostgreSQL schemas only; test module covers
current head, checkpoint hold after publication advancement, retirement fence
requirements, invalid/unknown identities, and absence of any cloud delete
path. Run prior retirement and D5 regression modules alongside this suite.

## Follow-up

414F0-B must audit/deploy-test PostgreSQL restricted roles and BigQuery IAM;
414F0-C must specify a supported physical table retention/deletion controller,
possibly with a protected deletion tombstone and provider IAM separation.
External GCP administrators and out-of-band deletion remain P0.
Never release the 0053 indefinite holds merely because the send journal
observes a terminal state.

**No live BigQuery analytical execution or table deletion is enabled.**
