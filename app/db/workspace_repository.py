from app.db.database import get_connection


def create_workspace(
    workspace_name: str,
    description: str | None = None,
    owner: str | None = None,
):
    """
    Create a workspace. Races on (owner, workspace_name) are resolved
    via ON CONFLICT DO NOTHING + fallback select rather than
    surfacing a unique-constraint error.

    Returns (workspace, was_inserted).
    """
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO workspaces (
                    workspace_name,
                    description,
                    owner
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (owner, workspace_name) DO NOTHING
                RETURNING
                    workspace_id,
                    workspace_name,
                    description,
                    owner,
                    status,
                    created_at,
                    updated_at;
                """,
                (
                    workspace_name,
                    description,
                    owner,
                ),
            )

            workspace = cur.fetchone()
            was_inserted = workspace is not None

            if workspace is None:
                cur.execute(
                    """
                    SELECT
                        workspace_id,
                        workspace_name,
                        description,
                        owner,
                        status,
                        created_at,
                        updated_at
                    FROM workspaces
                    WHERE owner = %s
                      AND workspace_name = %s;
                    """,
                    (owner, workspace_name),
                )

                workspace = cur.fetchone()

            conn.commit()

            return workspace, was_inserted

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_workspace_by_id(workspace_id: int):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    workspace_id,
                    workspace_name,
                    description,
                    owner,
                    status,
                    created_at,
                    updated_at
                FROM workspaces
                WHERE workspace_id = %s;
                """,
                (workspace_id,),
            )

            return cur.fetchone()

    finally:
        conn.close()


def get_workspace_by_owner_and_name(
    owner: str,
    workspace_name: str,
):
    """
    Look up a workspace scoped to its owner.

    Workspace names are only unique per-owner (UNIQUE(owner,
    workspace_name)); a global name lookup would leak/collide across
    users and must not be used for multi-user isolation.
    """
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    workspace_id,
                    workspace_name,
                    description,
                    owner,
                    status,
                    created_at,
                    updated_at
                FROM workspaces
                WHERE owner = %s
                  AND workspace_name = %s;
                """,
                (owner, workspace_name),
            )

            return cur.fetchone()

    finally:
        conn.close()