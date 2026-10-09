"""Deterministic current-state Gold; no delivery profiles or operational measures."""
import pandas as pd
from app.processing.gold_planner import build_gold_plan
from app.processing.gold_mart_builder import build_mart
from app.processing.gold_catalog_builder import build_gold_artifact_catalog, enrich_catalog_data_types


def build_cumulative_gold(state, policy):
    columns = [c['name'] for c in policy['schema_columns']]
    if policy['load_strategy'] == 'SNAPSHOT':
        active = state['_datarise_active']
        if active.isna().any() or not active.map(lambda v: isinstance(v, bool)).all():
            raise ValueError('Invalid activity state')
        state = state.loc[active]
    base = state[columns].copy()
    # Profile the authoritative business frame, never an historical delivery.
    profiles = [(0, 0, c, 'identifier' if c in policy['business_keys'] else str(base[c].dtype), int(base[c].isna().sum()),
                 int(base[c].nunique()), 0, None, None, 0) for c in columns]
    plan = build_gold_plan(profiles)
    # Time-name heuristics in the legacy planner are unsafe for arbitrary strings.
    safe_times = {c for c in columns if pd.api.types.is_datetime64_any_dtype(base[c])}
    dimensions = set(plan.dimensions + plan.time_dimensions + policy['business_keys'])
    catalog_columns = [{'column_name': c, 'column_role': 'DIMENSION' if c in dimensions else 'MEASURE',
                        'source_column': c, 'aggregation_type': None, 'ordinal_position': i+1,
                        'data_type': str(base[c].dtype)} for i,c in enumerate(columns)]
    artifacts = [(base, {'artifact_type': 'BASE', 'artifact_name': 'base',
                         'grain': ','.join(policy['business_keys']) or 'trusted_record',
                         'time_grain': None, 'columns': catalog_columns})]
    for item in plan.artifacts:
        if item.time_grain and not set(item.dimensions) <= safe_times:
            continue
        frame = build_mart(base, item)
        catalog = enrich_catalog_data_types(build_gold_artifact_catalog(item), frame).to_dict()
        artifacts.append((frame, {**catalog, 'artifact_type': 'MART'}))
    return artifacts
