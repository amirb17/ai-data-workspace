"""Allowlisted, no-value metadata handoff; metadata is always untrusted DATA."""
import json

SEMANTIC_VERSION = 1
FULL_COLUMNS = 40
MAX_COLUMNS = 200
MAX_INPUT_BYTES = 60000


def build_handoff(profile):
    columns = profile['columns']
    if len(columns) > MAX_COLUMNS:
        raise ValueError('WIDTH_LIMIT')
    summary = profile['summary']
    bundle = {'dataset': {'name': profile['dataset_name'], 'row_count': summary['row_count'],
        'column_count': summary['column_count'], 'load_strategy': summary['load_strategy']},
        'authoritative': {key: summary.get(key) for key in
            ('business_key','event_time_column','snapshot_effective_at','snapshot_coverage','rule_version')},
        'schema_version': profile['schema_version'], 'columns': [], 'compact_mode': len(columns) > FULL_COLUMNS,
        'value_policy': 'NO_RAW_VALUES', 'context_reduced': []}
    for column in columns:
        if len(column['original_name']) > 256:
            raise ValueError('WIDTH_LIMIT')
        compact = {key: column[key] for key in ('original_name','normalized_name','tokens','canonical_type',
            'null_ratio','distinct_ratio','identifier_candidate','authoritative_business_key',
            'business_key_position','authoritative_event_time','approved_required','sensitivity_hints')}
        # Preserve names/types for every column; rich context prioritized deterministically.
        important = column['authoritative_business_key'] or column['authoritative_event_time'] or bool(column['sensitivity_hints'])
        if not bundle['compact_mode'] or important:
            compact.update({key: column.get(key) for key in ('physical_type','identifier_score_components',
                'datetime_statistics','numeric_statistics','boolean_distribution','pattern_hints','pattern_scan')})
            cat = column.get('categorical_statistics')
            if cat:
                compact['categorical_frequencies'] = [
                    {key: item[key] for key in ('rank','count','percentage')} for item in cat['top_values'][:5]]
        # Defense in depth: sensitivity excludes ranges/aggregates even if upstream regresses.
        if column['sensitivity_hints']:
            compact.pop('numeric_statistics', None)
            compact.pop('datetime_statistics', None)
        bundle['columns'].append(compact)
    if bundle['compact_mode']:
        bundle['context_reduced'] = ['Rich statistics limited to configured keys, event time and sensitivity hints.']
    encoded = json.dumps(bundle, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode()) > MAX_INPUT_BYTES:
        bundle['compact_mode'] = True
        bundle['context_reduced'] = ['Statistics omitted to respect the deterministic request-size limit.']
        for column in bundle['columns']:
            for key in ('physical_type','identifier_score_components','datetime_statistics','numeric_statistics',
                        'boolean_distribution','pattern_hints','pattern_scan','categorical_frequencies'):
                column.pop(key, None)
        encoded = json.dumps(bundle, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode()) > MAX_INPUT_BYTES:
        raise ValueError('WIDTH_LIMIT')
    return bundle, encoded
