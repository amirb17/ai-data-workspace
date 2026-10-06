"""Execution snapshots and safe delivery read-model queries."""
from app.db.database import get_connection

GOLD_SKIP_REASON = "No valid rows were available for Gold publication."


def record_gold_skip(association_id):
    with get_connection() as conn:
        conn.execute("""UPDATE dataset_version_files SET status='SUCCESS_WITH_WARNINGS',
            gold_skip_reason=%s, gold_skipped_at=COALESCE(gold_skipped_at, NOW())
            WHERE dataset_version_file_id=%s""", (GOLD_SKIP_REASON, association_id))


def get_gold_skip(association_id):
    with get_connection() as conn:
        return conn.execute("SELECT gold_skip_reason, gold_skipped_at FROM dataset_version_files WHERE dataset_version_file_id=%s",
                            (association_id,)).fetchone()


def list_dataset_deliveries(user_id, workspace_id, dataset_id):
    with get_connection() as conn:
        return conn.execute("""SELECT u.upload_id, COALESCE(u.source_file_name, f.file_name), u.created_at
            FROM upload_requests u JOIN physical_files f ON f.file_id=u.file_id
            WHERE u.user_id=%s AND u.workspace_id=%s AND u.dataset_id=%s AND u.status='UPLOADED'
            ORDER BY u.upload_id""", (user_id, workspace_id, dataset_id)).fetchall()


def get_approved_policy(dataset_version_id, rule_version):
    with get_connection() as conn:
        row = conn.execute("SELECT rules FROM approved_rule_policies WHERE dataset_version_id=%s AND rule_version=%s",
                           (dataset_version_id, rule_version)).fetchone()
        return [(None, dataset_version_id, r["column_name"], r["rule_type"], r["rule_config"], True)
                for r in row[0]] if row else []


def get_delivery_attempts(association_id, file_id):
    with get_connection() as conn:
        return conn.execute("""SELECT DISTINCT ON (stage) attempt_id, stage, status, started_at, completed_at
            FROM processing_attempts WHERE dataset_version_file_id=%s
                OR (file_id=%s AND dataset_version_file_id IS NULL AND stage='BRONZE')
            ORDER BY stage, attempt_id DESC""", (association_id, file_id)).fetchall()


def get_delivery_issues(dq_run_id):
    with get_connection() as conn:
        return conn.execute("SELECT rule_type, SUM(violation_count) FROM data_quality_issues WHERE dq_run_id=%s GROUP BY rule_type ORDER BY rule_type",
                            (dq_run_id,)).fetchall()


def get_gold_publication(association_id, dq_run_id):
    # Includes failed partial publication so recovery does not insert a second run.
    with get_connection() as conn:
        return conn.execute("""SELECT gold_run_id,file_id,attempt_id,source_dq_run_id,gold_type,row_count,
            gold_path,dataset_version_file_id,created_at FROM gold_runs
            WHERE dataset_version_file_id=%s AND source_dq_run_id=%s ORDER BY gold_run_id DESC LIMIT 1""",
            (association_id, dq_run_id)).fetchone()
