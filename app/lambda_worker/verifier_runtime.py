"""Per-invocation verifier credentials, verified TLS and caller-owned injections."""
import os
from contextlib import closing

import boto3
import psycopg
from app.processing_worker.runtime_db_secret import load_database_url
from app.processing_worker.runtime_clients import client_config
from app.processing_worker.secure_db_connection import secure_conninfo
from app.processing_worker.verification_service import RegistryVerifier
from app.storage.versioned_artifacts import VersionedArtifacts


def build_verifier(*, s3_client=None, secrets_client=None, connection_factory=None,
                   remaining_time_ms=None):
    if os.getenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED") != "true":
        raise PermissionError("Verifier deployment is not approved")
    if os.getenv("DATARISE_PROCESSING_READERS") != "true":
        raise PermissionError("Verifier readers are not approved")
    bucket = os.getenv("DATARISE_ARTIFACT_BUCKET")
    secret_arn = os.getenv("DATARISE_VERIFIER_DB_SECRET_ARN")
    if not bucket or not bucket.strip():
        raise ValueError("Verifier artifact bucket is required")
    if not secret_arn or not secret_arn.strip():
        raise ValueError("Verifier database secret ARN is required")
    if remaining_time_ms is not None and not callable(remaining_time_ms):
        raise TypeError("Verifier invocation clock must be callable")

    def checkpoint():
        if remaining_time_ms is not None:
            remaining = remaining_time_ms()
            if type(remaining) is not int or remaining <= 5000:
                raise TimeoutError("Verifier execution budget unavailable")
    checkpoint()
    if connection_factory is None:
        if secrets_client is None:
            with closing(boto3.client("secretsmanager", config=client_config("secretsmanager"))) as client:
                url = load_database_url(client, secret_arn, "datarise_verifier")
        else:
            url = load_database_url(secrets_client, secret_arn, "datarise_verifier")
        verified_url = secure_conninfo(url, "/opt/certs/global-bundle.pem")

        def connection_factory():
            checkpoint()
            return psycopg.connect(verified_url)

    owned_s3 = s3_client is None
    s3 = boto3.client("s3", config=client_config("s3")) if owned_s3 else s3_client
    try:
        service = RegistryVerifier(VersionedArtifacts(s3, bucket, checkpoint=checkpoint),
                                   connection_factory, isolated_test=False)
    except BaseException:
        if owned_s3:
            s3.close()
        raise

    def verify(candidate_id):
        # This callable is invocation-scoped. Injected clients remain caller-owned.
        try:
            checkpoint()
            result = service.verify(candidate_id)
            checkpoint()
            return result
        finally:
            if owned_s3:
                s3.close()
    return verify
