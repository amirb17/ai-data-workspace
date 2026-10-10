"""Authoritative metric results, independently refreshed from trusted dataset Gold."""
from dataclasses import asdict
from collections import OrderedDict
import logging
import time
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.db import metric_result_repository as repository, metric_repository
from app.services import metric_service
from app.services.workspace_understanding_service import validate_workspace
from app.processing.metric_execution import compile_plan, PandasMetricExecutionEngine, MAX_ROWS, BoundedMetricArtifacts
from app.processing.metric_validator import ALGORITHM_VERSION, POLICY_VERSION

logger = logging.getLogger(__name__)


def blocked_reason(row, source, review):
    evidence = row['evidence']
    if review and review['status'] == 'REJECTED': return 'REJECTED'
    if evidence['validation_status'] != 'VALID' or evidence['decision'] == 'REVIEW_REQUIRED':
        return 'DEFINITION_REQUIRED'
    freshness = metric_service.freshness(evidence, source)
    if freshness['definition_freshness'] != 'CURRENT':
        return freshness['definition_freshness']
    confirmed = {r['candidate_id'] for r in source['relationships']}
    if any(rid not in confirmed for rid in evidence['definition']['relationship_ids']):
        return 'RELATIONSHIP_REQUIRED'
    if freshness['data_freshness'] != 'CURRENT': return 'REFRESH_REQUIRED'
    return 'REVALIDATION_REQUIRED'


def resolve(conn, workspace, user, lock=False):
    source = metric_service.gather(conn, workspace, user, lock)
    rows = repository.candidates(conn, workspace)
    reviews = {r['candidate_id']: r for r in metric_repository.latest_reviews(conn, workspace)}
    resolved = []
    for row in rows:
        e = row['evidence']
        if e['validation_status'] == 'INVALID': continue
        plan = None
        reason = None
        try:
            review = reviews.get(row['candidate_id'])
            if review and review['status'] == 'REJECTED': raise ValueError('REJECTED')
            run = conn.cursor(row_factory=dict_row).execute('SELECT algorithm_version,policy_version FROM metric_discovery_runs WHERE run_id=%s', (row['run_id'],)).fetchone()
            if run['algorithm_version'] != ALGORITHM_VERSION or run['policy_version'] != POLICY_VERSION:
                raise ValueError('REVALIDATION_REQUIRED')
            # Discovery identity is workspace-wide; execution dependencies are metric-scoped.
            # Retain immutable business scope while validating each actual current input.
            validation_source = source
            if not source['workspace_reasoning']:
                scope_pin = next(d['suggestion_id'] for d in e['dependencies'] if d['kind'] == 'WORKSPACE')
                scope = conn.cursor(row_factory=dict_row).execute("SELECT reasoning FROM workspace_semantic_suggestions WHERE workspace_id=%s AND suggestion_id=%s AND status='READY'", (workspace, scope_pin)).fetchone()
                if not scope: raise ValueError('REVALIDATION_REQUIRED')
                validation_source = {**source, 'workspace_reasoning': scope['reasoning'], 'workspace_suggestion_id': scope_pin}
            plan = compile_plan(workspace, row, validation_source)
        except (ValueError, KeyError, StopIteration):
            # Public categories only, never exception text/paths/row contents.
            reason = blocked_reason(row, source, reviews.get(row['candidate_id']))
        names = {x['pin']['dataset_id']: x['pin']['dataset_name'] for x in source['eligible']}
        resolved.append({'row': row, 'plan': plan, 'reason': reason,
                         'source_names': [{'dataset_id': i, 'dataset_name': names.get(i, f'Dataset {i}')} for i in e['required_datasets']]})
    return resolved


