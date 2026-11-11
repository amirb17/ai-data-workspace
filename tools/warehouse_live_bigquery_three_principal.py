"""Live BigQuery transport demo using THREE independently impersonated identities.

This is a *cloud-only* smoke. It is NOT the authenticated Warehouse HTTP API:
PostgreSQL role routing, user authorization and accounting are NOT exercised.
Do not count success here as a deployed Datarise query feature.

Requires a source table in datarise-511817.dr_dev_w18, the result dataset
datarise-511817.dr_results_dev_w18, and permission to impersonate the
three configured service accounts using gcloud ADC (no key downloads).

Example (explicitly billable and creates one named result table):
 python tools/warehouse_live_bigquery_three_principal.py \\
   --source-table model_123_v1_g1 --approve-billable-demo

Source data is not printed. A single COUNT(*) query is capped at 10 MiB billed.
The destination table persists after the demo; this runner does not delete it.
Unknown submission outcomes are never retried; the original job is only looked up.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
import re
import secrets
import sys
import time

import google.auth
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request
from google.cloud import bigquery
import requests

from app.warehouse.named_query_results import (
    NamedResultPolicy, STORAGE_BYTES, WORKSPACE_STORAGE_BYTES,
    result_identity, validate_result_contract, verify_materialization,
)
from app.warehouse.query_bigquery_first_page_adapter import validate_bigquery_first_page
from app.warehouse.query_bigquery_job_insert_http import BoundedBigQueryJobInsertTransport
from app.warehouse.query_bigquery_named_table_reader import NamedTableBigQueryResultReader
from app.warehouse.query_recovery_attestation import (
    ExpectedJob, LookupPolicy, verify_metadata,
)

PROJECT = "datarise-511817"
LOCATION = "asia-south1"
WORKSPACE = 18
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
PRINCIPALS = {
    "sender": f"datarise-query-sender@{PROJECT}.iam.gserviceaccount.com",
    "attester": f"datarise-query-attester@{PROJECT}.iam.gserviceaccount.com",
    "reader": f"datarise-result-reader@{PROJECT}.iam.gserviceaccount.com",
}
_TABLE = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}\Z")


def token(source, principal):
    credentials = impersonated_credentials.Credentials(
        source_credentials=source, target_principal=principal,
        target_scopes=[SCOPE], lifetime=900,
    )
    credentials.refresh(Request())
    if (not credentials.valid or not isinstance(credentials.token, str)
            or len(credentials.token) < 16):
        raise RuntimeError("SERVICE_ACCOUNT_IMPERSONATION_FAILED")
    return credentials


def session(credential):
    s = requests.Session()
    s.trust_env = False
    s.headers["Authorization"] = "Bearer " + credential.token
    return s


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-table", required=True)
    parser.add_argument("--approve-billable-demo", action="store_true")
    parser.add_argument("--resume-job-id", help="Inspect one original demo job without another submission")
    args = parser.parse_args()
    if not args.approve_billable_demo and not args.resume_job_id:
        parser.error("Explicit --approve-billable-demo required for new submissions")
    if args.resume_job_id and not re.fullmatch(r"dr_query_w18_r1_[a-f0-9]{32}", args.resume_job_id):
        parser.error("Resume requires an original bounded workspace-18 demo job ID")
    if not _TABLE.fullmatch(args.source_table):
        parser.error("Source table must be an exact unqualified BigQuery table identifier")
    # Never permit this runner to use downloaded service-account keys or
    # manually injected credential JSON.
    if any(os.getenv(key) for key in (
        "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_KEYFILE_JSON",
        "GOOGLE_CREDENTIALS",
    )):
        parser.error("No service account JSON keys or credential-file overrides allowed")
    stage = "CREDENTIAL_SOURCE"
    job_id = args.resume_job_id
    try:
        source, _ = google.auth.default(scopes=[SCOPE])
        # Source ADC may be a local authorized-user credential; individual
        # cloud calls below use only the three short-lived target identities.
        stage = "IMPERSONATE_SENDER"
        sender = token(source, PRINCIPALS["sender"])
        stage = "IMPERSONATE_ATTESTER"
        attester = token(source, PRINCIPALS["attester"])
        stage = "IMPERSONATE_READER"
        reader = token(source, PRINCIPALS["reader"])
        stage = "CREATE_ATTESTER_CLIENT"
        metadata = bigquery.Client(project=PROJECT, location=LOCATION,
                                   credentials=attester)
        try:
            source_table = f"{PROJECT}.dr_dev_w{WORKSPACE}.{args.source_table}"
            sql = f"SELECT COUNT(*) AS demo_value FROM `{source_table}`"
            reservation = 1
            intent = hashlib.sha256(sql.encode()).hexdigest()
            # A unique job ID avoids overwriting an earlier cloud result.
            job_id = job_id or f"dr_query_w{WORKSPACE}_r{reservation}_{secrets.token_hex(16)}"
            destination = result_identity(PROJECT, WORKSPACE, job_id)
            contract = {
                "version": 1, "destination": destination,
                "schema": [["demo_value", "INTEGER"]], "maximum_rows": 1,
                "maximum_storage_bytes": STORAGE_BYTES,
                "workspace_storage_bytes": WORKSPACE_STORAGE_BYTES,
                "read_until_ms": int(time.time()*1000) + 3600000,
                "policy": {
                    "reader_principal": PRINCIPALS["reader"],
                    "revision": "live-demo-1", "read_seconds": 3600,
                },
            }
            validate_result_contract(contract, PROJECT,
                                     f"dr_dev_w{WORKSPACE}", job_id)
            labels = dict(datarise="query", workspace=str(WORKSPACE),
                          reservation=str(reservation), intent=intent[:63])
            request = {
                "jobReference": {
                    "projectId": PROJECT, "location": LOCATION, "jobId": job_id,
                },
                "configuration": {
                    "jobType": "QUERY", "dryRun": False,
                    "jobTimeoutMs": "60000", "labels": labels,
                    "query": {
                        "query": sql, "useLegacySql": False,
                        "useQueryCache": False, "priority": "INTERACTIVE",
                        "allowLargeResults": False,
                        "maximumBytesBilled": str(10 * 1024 * 1024),
                        "destinationTable": destination,
                        "createDisposition": "CREATE_IF_NEEDED",
                        "writeDisposition": "WRITE_EMPTY",
                    },
                },
            }
            # This runner is cloud transport only: the request is synthetic,
            # not a user-admitted request saved in PostgreSQL. No production
            # execution enablement is inferred from this test.
            with session(sender) as sender_http, session(reader) as reader_http:
                transport = BoundedBigQueryJobInsertTransport(
                    project=PROJECT, location=LOCATION, session=sender_http)
                if not args.resume_job_id:
                    stage = "SUBMISSION"
                    print("JOB:", job_id, flush=True)
                    print("LIVE: sending one bounded BigQuery job; no retry", flush=True)
                    try:
                        transport.insert_job_once(
                            project=PROJECT, location=LOCATION, body=request)
                    except Exception:
                        print("SEND UNKNOWN: verifying only the original job", flush=True)
                else:
                    print("RESUME: original job only; no new query will be submitted", flush=True)
                    print("JOB:", job_id, flush=True)
                stage = "INDEPENDENT_JOB_LOOKUP"
                policy = LookupPolicy(
                    PROJECT, LOCATION, PRINCIPALS["sender"], "live-demo-1")
                expected = ExpectedJob(
                    PROJECT, LOCATION, job_id, WORKSPACE, reservation,
                    intent, hashlib.sha256(sql.encode()).hexdigest(),
                    (source_table,), 10*1024*1024,
                    0 if args.resume_job_id else int(time.time()*1000)-60000,
                )
                report = None
                for attempt in range(15):
                    try:
                        job = metadata.get_job(
                            job_id, project=PROJECT, location=LOCATION,
                            retry=None, timeout=10)
                        raw = dict(job._properties)
                        report = verify_metadata(
                            raw, expected, policy, named_result=contract)
                    except Exception as error:
                        # Class name only; do not expose service account tokens,
                        # HTTP payloads or provider error strings.
                        print("LOOKUP ERROR:", type(error).__name__, flush=True)
                        report = None
                    if report:
                        print("ATTESTATION:", report.verdict, report.observation,
                              report.reason, flush=True)
                        if report.verdict == "REJECTED":
                            break
                        if report.observation in ("SUCCEEDED", "FAILED", "CANCELLED"):
                            break
                    if attempt < 14:
                        time.sleep(2)
                if (report is None or report.verdict != "VERIFIED_METADATA"
                        or not report.billing_complete
                        or report.observation != "SUCCEEDED"):
                    raise RuntimeError("INDEPENDENT_JOB_ATTESTATION_NOT_SUCCESSFUL")
                stage = "RESULT_METADATA_VERIFICATION"
                full_expected = {
                    "contract_version": 2, "project": PROJECT,
                    "dataset": f"dr_dev_w{WORKSPACE}",
                    "location": LOCATION, "job_id": job_id,
                    "result": contract,
                    "provider_policy": {"creator_email": PRINCIPALS["sender"]},
                }
                proof, sha = verify_materialization(metadata, full_expected)
                stage = "NAMED_TABLE_RESULT_READER"
                bound = NamedTableBigQueryResultReader(
                    expected=full_expected, principal=PRINCIPALS["reader"],
                    session=reader_http)
                raw_page = bound.get_query_results_once(
                    project=PROJECT, location=LOCATION, job_id=job_id,
                    max_results=1)
                _, after_sha = verify_materialization(metadata, full_expected)
                if sha != after_sha:
                    raise RuntimeError("RESULT_METADATA_CHANGED_DURING_READ")
                stage = "RESULT_PAGE_VALIDATION"
                page = validate_bigquery_first_page(
                    project=PROJECT, location=LOCATION, job_id=job_id,
                    approved_schema=(("demo_value", "INTEGER"),),
                    requested_rows=1, raw=raw_page)
                if len(page.rows) != 1:
                    raise RuntimeError("EXPECTED_ONE_ROW")
                print("LIVE CLOUD SMOKE PASS: sender, independent attester, reader")
                print("JOB:", job_id)
                print("ROWS:", len(page.rows))
                print("COUNT:", page.rows[0][0])
                print("ATTESTED BILLING BYTES:", report.bytes_billed)
                print("NOTE: named result table persists in dr_results_dev_w18")
                print("LIMITATION: PostgreSQL/API/UI is NOT tested by this script")
        finally:
            metadata.close()
        return 0
    except Exception as exc:
        # No tokens or SQL printed, fail with stable safe reason.
        print("LIVE CLOUD SMOKE BLOCKED:", stage, type(exc).__name__, file=sys.stderr)
        if job_id:
            print("ORIGINAL JOB:", job_id, file=sys.stderr)
            print("Do not resubmit it; use --resume-job-id instead.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
