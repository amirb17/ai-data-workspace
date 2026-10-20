"""Explicit operator-only maintenance operation. No API route or automatic call."""
from psycopg import sql, Error
from psycopg.types.json import Jsonb
from app.db.database import get_connection
from app.db import ownership_adoption_repository as repository
from app.services.ownership_adoption_plan import plan, AdoptionConflict


def adoption(manifest, operator_user_id, *, execute=False, operation='ADOPT', connection_factory=get_connection):
    """Default is READ ONLY. Call execute only after independent human approval.

    The same reviewed manifest drives dry run, execution, replay and inverse.
    Database exceptions roll back the entire connection context before returning.
    """
    try:
        with connection_factory() as conn:
            if execute:
                conn.execute("SET LOCAL lock_timeout='5s'")
                conn.execute("SET LOCAL statement_timeout='60s'")
                # Lock before any snapshot: READ COMMITTED sees writers that finished
                # while we waited. SHARE ROW EXCLUSIVE serializes all business writes.
                tables = sorted(set(repository.business_tables(conn) + ['users', 'ownership_adoption_events']))
                for table in tables:
                    conn.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(sql.Identifier(table)))
            else:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            actor = conn.execute('SELECT email_verified,account_status FROM users WHERE user_id=%s', (operator_user_id,)).fetchone()
            if actor != (True, 'ACTIVE'):
                return {'status': 'CONFLICT', 'reason': 'Verified active operator required'}
            current = repository.snapshot(conn, manifest['source_principal'], manifest['target_user_id'])
            prior = repository.events(conn, manifest['adoption_id'])
            # UUIDs are converted for the pure planner's JSON contract.
            for event in prior:
                event['adoption_id'] = str(event['adoption_id'])
            proposed = plan(manifest, current, prior, operation)
            if proposed['status'].startswith('ALREADY_'):
                return proposed
            if repository.active_work(conn):
                return {'status': 'CONFLICT', 'reason': 'Active or interrupted processing/generation requires review'}
            if not execute:
                return {**proposed, 'status': 'READY_TO_ADOPT' if operation == 'ADOPT' else 'READY_TO_REVERSE'}
            event = {**proposed['provenance_inserts'][0], 'initiated_by_user_id': operator_user_id}
            columns = list(event)
            values = [Jsonb(event[k]) if k == 'resource_scope' else event[k] for k in columns]
            conn.execute(sql.SQL('INSERT INTO ownership_adoption_events ({}) VALUES ({})').format(
                sql.SQL(',').join(map(sql.Identifier, columns)), sql.SQL(',').join(sql.Placeholder() for _ in columns)), values)
            for change in proposed['updates']:
                key = 'workspace_id' if change['table'] == 'workspaces' else 'dataset_id'
                result = conn.execute(sql.SQL('UPDATE {} SET owner=%s WHERE {}=%s AND owner=%s').format(
                    sql.Identifier(change['table']), sql.Identifier(key)), (change['after'], change['id'], change['before']))
                if result.rowcount != 1:
                    raise AdoptionConflict('Concurrent resource change')
            after = repository.snapshot(conn, manifest['source_principal'], manifest['target_user_id'])
            if after['invariants'] != current['invariants']:
                raise AdoptionConflict('Business-state fingerprint changed')
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        return {**proposed, 'status': 'ADOPTED' if operation == 'ADOPT' else 'REVERSED'}
    except AdoptionConflict as exc:
        message = str(exc)
        scope = any(word in message.lower() for word in ('scope', 'membership', 'business state'))
        return {'status': 'SCOPE_CHANGED' if scope else 'CONFLICT', 'reason': message}
    except (Error, ValueError, KeyError, TypeError):
        return {'status': 'FAILED', 'reason': 'Operation rolled back; inspect private operator diagnostics'}
