"""Explicit LOCAL development bridge for authenticated warehouse UI -> BigQuery.

This is intentionally NOT an AWS/production runtime. It reuses the existing
tested FastAPI workflow and the original local DATABASE_URL, with three
independently impersonated GCP identities and no downloaded service-account
keys. Only a loopback server is allowed; the production restricted query-control
gateway and get_query_runtime() stay fail-closed in normal application startup.

Usage:
    python -m tools.warehouse_local_live_api --approve-billable-local-demo

A real query can be billed up to the server-approved byte ceiling. The operator
must first have authenticated the local application and published a compatible
fixed-scalar warehouse model in workspace 18. This runner does not create
models, grant IAM, edit role ACLs or submit jobs during startup.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT = "datarise-511817"
WORKSPACE = 18
LOCATION = "asia-south1"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
SENDER = f"datarise-query-sender@{PROJECT}.iam.gserviceaccount.com"
ATTESTER = f"datarise-query-attester@{PROJECT}.iam.gserviceaccount.com"
READER = f"datarise-result-reader@{PROJECT}.iam.gserviceaccount.com"
LIMIT_BYTES = 256 * 1024 * 1024


def _assert_local_development():
    from psycopg.conninfo import conninfo_to_dict
    from app.db.database import DATABASE_URL

    if os.environ.get("APP_ENV", "").lower() not in ("development", "local"):
        raise RuntimeError("EXPLICIT_LOCAL_DEVELOPMENT_ENV_REQUIRED")
    if os.environ.get("DATARISE_PUBLIC_LAUNCH_MODE", "off").lower() != "off":
        raise RuntimeError("PUBLIC_LAUNCH_MUST_BE_OFF")
    if os.environ.get("AUTH_MODE") != "session":
        raise RuntimeError("SESSION_AUTH_REQUIRED")
    if not DATABASE_URL:
        raise RuntimeError("LOCAL_DATABASE_CONFIGURATION_REQUIRED")
    db = conninfo_to_dict(DATABASE_URL)
    if (db.get("dbname") != "ai_data_workspace"
            or db.get("host", "localhost") not in ("localhost", "127.0.0.1")
            or db.get("hostaddr") not in (None, "127.0.0.1", "::1")):
        raise RuntimeError("LOOPBACK_DEVELOPMENT_DATABASE_REQUIRED")
    if any(os.environ.get(k) for k in (
            "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_KEYFILE_JSON",
            "GOOGLE_CREDENTIALS")):
        raise RuntimeError("KEYFILE_CREDENTIAL_OVERRIDES_FORBIDDEN")


def _credential(source, target):
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request
    credential = impersonated_credentials.Credentials(
        source_credentials=source, target_principal=target,
        target_scopes=[SCOPE], lifetime=900,
    )
    credential.refresh(Request())
    if not credential.valid or not credential.token:
        raise RuntimeError("REVIEWED_SERVICE_ACCOUNT_IMPERSONATION_REQUIRED")
    return credential


def _session(credentials):
    import requests
    from google.auth.transport.requests import Request
    credentials.refresh(Request())
    session = requests.Session()
    session.trust_env = False
    session.headers["Authorization"] = "Bearer " + credentials.token
    return session


class LocalDryRunEstimator:
    """Bounded BigQuery dry run; no actual user query is submitted by estimate."""
    def __init__(self, client):
        self.client = client

    def estimate(self, compiled):
        from google.cloud import bigquery
        from app.query_governance.types import QueryEstimate, EstimateConfidence
        if (type(compiled.sql) is not str
                or not compiled.sql.startswith("SELECT ")
                or not 1 <= len(compiled.sql) <= 16000):
            raise ValueError("COMPILED_SELECT_REQUIRED")
        config = bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, maximum_bytes_billed=LIMIT_BYTES,
        )
        job = self.client.query(
            compiled.sql, job_config=config, location=LOCATION,
            retry=None, job_retry=None, timeout=10,
        )
        amount = job.total_bytes_processed
        if type(amount) is not int or amount < 0 or amount > LIMIT_BYTES:
            raise ValueError("BOUNDED_BIGQUERY_ESTIMATE_REQUIRED")
        # Reserve at least the minimum on-demand billing unit instead of
        # claiming a free zero-byte estimate in the governance ledger.
        return QueryEstimate(max(10 * 1024 * 1024, amount),
                             EstimateConfidence.UPPER_BOUND)


class LocalSender:
    """Fresh independently impersonated short-lived sender token per job."""
    def __init__(self, credentials):
        self.credentials = credentials

    def insert_job_once(self, *, project, location, body):
        from app.warehouse.query_bigquery_job_insert_http import (
            BoundedBigQueryJobInsertTransport,
        )
        if (project, location) != (PROJECT, LOCATION):
            raise ValueError("REVIEWED_LOCAL_SENDER_SCOPE_REQUIRED")
        with _session(self.credentials) as session:
            return BoundedBigQueryJobInsertTransport(
                project=PROJECT, location=LOCATION, session=session
            ).insert_job_once(project=project, location=location, body=body)


class LocalCancellation:
    """Cancellation is bounded and never proves that the job was cancelled."""
    def __init__(self, client):
        self.client = client

    def cancel_job_once(self, *, project, location, job_id):
        if (project, location) != (PROJECT, LOCATION):
            raise ValueError("REVIEWED_LOCAL_CANCEL_SCOPE_REQUIRED")
        return self.client.cancel_job(
            job_id, project=PROJECT, location=LOCATION, retry=None, timeout=10,
        )


def make_runtime():
    import google.auth
    from google.cloud import bigquery
    from types import MappingProxyType
    from app.services import warehouse_query_workflow_service as workflow
    from app.services import warehouse_sql_provider_policy_service as policy
    from app.services import warehouse_sql_recovery_attestation_service as attest
    from app.warehouse.query_provider_policy import ProviderPolicy
    from app.warehouse.query_recovery_attestation import (
        LookupPolicy, LookupRuntime, LookupOnlyClient,
    )
    from app.warehouse.named_query_results import NamedResultPolicy
    from app.warehouse.query_bigquery_named_table_reader import (
        PinnedNamedTableResultReaderFactory,
        NamedTableBigQueryResultReader,
    )

    source, _ = google.auth.default(scopes=[SCOPE])
    sender = _credential(source, SENDER)
    attester = _credential(source, ATTESTER)
    reader = _credential(source, READER)
    sender_client = bigquery.Client(
        project=PROJECT, location=LOCATION, credentials=sender,
    )
    attester_client = bigquery.Client(
        project=PROJECT, location=LOCATION, credentials=attester,
    )

    class LocalRotatingReader(PinnedNamedTableResultReaderFactory):
        def __init__(self):
            import requests
            super().__init__(principal=READER, session=requests.Session())

        def bind(self, expected):
            from app.warehouse.query_bigquery_named_table_reader import (
                NamedTableBigQueryResultReader,
            )
            return NamedTableBigQueryResultReader(
                expected=expected, principal=READER, session=_session(reader),
            )

    policy._POLICIES = MappingProxyType({
        WORKSPACE: ProviderPolicy(PROJECT, SENDER, "local-live-1"),
    })
    policy._RESULT_POLICIES = MappingProxyType({
        WORKSPACE: NamedResultPolicy(READER, "local-live-1", 3600),
    })
    attest._RUNTIMES = MappingProxyType({
        (PROJECT, LOCATION): LookupRuntime(
            LookupPolicy(PROJECT, LOCATION, SENDER, "local-live-1"),
            LookupOnlyClient(attester_client),
        ),
    })
    runtime = workflow.InjectedQueryRuntime(
        metadata_client=attester_client,
        estimator=LocalDryRunEstimator(sender_client),
        submit_transport=LocalSender(sender),
        results_transport=LocalRotatingReader(),
        cancel_transport=LocalCancellation(sender_client),
        lease_seconds=30,
    )
    # Require the same typed policy consistency as the application workflow.
    policy._runtime(WORKSPACE)
    return runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-billable-local-demo", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not args.approve_billable_local_demo:
        parser.error("Explicit billable local-demo approval required")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    _assert_local_development()

    from app.main import app
    from app.api import warehouse_queries
    from app.services import warehouse_query_workflow_service as workflow

    runtime = make_runtime()
    # LOCAL ONLY: the existing API/session/SQL/budget checks still execute,
    # but the local admin fixture uses its established DATABASE_URL, not the
    # production restricted-role gateway. Never use this runner in AWS.
    warehouse_queries.query_control_boundary = lambda request: nullcontext()
    app.dependency_overrides[workflow.get_query_runtime] = lambda: runtime

    import uvicorn
    print("LOCAL LIVE WAREHOUSE: http://127.0.0.1:%d" % args.port, flush=True)
    print("SCOPE: workspace 18, fixed-scalar named results, explicit billing cap",
          flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, reload=False, workers=1)


if __name__ == "__main__":
    main()
