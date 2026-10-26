# Phase 11G A1.6 — Controlled Metric provider switch

## Status

A1.6 introduces an explicitly gated Metric provider-selection layer.

The existing Pandas Metric engine remains the deterministic fallback.
BigQuery may become the selected result provider only when the already-proven
A1.4/A1.5 governed shadow path returns MATCH and the decoded warehouse result
is exactly equal to the existing public Pandas output contract.

## Rollout flags

- `METRIC_BIGQUERY_SHADOW_ENABLED=false`
- `METRIC_BIGQUERY_PRIMARY_ENABLED=false`

Both default OFF.

Primary promotion requires shadow execution to be enabled.

## Provider selection

When primary rollout is disabled:

Pandas remains authoritative and the existing shadow-only behavior is unchanged.

When primary rollout is enabled:

1. Pandas executes under the existing Metric contract.
2. BigQuery executes through the existing governed METRIC query path.
3. Existing warehouse eligibility, publication, serving-receipt, type,
   numeric-portability, query-governance and recovery rules remain authoritative.
4. BigQuery is selected only for a MATCH with an exact public-result payload.
5. All other outcomes fall back to Pandas.

## Fallback outcomes

Pandas remains selected for:

- shadow disabled
- warehouse ineligible
- serving not ready
- query not admitted
- estimate revalidation
- query recovery required
- provider failure
- semantic mismatch
- missing warehouse result
- public API representation mismatch
- unexpected rollout-service failure

## Recovery

A1.6 does not introduce another query recovery protocol.

The existing Phase 11H governed-query reservation and BigQuery deterministic
job recovery remain authoritative.

An uncertain BigQuery result is not promoted. The user-visible refresh safely
falls back to Pandas.

## API compatibility

Metric definitions, execution-plan signatures, candidate identity, API response
shape and Metric output representation are unchanged.

Semantic equality alone is not sufficient for promotion. The decoded BigQuery
output must also equal the existing Pandas public result exactly.

## Rollback

Rollback is immediate:

`METRIC_BIGQUERY_PRIMARY_ENABLED=false`

No database migration, data rewrite or Metric rediscovery is required.

## Intentional limitations

Legacy warehouse FLOAT measures remain ineligible.

Decimal arithmetic outside the A1.5 jointly portable numeric envelope remains
ineligible.

Pandas continues to execute during controlled rollout as a runtime safety
reference. Removing dual execution is a later optimization after live-cloud
confidence.

Analytics, Ask and SQL are not migrated by A1.6.