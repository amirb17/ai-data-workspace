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
             AND e.workspace_id=q.workspace_id AND e.owner_key=q.owner_key) AS last_run
            FROM saved_sql_queries q WHERE workspace_id=%s AND owner_key=%s ORDER BY updated_at DESC,query_id DESC LIMIT %s''',
            (workspace,user['owner_key'],LIMITS.saved_queries)).fetchall()


def owned_query(conn, workspace, user, query_id, lock=False):
    row=conn.cursor(row_factory=dict_row).execute('SELECT * FROM saved_sql_queries WHERE query_id=%s AND workspace_id=%s AND owner_key=%s'+(' FOR UPDATE' if lock else ''),
        (query_id,workspace,user['owner_key'])).fetchone()
    if not row: raise HTTPException(404,'Saved query not found in this workspace.')
    return row


def save(workspace, user, request, query_id=None):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        c=conn.cursor(row_factory=dict_row)
        # Membership parent lock serializes owner-scoped quota and changes with workspace moves.
        conn.execute('SELECT workspace_id FROM workspaces WHERE workspace_id=%s FOR UPDATE',(workspace,))
        if query_id:
            row=owned_query(conn,workspace,user,query_id,True)
            if request.expected_version!=row['version']: raise HTTPException(409,'This saved query changed. Reopen it before saving.')
            return c.execute('''UPDATE saved_sql_queries SET name=%s,description=%s,sql_text=%s,version=version+1,updated_at=NOW()
                WHERE query_id=%s RETURNING *''',(request.name,request.description,request.sql_text,query_id)).fetchone()
        if c.execute('SELECT count(*) AS n FROM saved_sql_queries WHERE workspace_id=%s AND owner_key=%s',(workspace,user['owner_key'])).fetchone()['n']>=LIMITS.saved_queries:
            raise HTTPException(409,'Saved query limit reached. Delete an unneeded saved query first.')
        return c.execute('''INSERT INTO saved_sql_queries(workspace_id,owner_key,name,description,sql_text)
            VALUES (%s,%s,%s,%s,%s) RETURNING *''',(workspace,user['owner_key'],request.name,request.description,request.sql_text)).fetchone()


def delete(workspace,user,query_id,request):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        row=owned_query(conn,workspace,user,query_id,True)
        if request.expected_version!=row['version']: raise HTTPException(409,'Saved query changed. Reopen it before deleting.')
        conn.execute('DELETE FROM saved_sql_queries WHERE query_id=%s AND workspace_id=%s AND owner_key=%s',(query_id,workspace,user['owner_key']))
    return {'deleted':True}


def record(workspace,user,text,query_hash,payload,saved_query_id=None,check_current=None):
    with repository_transaction() as conn:
        parent=conn.execute('SELECT workspace_id FROM workspaces WHERE workspace_id=%s AND owner=%s FOR UPDATE',(workspace,user['owner_key'])).fetchone()
        if not parent: raise HTTPException(404,'Workspace not found.')
        if check_current: check_current(conn)
        if saved_query_id: owned_query(conn,workspace,user,saved_query_id)
        row=conn.execute('''INSERT INTO sql_query_executions(workspace_id,owner_key,saved_query_id,sql_text,query_hash,payload)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING execution_id,created_at''',
            (workspace,user['owner_key'],saved_query_id,text,query_hash,Jsonb(payload))).fetchone()
        conn.execute('''DELETE FROM sql_query_executions WHERE workspace_id=%s AND owner_key=%s AND execution_id IN (
            SELECT execution_id FROM sql_query_executions WHERE workspace_id=%s AND owner_key=%s
            ORDER BY execution_id DESC OFFSET %s)''',(workspace,user['owner_key'],workspace,user['owner_key'],LIMITS.history))
    return dict(execution_id=row[0],executed_at=row[1])


def history(workspace,user,execution_id=None):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        rows=conn.cursor(row_factory=dict_row).execute('''SELECT * FROM sql_query_executions WHERE workspace_id=%s AND owner_key=%s'''+
            (' AND execution_id=%s' if execution_id else '')+' ORDER BY execution_id DESC LIMIT %s',
            (workspace,user['owner_key'],*((execution_id,) if execution_id else ()),LIMITS.history)).fetchall()
    if execution_id and not rows: raise HTTPException(404,'Execution not found in this workspace.')
    return rows
