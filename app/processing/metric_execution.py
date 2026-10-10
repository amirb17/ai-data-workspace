"""Deterministic bounded metric execution. No provider, SQL or code evaluation."""
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict
import math
import json
import pandas as pd
from app.schemas.metrics import MetricProposal
from app.processing.metric_validator import validate_metric
from app.processing.relationship_evidence import digest

EXECUTION_VERSION = 1
MAX_ROWS = 250_000
MAX_GROUPS = 100
MAX_OUTPUT_BYTES = 256_000


MetricScalar = int | float | str | bool | None


class MetricGroup(TypedDict):
    dimensions: list[MetricScalar]
    value: MetricScalar


class MetricExecutionResult(TypedDict):
    kind: Literal['scalar', 'grouped']
    value: MetricScalar
    groups: list[MetricGroup]
    input_rows: int
    total_groups: int
    truncated: bool
    display_hint: Literal['scalar', 'table', 'line']


@dataclass(frozen=True)
class MetricExecutionPlan:
    workspace_id: int
    candidate_id: int
    definition: dict
    sources: tuple
    joins: tuple
    signature: str
    algorithm_version: int = EXECUTION_VERSION


class MetricExecutionEngine(Protocol):
    def execute(self, plan: MetricExecutionPlan, sources: dict[int, Any]) -> MetricExecutionResult: ...


def compile_plan(workspace, candidate, source):
    """Revalidate structural semantics against current evidence; pin only used sources."""
    original = candidate['evidence']
    if source.get('workspace_id', workspace) != workspace:
        raise ValueError('WORKSPACE_MISMATCH')
    if original['validation_status'] != 'VALID' or original['decision'] == 'REVIEW_REQUIRED':
        raise ValueError('DEFINITION_REQUIRED')
    checked = validate_metric(original['definition'], source)
    if checked['validation_status'] != 'VALID' or checked['decision'] == 'REVIEW_REQUIRED':
        raise ValueError('REVALIDATION_REQUIRED')
    old = {d['dataset_id']: d for d in original['dependencies'] if d['kind'] == 'DATASET'}
    pins = []
    for dep in checked['dependencies']:
        if dep['kind'] != 'DATASET':
            continue
        prior = old.get(dep['dataset_id'])
        if not prior or any(prior[k] != dep[k] for k in ('structural_key', 'semantic_key')):
            raise ValueError('REVALIDATION_REQUIRED')
        row = source['members'][dep['dataset_id']]
        if (row['gold_status'] != 'SUCCESS' or row['gold_state_id'] != row['current_state_id']
                or row['state_analytics_status'] != 'FRESH'):
            raise ValueError('REFRESH_REQUIRED')
        pins.append({'dataset_id': dep['dataset_id'], 'state_id': row['current_state_id'],
                     'gold_run_id': row['current_gold_run_id'], 'structural_key': dep['structural_key'],
                     'semantic_key': dep['semantic_key']})
    joins = []
    prior_relationships = {d['candidate_id']: d['relationship_version'] for d in original['dependencies'] if d['kind'] == 'RELATIONSHIP'}
    for rid in checked['definition']['relationship_ids']:
        relation = next(r for r in source['relationships'] if r['candidate_id'] == rid)
        if prior_relationships.get(rid) != relation['relationship_version']:
            raise ValueError('REVALIDATION_REQUIRED')
        e = relation['evidence']
        if len(e['parent']['columns']) != 1 or len(e['child']['columns']) != 1:
            raise ValueError('UNSUPPORTED_JOIN')
        joins.append({'relationship_id': rid, 'version': relation['relationship_version'],
                      'parent': e['parent'], 'child': e['child'], 'cardinality': e['candidate_cardinality']})
    definition = checked['definition']
    signature = digest([candidate['candidate_id'], definition, pins, joins, EXECUTION_VERSION])
    return MetricExecutionPlan(workspace, candidate['candidate_id'], definition, tuple(pins), tuple(joins), signature)


