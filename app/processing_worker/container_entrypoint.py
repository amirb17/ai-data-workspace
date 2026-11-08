"""Explicitly approved container composition; no SDK work while disabled."""
import importlib
import logging
import os
import sys
from contextlib import ExitStack, closing

import boto3
from app.processing_worker.runtime_clients import client_config

logger = logging.getLogger(__name__)


def initialize_database(session, *, purpose='worker'):
    if purpose not in {'worker', 'dispatch'}:
        raise ValueError('Unsupported database runtime purpose')
    if any(name in sys.modules for name in ("app.db.database", "app.config")):
        raise RuntimeError("Application configuration initialized before worker credentials")
    from app.processing_worker.worker_secrets import load_worker_database_url
    from app.processing_worker.secure_db_connection import secure_conninfo
    with closing(session.client("secretsmanager", config=client_config("secretsmanager"))) as client:
        if purpose == 'worker':
            url = load_worker_database_url(client)
        else:
            from app.processing_worker.runtime_db_secret import load_database_url
            url = load_database_url(client, os.getenv('DATARISE_DISPATCH_DB_SECRET_ARN'), 'datarise_dispatch')
    os.environ["DATABASE_URL"] = secure_conninfo(url, "/app/certs/global-bundle.pem")
    try:
        database = importlib.import_module("app.db.database")
    finally:
        # The repository retains its private connection value. Do not leave a
        # second credential copy available to subsequently spawned processes.
        os.environ.pop("DATABASE_URL", None)
    security = importlib.import_module("app.processing_worker.runtime_security")
    with database.get_connection() as conn:
        security.require_runtime(conn, purpose)


def main() -> int:
    if os.getenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED") != "true":
        raise PermissionError("Worker deployment is disabled")
    if os.getenv("DATARISE_PROCESSING_READERS") != "true":
        raise PermissionError("Processing readers are disabled")
    required = ("DATARISE_PROCESSING_QUEUE_URL", "DATARISE_ARTIFACT_BUCKET", "DATARISE_VERIFIER_FUNCTION_NAME")
    values = [os.getenv(name, "") for name in required]
    if not all(v and v == v.strip() for v in values):
        raise ValueError("Worker configuration is incomplete")
    queue_url, bucket, verifier_name = values
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not region or any(os.getenv(name) not in (None, region) for name in ("AWS_REGION", "AWS_DEFAULT_REGION")):
        raise ValueError("An unambiguous AWS runtime region is required")
    if os.getenv("S3_BUCKET_NAME") not in (None, bucket):
        raise ValueError("Artifact bucket configuration differs")
    # Bridge legacy constants BEFORE processing imports; never use development
    # region/bucket defaults in the production adapter.
    os.environ["AWS_REGION"] = region
    os.environ["S3_BUCKET_NAME"] = bucket
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    session = boto3.Session(region_name=region)
    initialize_database(session)  # TLS and runtime-role preflight before receive.
    from app.processing_worker.bootstrap import build_worker
    from app.processing_worker.storage_factory import make_storage_factory
    from app.processing_worker.verifier_client import LambdaVerifierClient
    from app.processing_worker.worker import run_worker
    with ExitStack() as resources:
        s3 = session.client("s3", config=client_config("s3"))
        resources.callback(s3.close)
        remote = session.client("lambda", config=client_config("lambda"))
        resources.callback(remote.close)
        sqs = session.client("sqs", config=client_config("sqs"))
        try:
            worker = build_worker(sqs_client=sqs, queue_url=queue_url,
                storage_factory=make_storage_factory(s3, bucket),
                verifier=LambdaVerifierClient(remote, verifier_name))
        except BaseException:
            sqs.close()
            raise
        # The signal runner/transport owns SQS cleanup, including setup failure.
        logger.info("Processing worker starting")
        result = run_worker(worker)
        logger.info("Processing worker stopped")
        return result


def cli() -> int:
    try:
        return main()
    except Exception as exc:
        # No SDK bodies, secret values, SQL messages or exception chains in logs.
        logger.error("Worker startup or execution refused (%s)", type(exc).__name__)
        return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(cli())