def view(workspace, resolved, history, computing=False):
    cards = []
    for item in resolved:
        row, plan = item['row'], item['plan']
        attempts = [r for r in history if r['candidate_id'] == row['candidate_id']]
        matching = [r for r in attempts if plan and r['dependency_signature'] == plan.signature]
        success = next((r for r in matching if r['status'] == 'FRESH'), None)
        latest = matching[0] if matching else None
        reason = item['reason']
        if not plan: status = 'STALE' if reason == 'REFRESH_REQUIRED' else 'BLOCKED'
        elif success: status = 'FRESH'
        elif latest and latest['status'] == 'COMPUTING': status = 'COMPUTING' if computing else 'FAILED'
        elif latest: status = latest['status']
        else: status = 'STALE' if attempts else 'NOT_COMPUTED'
        current = success if status == 'FRESH' else None
        cards.append({'candidate_id': row['candidate_id'], 'definition_version': row['run_id'],
                      'definition': row['evidence']['definition'], 'decision': row['evidence']['decision'],
                      'required_datasets': row['evidence']['required_datasets'], 'status': status,
                      'source_names': item.get('source_names', []),
                      'source_versions': [{k: p[k] for k in ('dataset_id', 'state_id', 'gold_run_id')} for p in plan.sources] if plan else [],
                      'relationship_versions': [{'relationship_id': j['relationship_id'], 'version': j['version']} for j in plan.joins] if plan else [],
                      'execution_algorithm_version': plan.algorithm_version if plan else None,
                      'reason': reason, 'review_reasons': row['evidence']['review_reasons'],
                      'result_id': current['result_id'] if current else None,
                      'computed_at': current['computed_at'] if current else None,
                      'output': current['output'] if current else None,
                      'failure_code': latest['failure_code'] if status == 'FAILED' and latest else None,
                      'can_retry': bool(plan and status == 'FAILED')})
    counts = {key: sum(c['status'] == state for c in cards) for key, state in
              [('metrics_fresh', 'FRESH'), ('metrics_stale', 'STALE'), ('metrics_failed', 'FAILED'), ('metrics_blocked', 'BLOCKED')]}
    available = sum(i['plan'] is not None for i in resolved)
    state = 'COMPUTING' if computing else 'NOT_COMPUTED' if not cards or all(c['status'] == 'NOT_COMPUTED' for c in cards) else 'FRESH' if counts['metrics_fresh'] == len(cards) else 'NEEDS_ATTENTION'
    times = [r['computed_at'] for r in history if r['computed_at']]
    return {'workspace_id': workspace, 'status': state, 'metrics_total': len(cards),
            'metrics_available': available, **counts, 'last_refreshed': max(times) if times else None,
            'can_refresh': not computing and available > 0, 'metrics': cards}


def read_analytics(workspace, user):
    validate_workspace(workspace, user)
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        resolved = resolve(conn, workspace, user)
        result = view(workspace, resolved, repository.results(conn, workspace, resolved), repository.active(conn, workspace))
        result['last_refreshed'] = conn.execute('SELECT MAX(computed_at) FROM workspace_metric_results WHERE workspace_id=%s', (workspace,)).fetchone()[0]
        return result


def load_sources(conn, plan):
    handles = []
    for pin in plan.sources:
        run = conn.cursor(row_factory=dict_row).execute('''SELECT g.* FROM dataset_gold_runs g
            JOIN datasets d USING(dataset_id) WHERE g.gold_run_id=%s AND g.dataset_id=%s
            AND d.workspace_id=%s AND g.source_state_id=%s AND g.status='SUCCESS' ''',
            (pin['gold_run_id'], pin['dataset_id'], plan.workspace_id, pin['state_id'])).fetchone()
        if not run: raise ValueError('INPUT_INVALID')
        base = next(a for a in run['catalog'] if a['artifact_type'] == 'BASE')
        if base['row_count'] > MAX_ROWS: raise ValueError('INPUT_LIMIT')
        handles.append((pin, run, base))
    return handles


def read_frames(handles, store, cache=None):
    cache = OrderedDict() if cache is None else cache
    frames = {}
    for pin, run, base in handles:
        key = (pin['dataset_id'], pin['state_id'], pin['gold_run_id'])
        if key in cache:
            cache.move_to_end(key)
            frames[pin['dataset_id']] = cache[key]
            continue
        columns = [c['column_name'] for c in base['columns']]
        # Reuse owned immutable manifest/checksum/schema validation, never delivery Gold.
        validated = {}
        catalog = store.validate(run, run['manifest_key'], run['manifest_sha256'], base['row_count'], columns,
                                 base_only=True, frames=validated)
        if catalog[0] != base: raise ValueError('INPUT_INVALID')
        frame = validated[base['gold_artifact_id']]
        if len(frame) != base['row_count']: raise ValueError('INPUT_INVALID')
        cache[key] = frame
        if len(cache) > 2: cache.popitem(last=False)
        frames[pin['dataset_id']] = frame
    return frames


