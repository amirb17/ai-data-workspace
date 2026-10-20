from contextlib import contextmanager
from psycopg.rows import dict_row
from app.db.database import get_connection


@contextmanager
def transaction():
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            yield cursor


def event(cursor, kind, user_id=None, category=None):
    cursor.execute('INSERT INTO auth_security_events(user_id,event_type,category) VALUES (%s,%s,%s)', (user_id,kind,category))


def user(cursor, **lookup):
    column, value = next(iter(lookup.items()))
    if column not in ('user_id','normalized_email'):
        raise ValueError('Unsupported user lookup')
    return cursor.execute('SELECT * FROM users WHERE '+column+'=%s FOR UPDATE',(value,)).fetchone()


def principal(row):
    return {key:row[key] for key in ('user_id','owner_key','display_name','email','email_verified','avatar_url','account_status')}
