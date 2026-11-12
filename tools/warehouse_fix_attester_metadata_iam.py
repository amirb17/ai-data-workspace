"""Grant metadata-only BigQuery serving access to the independent attester.

Default: read-only IAM inspection. Only --apply mutates one dataset access ACL.
Uses the human operator's existing Google ADC with dataset-update authority,
never a service-account key, sender impersonation or unrestricted project grant.

Usage:
  python -m tools.warehouse_fix_attester_metadata_iam
  python -m tools.warehouse_fix_attester_metadata_iam --apply

After --apply, validates the SAME dataset and impersonated attester can
retrieve metadata for the original publication-pinned Customers Dimension table.
Never executes SQL, submits jobs, retrieves rows or changes publications.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from tools.warehouse_local_live_api import (
    _assert_local_development,
    _bind_canonical_local_database,
    _credential,
    ATTESTER,
    LOCATION,
    PROJECT,
    SCOPE,
)


DATASET = f"{PROJECT}.dr_dev_w18"
ROLE = "roles/bigquery.metadataViewer"


def _load_local_env():
    root = Path(__file__).resolve().parents[1] / ".env"
    fallback = Path("D:/Project/ai-data-workspace/.env")
    if root.is_file():
        load_dotenv(root)
    elif fallback.is_file():
        load_dotenv(fallback)
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("AUTH_MODE", "session")
    os.environ.setdefault("DATARISE_PUBLIC_LAUNCH_MODE", "off")
    _assert_local_development()


def _pinned_original_table():
    from app.db import database
    from psycopg import connect
    from psycopg.rows import dict_row
    from app.services import warehouse_query_workflow_service as workflow

    _bind_canonical_local_database()
    with connect(database.DATABASE_URL, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT owner,status FROM public.workspaces WHERE workspace_id=18"
        ).fetchone()
        if not row or row["status"] != "ACTIVE":
            raise RuntimeError("WORKSPACE_NOT_ACTIVE")
        resolved = workflow._resolve(
            conn, {"owner_key": row["owner"]}, 18, 1,
            'SELECT "customer_id" FROM model_1 LIMIT 100',
        )
        model = resolved[1].models[0]
        if model.model_id != 1 or not model.physical_table.startswith(DATASET + "."):
            raise RuntimeError("ORIGINAL_PUBLISHED_TABLE_REQUIRED")
        return model.physical_table


def _has_grant(dataset):
    return any(
        entry.entity_type == "userByEmail"
        and entry.entity_id == ATTESTER
        and entry.role in (ROLE, "roles/bigquery.dataViewer", "READER",
                           "roles/bigquery.dataEditor", "WRITER",
                           "roles/bigquery.dataOwner", "OWNER")
        for entry in dataset.access_entries
    )


def run(apply=False):
    import google.auth
    from google.cloud import bigquery

    _load_local_env()
    table_name = _pinned_original_table()
    source, _ = google.auth.default(scopes=[SCOPE])
    admin = bigquery.Client(project=PROJECT, location=LOCATION, credentials=source)
    dataset = admin.get_dataset(DATASET, retry=None, timeout=10)
    if dataset.full_dataset_id != PROJECT + ":dr_dev_w18" or dataset.location != LOCATION:
        raise RuntimeError("EXPECTED_SERVING_DATASET_SCOPE_REQUIRED")

    result = {
        "project": PROJECT, "dataset": "dr_dev_w18",
        "principal": ATTESTER, "role": ROLE,
        "metadata_grant_present": _has_grant(dataset),
        "changed": False, "query_submitted": False,
    }
    if apply and not result["metadata_grant_present"]:
        # The dataset etag from get_dataset prevents overwriting a concurrent
        # IAM edit. Only append the metadataViewer entry; retain other access.
        entries = list(dataset.access_entries)
        entries.append(bigquery.AccessEntry(
            role=ROLE, entity_type="userByEmail", entity_id=ATTESTER,
        ))
        dataset.access_entries = entries
        admin.update_dataset(dataset, ["access_entries"], retry=None, timeout=10)
        result["changed"] = True

    if not apply:
        result["status"] = "READ_ONLY"
        return result

    # Prove metadata GET as the original independent attester, not the admin.
    service_account = _credential(source, ATTESTER)
    attester = bigquery.Client(
        project=PROJECT, location=LOCATION, credentials=service_account,
    )
    table = attester.get_table(table_name, retry=None, timeout=10)
    if table.full_table_id != table_name.replace(".", ":", 1):
        raise RuntimeError("ATTESTER_RETURNED_UNEXPECTED_TABLE")
    result["status"] = "ATTESTER_METADATA_VERIFIED"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.apply), sort_keys=True, indent=2))
        return 0
    except Exception as error:
        # Never include provider exception text: may contain credentials or
        # sensitive resource information. Permission failures require the
        # operator to use a GCP admin authorized for dataset ACL updates.
        print("BIGQUERY_METADATA_IAM_BLOCKED:", type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
