"""Owned logical SQL catalog. Current published cumulative Gold only; no delivery fallback."""
import re
from psycopg.rows import dict_row
from app.db.database import repository_transaction
from app.services.workspace_understanding_service import validate_workspace
from app.services.dataset_profile_service import read_profile
from app.services.dataset_understanding_service import read_understanding
from app.services import relationship_service
from app.db import relationship_repository
from app.processing.relationship_evidence import digest
from app.processing.sql_policy import capabilities


def aliases(rows):
    result = {}
    for row in sorted(rows, key=lambda r: r['dataset_id']):
        name = re.sub(r'[^a-z0-9_]+', '_', row['dataset_name'].casefold()).strip('_')[:60] or 'dataset'
        if name[0].isdigit(): name = 'data_' + name
        # The immutable identity suffix prevents adding/archiving/renaming another
        # dataset from silently rebinding saved SQL to a different business entity.
        result[row['dataset_id']] = name + '_d' + str(row['dataset_id'])
    return result


def gather(conn, workspace, user, lock=False):
    c = conn.cursor(row_factory=dict_row)
    w = c.execute('SELECT * FROM workspaces WHERE workspace_id=%s' + (' FOR UPDATE' if lock else ''), (workspace,)).fetchone()
    if not w or w['owner'] != user['owner_key']:
        validate_workspace(workspace, user)
    if lock:
        c.execute('SELECT dataset_id FROM datasets WHERE workspace_id=%s ORDER BY dataset_id FOR UPDATE', (workspace,)).fetchall()
    rows = c.execute('''SELECT d.*,s.dataset_version_id,s.policy_id,p.schema_columns,p.business_keys,p.event_time_column,
        g.source_state_id AS gold_state_id,g.status AS gold_status,g.row_count AS gold_rows,g.completed_at
        FROM datasets d LEFT JOIN dataset_state_versions s ON s.state_id=d.current_state_id
        LEFT JOIN dataset_load_policies p ON p.policy_id=s.policy_id
        LEFT JOIN dataset_gold_runs g ON g.gold_run_id=d.current_gold_run_id
        WHERE d.workspace_id=%s AND d.owner=%s AND d.status!='ARCHIVED' ORDER BY d.dataset_id''', (workspace, user['owner_key'])).fetchall()
    names = aliases(rows); datasets = []
    for row in rows:
        ready = bool(row['current_state_id'] and row['gold_status'] == 'SUCCESS' and
                     row['gold_state_id'] == row['current_state_id'] and row['state_analytics_status'] == 'FRESH')
        profile = read_profile(workspace, row['dataset_id'], user) if row['current_state_id'] else None
        ready = ready and bool(profile and profile['profile_ready'])
        evidence = {v['original_name']: v for v in profile['columns']} if profile and profile['profile_ready'] else {}
        semantic = read_understanding(workspace, row['dataset_id'], user) if profile and profile['profile_ready'] else None
        roles = {v['column_name']: v for v in semantic['suggestion']['reasoning']['columns']} if semantic and semantic['status'] == 'READY' else {}
        columns = []
        for raw in row['schema_columns'] or []:
            name = raw['name']; facts = evidence.get(name, {}); suggestion = roles.get(name, {})
            sensitive = bool(facts.get('sensitivity_hints'))
            columns.append(dict(name=name, type=facts.get('canonical_type', 'UNKNOWN'),
                physical_type=facts.get('physical_type', raw.get('data_type', 'Unknown')),
                role=suggestion.get('suggested_role', 'UNKNOWN'), description=suggestion.get('business_meaning', ''),
                key=name in (row['business_keys'] or []), time=name == row['event_time_column'], sensitive=sensitive,
                queryable=not sensitive and bool(facts) and not name.startswith(('_datarise_', '_source_', '_dq_'))))
        ready = ready and any(v['queryable'] for v in columns)
        datasets.append(dict(dataset_id=row['dataset_id'], name=row['dataset_name'], alias=names[row['dataset_id']],
            queryable=ready, freshness='Current' if ready else 'Refresh required',
            reason='' if ready else 'Process and refresh current analytics and the data profile before querying.',
            row_count=row['gold_rows'] if ready else None, updated_at=row['completed_at'] if ready else None,
            columns=columns, state_id=row['current_state_id'], gold_run_id=row['current_gold_run_id'],
            profile_id=profile['profile_id'] if profile else None))
    source = relationship_service.gather(conn, workspace, user)
    reviewed = relationship_service.reviewed_view(source, relationship_repository.reviews(conn, workspace))
    available = {d['dataset_id'] for d in datasets if d['queryable']}
    relations = []
    for r in reviewed:
        e = r['evidence']
        if r['status'] == 'CONFIRMED' and r['structural_status'] == r['verification_status'] == 'CURRENT' and {e['parent']['dataset_id'], e['child']['dataset_id']} <= available:
            relations.append(dict(relationship_id=r['candidate_id'], version=r['relationship_version'],
                parent=e['parent'], child=e['child'], cardinality=e['candidate_cardinality']))
    return dict(workspace_id=workspace, workspace_name=w['workspace_name'], datasets=datasets, relationships=relations,
                capabilities=capabilities())


def snapshot(workspace, user):
    validate_workspace(workspace, user)
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return gather(conn, workspace, user)


def pins(catalog, ids):
    return [{k: d[k] for k in ('dataset_id', 'name', 'alias', 'state_id', 'gold_run_id', 'profile_id')}
            for d in catalog['datasets'] if d['dataset_id'] in ids]


def signature(catalog, ids, relations):
    return digest([pins(catalog, ids), [{k:d[k] for k in ('dataset_id','queryable','columns')}
                   for d in catalog['datasets'] if d['dataset_id'] in ids], relations])