def refresh_analytics(workspace, user, candidate_id=None):
    validate_workspace(workspace, user)
    with repository.execution_lock(workspace):
        frames_cache = OrderedDict()
        store = None
        with repository_transaction() as conn:
            resolved = resolve(conn, workspace, user, True)
            if candidate_id is not None and not any(i['row']['candidate_id'] == candidate_id for i in resolved):
                raise HTTPException(404, 'Metric not found in workspace')
        for item in resolved:
            if not item['plan'] or candidate_id is not None and item['row']['candidate_id'] != candidate_id: continue
            started = time.monotonic()
            with repository_transaction() as conn:
                # Re-resolve each metric after earlier execution/source changes.
                current = next((i for i in resolve(conn, workspace, user, True) if i['row']['candidate_id'] == item['row']['candidate_id']), None)
                if not current or not current['plan']: continue
                plan = current['plan']
                c = conn.cursor(row_factory=dict_row)
                reused = c.execute("SELECT result_id FROM workspace_metric_results WHERE candidate_id=%s AND dependency_signature=%s AND status='FRESH'", (plan.candidate_id, plan.signature)).fetchone()
                if reused:
                    logger.info('Metric reused workspace=%s candidate=%s result=%s', workspace, plan.candidate_id, reused['result_id'])
                    continue
                # Dead coordinator attempts are finalized; retry appends a new immutable attempt.
                c.execute("UPDATE workspace_metric_results SET status='FAILED',failure_code='INTERRUPTED',computed_at=NOW() WHERE workspace_id=%s AND candidate_id=%s AND status='COMPUTING'", (workspace, plan.candidate_id))
                attempt = c.execute('''INSERT INTO workspace_metric_results(workspace_id,candidate_id,definition_version,
                    algorithm_version,dependency_signature,plan,status) VALUES (%s,%s,%s,%s,%s,%s,'COMPUTING') RETURNING result_id''',
                    (workspace, plan.candidate_id, current['row']['run_id'], plan.algorithm_version, plan.signature, Jsonb(asdict(plan)))).fetchone()
            failure = None
            try:
                with repository_transaction() as conn: handles = load_sources(conn, plan)
                if store is None: store = BoundedMetricArtifacts.create()
                output = PandasMetricExecutionEngine().execute(plan, read_frames(handles, store, frames_cache))
                with repository_transaction() as conn:
                    # No mutable current pointer: reads resolve exact current signatures. Old completion is historical.
                    conn.execute("UPDATE workspace_metric_results SET status='FRESH',output=%s,computed_at=NOW() WHERE result_id=%s", (Jsonb(output), attempt['result_id']))
            except Exception as exc:
                failure = 'INPUT_LIMIT' if isinstance(exc, ValueError) and str(exc) in ('INPUT_LIMIT', 'OUTPUT_LIMIT', 'Artifact byte limit exceeded') else 'EXECUTION_FAILED'
                with repository_transaction() as conn:
                    conn.execute("UPDATE workspace_metric_results SET status='FAILED',failure_code=%s,computed_at=NOW() WHERE result_id=%s AND status='COMPUTING'", (failure, attempt['result_id']))
            logger.info('Metric execution workspace=%s candidate=%s definition=%s result=%s sources=%s relationships=%s algorithm=%s status=%s duration=%.3f',
                        workspace, plan.candidate_id, current['row']['run_id'], attempt['result_id'],
                        list(plan.sources), [j['relationship_id'] for j in plan.joins], plan.algorithm_version, failure or 'FRESH', time.monotonic()-started)
            if not failure:
                logger.info('Metric size workspace=%s candidate=%s result=%s input_rows=%s output_groups=%s',
                            workspace, plan.candidate_id, attempt['result_id'], output['input_rows'], len(output['groups']))
    return read_analytics(workspace, user)
