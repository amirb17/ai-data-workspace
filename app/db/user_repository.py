from app.db.database import get_connection


def get_or_create_dev_user(owner_key: str, display_name: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (owner_key, display_name) VALUES (%s, %s)
                ON CONFLICT (owner_key) DO UPDATE SET owner_key = EXCLUDED.owner_key
                RETURNING user_id, owner_key, display_name;
            """, (owner_key, display_name))
            row = cur.fetchone()
    return {"user_id": row[0], "owner_key": row[1], "display_name": row[2]}


def get_user_by_id(user_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, owner_key, display_name FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    return None if row is None else {"user_id": row[0], "owner_key": row[1], "display_name": row[2]}