def scalar(value):
    if pd.isna(value):
        return None
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('NONFINITE_RESULT')
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class PandasMetricExecutionEngine:
    def execute(self, plan: MetricExecutionPlan, sources: dict[int, Any]) -> MetricExecutionResult:
        result = self._execute(plan, sources)
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_OUTPUT_BYTES:
            raise ValueError('OUTPUT_LIMIT')
        return result

    def _execute(self, plan, sources):
        m = MetricProposal.model_validate(plan.definition)
        if m.default_filters or any(n.filters for n in m.formula.nodes):
            raise ValueError('UNRESOLVED_FILTER')
        def name(ref):
            return (ref.dataset_id, ref.column)
        frames = {}
        for pin in plan.sources:
            frame = sources[pin['dataset_id']]
            if len(frame) > MAX_ROWS or frame.columns.duplicated().any():
                raise ValueError('INPUT_LIMIT')
            frames[pin['dataset_id']] = frame.rename(columns=lambda c: (pin['dataset_id'], c)).copy()
        base = frames[m.base_dataset_id]
        input_count = len(base)
        if m.grain == 'ENTITY':
            keys = [(m.base_dataset_id, c) for c in m.grain_keys]
            if base[keys].isna().any().any() or base.duplicated(keys).any():
                raise ValueError('GRAIN_NOT_UNIQUE')
        for join in plan.joins:
            parent, child = join['parent'], join['child']
            if child['dataset_id'] != m.base_dataset_id or join['cardinality'] not in ('ONE_TO_MANY', 'ONE_TO_ONE'):
                raise ValueError('FANOUT_UNSAFE')
            pk = (parent['dataset_id'], parent['columns'][0])
            fk = (child['dataset_id'], child['columns'][0])
            dimension = frames[parent['dataset_id']]
            if dimension[pk].isna().any() or dimension[pk].duplicated().any():
                raise ValueError('DIMENSION_NOT_UNIQUE')
            if base[fk].isna().any() or not base[fk].isin(dimension[pk]).all():
                raise ValueError('JOIN_COVERAGE')
            if join['cardinality'] == 'ONE_TO_ONE' and base[fk].duplicated().any():
                raise ValueError('GRAIN_NOT_UNIQUE')
            base = base.merge(dimension, left_on=[fk], right_on=[pk], how='left', validate='many_to_one')
            if len(base) != input_count:
                raise ValueError('FANOUT_UNSAFE')
        grouping = [name(r) for r in m.dimensions]
        if m.time_field:
            time = base[name(m.time_field)]
            if not pd.api.types.is_datetime64_any_dtype(time):
                raise ValueError('TIME_TYPE_UNSUPPORTED')
            # Preserve source timezone calendar; do not guess a business timezone.
            local = time.dt.tz_localize(None) if time.dt.tz is not None else time
            bucket = local.dt.to_period({'DAY': 'D', 'WEEK': 'W-SUN', 'MONTH': 'M'}[m.time_bucket]).dt.start_time
            time_key = ('time', 'bucket')
            base[time_key] = bucket
            grouping.append(time_key)
        def aggregate(frame):
            values = {}
            for node in m.formula.nodes:
                args = [values[a] for a in node.args]
                if node.op == 'COLUMN':
                    value = frame[name(node.column)]
                    # Python integer arithmetic avoids silent int64 aggregate/product overflow.
                    if pd.api.types.is_integer_dtype(value.dtype): value = value.astype(object)
                elif node.op == 'MULTIPLY': value = args[0] * args[1]
                elif node.op == 'COUNT': value = len(frame)
                elif node.op == 'COUNT_DISTINCT': value = args[0].nunique(dropna=True)
                elif node.op == 'SUM': value = args[0].sum(min_count=1)
                elif node.op == 'AVG': value = args[0].mean()
                elif node.op == 'MIN': value = args[0].min()
                elif node.op == 'MAX': value = args[0].max()
                elif node.op == 'DIVIDE':
                    value = None if pd.isna(args[0]) or pd.isna(args[1]) or args[1] == 0 else args[0] / args[1]
                else: raise ValueError('UNSUPPORTED_EXPRESSION')
                values[node.id] = value
            value = values[m.formula.root]
            return scalar(value * m.formula.scale if value is not None else None)
        if not grouping:
            return {'kind': 'scalar', 'value': aggregate(base), 'groups': [], 'input_rows': input_count,
                    'total_groups': 0, 'truncated': False, 'display_hint': 'scalar'}
        # Bound output deterministically by ascending typed grouping keys, nulls last.
        groups = []
        total = base.groupby(grouping, dropna=False, sort=True).ngroups
        for index, (keys, frame) in enumerate(base.groupby(grouping, dropna=False, sort=True)):
            if index >= MAX_GROUPS: break
            if not isinstance(keys, tuple): keys = (keys,)
            groups.append({'dimensions': [scalar(k) for k in keys], 'value': aggregate(frame)})
        return {'kind': 'grouped', 'value': None, 'groups': groups, 'input_rows': input_count,
                'total_groups': total, 'truncated': total > MAX_GROUPS,
                'display_hint': 'line' if m.time_field and not m.dimensions else 'table'}


class BoundedMetricArtifacts:
    """Factory reuses the Gold reader with a byte bound on every object read."""
    @staticmethod
    def create():
        from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts
        class Reader(CumulativeGoldArtifacts):
            def read(self, key, max_bytes=None):
                return super().read(key, max_bytes=min(max_bytes or 64 * 1024 * 1024, 64 * 1024 * 1024))
        return Reader()
