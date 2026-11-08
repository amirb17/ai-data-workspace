
"""Load the worker-only PostgreSQL credential from Secrets Manager."""

import os

from app.processing_worker.runtime_db_secret import load_database_url


def load_worker_database_url(secrets_client):
    secret_arn = os.getenv("DATARISE_WORKER_DB_SECRET_ARN")

    if not secret_arn or not secret_arn.strip():
        raise ValueError("Worker database secret ARN is required")

    if secrets_client is None:
        raise ValueError("Secrets Manager client is required")

    try:
        return load_database_url(secrets_client, secret_arn, "datarise_worker")
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid worker database secret") from None
