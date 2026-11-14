"""Principal/workspace-scoped query text and immutable execution metadata; no source rows."""
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.services.workspace_understanding_service import validate_workspace
from app.processing.sql_policy import LIMITS


def queries(workspace, user):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        return conn.cursor(row_factory=dict_row).execute('''SELECT q.*,
            (SELECT max(created_at) FROM sql_query_executions e WHERE e.saved_query_id=q.query_id
             AND e.workspace_id=q.workspace_id) AS last_run
            FROM saved_sql_queries q JOIN workspaces w USING(workspace_id)
            WHERE workspace_id=%s AND w.owner=%s ORDER BY updated_at DESC,query_id DESC LIMIT %s''',
            (workspace,user['owner_key'],LIMITS.saved_queries)).fetchall()


def owned_query(conn, workspace, user, query_id, lock=False):
    row=conn.cursor(row_factory=dict_row).execute('''SELECT q.* FROM saved_sql_queries q
        JOIN workspaces w USING(workspace_id) WHERE query_id=%s AND workspace_id=%s AND w.owner=%s'''+(' FOR UPDATE OF q,w' if lock else ''),
        (query_id,workspace,user['owner_key'])).fetchone()
    if not row: raise HTTPException(404,'Saved query not found in this workspace.')
    return row


def save(workspace, user, request, query_id=None):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            'SELECT * FROM task23_sql_save(%s,%s,%s,%s,%s,%s,%s,%s)',
            (user['user_id'],workspace,query_id,request.name,request.description,
             request.sql_text,request.expected_version,LIMITS.saved_queries)
        ).fetchone()


def delete(workspace,user,query_id,request):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        conn.execute('SELECT task23_sql_delete(%s,%s,%s,%s)',
                     (user['user_id'],workspace,query_id,request.expected_version))
    return {'deleted':True}


def record(workspace,user,text,query_hash,payload,saved_query_id=None,check_current=None):
    with repository_transaction() as conn:
        # Authoritative owner + ordered workspace/dataset locks serialize publication.
        # The API role itself has SELECT only on trusted dataset metadata.
        conn.execute('SELECT task23_sql_lock_scope(%s,%s)', (user['user_id'],workspace))
        if check_current: check_current(conn)
        if saved_query_id: owned_query(conn,workspace,user,saved_query_id)
        row=conn.execute(
            'SELECT execution_id,created_at FROM task23_sql_record(%s,%s,%s,%s,%s,%s,%s)',
            (user['user_id'],workspace,saved_query_id,text,query_hash,
             Jsonb(payload),LIMITS.history)
        ).fetchone()
    return dict(execution_id=row[0],executed_at=row[1])


def history(workspace,user,execution_id=None):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        rows=conn.cursor(row_factory=dict_row).execute('''SELECT e.* FROM sql_query_executions e
            JOIN workspaces w USING(workspace_id) WHERE workspace_id=%s AND w.owner=%s'''+
            (' AND execution_id=%s' if execution_id else '')+' ORDER BY execution_id DESC LIMIT %s',
            (workspace,user['owner_key'],*((execution_id,) if execution_id else ()),LIMITS.history)).fetchall()
    if execution_id and not rows: raise HTTPException(404,'Execution not found in this workspace.')
    return rows
