"""414E1 trusted backend metadata repository. No provider or authorization permit."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from app.db.database import repository_transaction, require_connection_boundary
from app.db import query_governance_repository as budgets


class RecoveryConflict(ValueError):
    pass


def one(conn,sql,params=()):
    return conn.cursor(row_factory=dict_row).execute(sql,params).fetchone()


def checkpoint(conn,attempt_id):
    c=one(conn,"""SELECT c.*,a.owner_key,a.request_token FROM warehouse_query_dispatch_checkpoints c
        JOIN warehouse_query_attempt_registry a USING(attempt_id) WHERE c.attempt_id=%s""",(attempt_id,))
    if not c:raise RecoveryConflict('CHECKPOINT_REQUIRED_NO_RESUBMISSION')
    return c


@contextmanager
def transaction(workspace_id=None,*,duty='query_control'):
    require_connection_boundary(duty)
    with repository_transaction() as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='10s'")
        if workspace_id is None:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('datarise-query-governance-global',))
        else:
            budgets._lock_scopes(conn,workspace_id)
        yield conn


def lock_identity(conn,c):
    for (resource,) in conn.execute('SELECT resource_id FROM warehouse_query_attempt_publication_pins WHERE attempt_id=%s ORDER BY resource_id',(c['attempt_id'],)):
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f'datarise-publication-retirement:{resource}',))
    conn.execute('SELECT 1 FROM warehouse_query_attempt_registry WHERE attempt_id=%s FOR UPDATE',(c['attempt_id'],))
    r=one(conn,'SELECT * FROM query_governance_reservations WHERE reservation_id=%s FOR UPDATE',(c['reservation_id'],))
    if (not r or r['workspace_id']!=c['workspace_id'] or r['owner_key']!=c['owner_key']
            or r['request_token']!=c['request_token'].strip() or r['intent_sha256']!=c['compiled_intent_sha256']
            or r['consumer']!='SQL' or r['provider']!='BIGQUERY'):
        raise RecoveryConflict('CHECKPOINT_RESERVATION_IDENTITY_MISMATCH')
    return r


def latest(conn,attempt_id,*,lock=True):
    intent=one(conn,'SELECT * FROM warehouse_query_send_intents WHERE attempt_id=%s'+(' FOR UPDATE' if lock else ''),(attempt_id,))
    if not intent:raise RecoveryConflict('SEND_INTENT_REQUIRED_NO_RESUBMISSION')
    event=one(conn,'SELECT * FROM warehouse_query_send_journal WHERE attempt_id=%s ORDER BY revision DESC LIMIT 1',(attempt_id,))
    if not event:raise RecoveryConflict('SEND_JOURNAL_MISSING')
    return intent,event


def append(conn,attempt_id,previous,event,phase,*,observation=None,token=None,until=None):
    if previous is None:
        revision,generation=1,0
    else:
        revision=previous['revision']+1
        generation=previous['lease_generation']+(event=='LEASE_ACQUIRED')
        if event!='LEASE_ACQUIRED':token,until=previous['lease_token'],previous['lease_until']
    return one(conn,"""INSERT INTO warehouse_query_send_journal(attempt_id,revision,event,phase,observation,
        lease_token,lease_generation,lease_until) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (attempt_id,revision,event,phase,observation,token,generation,until))


def require_revision(event,revision):
    if type(revision) is not int or revision<1 or event['revision']!=revision:
        raise RecoveryConflict('STALE_RECOVERY_REVISION_INSPECT_ONLY')


def require_owner(conn,event,lease,revision):
    require_revision(event,revision)
    if (event['lease_token']!=lease.token or event['lease_generation']!=lease.generation
            or not conn.execute('SELECT %s>clock_timestamp()',(event['lease_until'],)).fetchone()[0]):
        raise RecoveryConflict('STALE_OR_EXPIRED_RECOVERY_OWNER')
