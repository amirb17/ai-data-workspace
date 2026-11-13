"""Read-only, fail-closed Batch 1 AWS processing release gate.

Usage:
  python -m tools.processing_batch1_release_gate --region ap-southeast-2
Requires AWS credentials with read-only S3/SQS/Lambda permissions.
Optional DATABASE_URL is used only for a read-only PostgreSQL capability audit.
This program never enables feature flags, starts workers, sends queue messages,
reads user objects, changes database state, or prints secrets.
"""
import argparse
import json
import os
import sys
from urllib.parse import urlparse

REQUIRED_DB = (
    "processing_source_objects",
    "processing_jobs",
    "processing_job_stage_attempts",
    "processing_stage_intents",
    "processing_artifact_candidates",
    "processing_stage_receipts",
    "processing_stage_heads",
    "processing_dispatch_outbox",
    "processing_admission_pins",
)
REQUIRED_PROCS = (
    "task20_admit(bigint,bigint,text,text)",
    "task20_register_source(bigint,bigint,jsonb)",
    "task20_publish(uuid,uuid,uuid)",
    "task20_verify(uuid,uuid,uuid,jsonb)",
    "task20_claim_dispatch()",
)
REQUIRED_ROLES = ("datarise_api", "datarise_worker", "datarise_dispatch", "datarise_verifier")


def evaluate(*, region, bucket, queue, verifier, expected_account=None, db_url=None,
             session=None):
    blockers = []
    evidence = {}

    def fail(code):
        if code not in blockers:
            blockers.append(code)

    if not region or not bucket or not queue or not verifier:
        fail("AWS_RESOURCE_CONFIGURATION_INCOMPLETE")
        return {"ready": False, "blockers": blockers, "evidence": evidence}
    parsed = urlparse(queue)
    if parsed.scheme != "https" or "sqs." not in (parsed.hostname or ""):
        fail("QUEUE_URL_INVALID")
        return {"ready": False, "blockers": blockers, "evidence": evidence}
    if session is None:
        import boto3
        session = boto3.Session(region_name=region)
    if session.region_name != region:
        fail("AWS_REGION_MISMATCH")
        return {"ready": False, "blockers": blockers, "evidence": evidence}

    try:
        caller = session.client("sts").get_caller_identity()
        if expected_account and caller["Account"] != expected_account:
            fail("AWS_ACCOUNT_MISMATCH")
        evidence["account_checked"] = True
    except Exception:
        fail("AWS_IDENTITY_UNVERIFIED")

    try:
        s3 = session.client("s3")
        versioning = s3.get_bucket_versioning(Bucket=bucket)
        evidence["artifact_versioning"] = versioning.get("Status") == "Enabled"
        if not evidence["artifact_versioning"]:
            fail("VERSIONED_ARTIFACT_BUCKET_REQUIRED")
    except Exception:
        fail("ARTIFACT_BUCKET_UNVERIFIED")

    try:
        sqs = session.client("sqs")
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue,
            AttributeNames=["VisibilityTimeout", "RedrivePolicy", "SqsManagedSseEnabled"],
        )["Attributes"]
        redrive = json.loads(attrs.get("RedrivePolicy", "{}"))
        evidence["queue_dlq_configured"] = bool(redrive.get("deadLetterTargetArn"))
        evidence["queue_encrypted"] = attrs.get("SqsManagedSseEnabled") == "true"
        evidence["queue_visibility_seconds"] = int(attrs.get("VisibilityTimeout", 0))
        if not evidence["queue_dlq_configured"]:
            fail("PROCESSING_DLQ_MISSING")
        if not evidence["queue_encrypted"]:
            fail("PROCESSING_QUEUE_SSE_UNVERIFIED")
        if evidence["queue_visibility_seconds"] < 300:
            fail("PROCESSING_VISIBILITY_TOO_SHORT")
    except Exception:
        fail("PROCESSING_QUEUE_UNVERIFIED")

    try:
        lam = session.client("lambda")
        response = lam.get_function_configuration(FunctionName=verifier)
        evidence["verifier_active"] = (
            response.get("State") == "Active"
            and response.get("LastUpdateStatus") == "Successful"
        )
        if not evidence["verifier_active"]:
            fail("VERIFIER_NOT_ACTIVE")
        config = response.get("Environment", {}).get("Variables", {})
        evidence["verifier_approved"] = (
            config.get("DATARISE_PROCESSING_DEPLOYMENT_APPROVED") == "true"
            and config.get("DATARISE_PROCESSING_READERS") == "true"
        )
        if not evidence["verifier_approved"]:
            fail("VERIFIER_RELEASE_DISABLED")
    except Exception:
        fail("VERIFIER_UNVERIFIED")

    secrets = [os.getenv(name) for name in (
        "DATARISE_WORKER_DB_SECRET_ARN",
        "DATARISE_DISPATCH_DB_SECRET_ARN",
        "DATARISE_VERIFIER_DB_SECRET_ARN",
    )]
    evidence["separate_db_secrets_declared"] = (
        all(secrets) and len(set(secrets)) == len(secrets)
    )
    if not evidence["separate_db_secrets_declared"]:
        fail("INDEPENDENT_RUNTIME_SECRETS_UNVERIFIED")

    if not db_url:
        fail("POSTGRESQL_RELEASE_AUDIT_NOT_RUN")
    else:
        try:
            import psycopg
            with psycopg.connect(db_url, connect_timeout=5, options="-c default_transaction_read_only=on") as conn:
                with conn.cursor() as cur:
                    for table in REQUIRED_DB:
                        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
                        if not cur.fetchone()[0]:
                            fail("DB_TABLE_MISSING_" + table.upper())
                    for proc in REQUIRED_PROCS:
                        cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f"public.{proc}",))
                        if not cur.fetchone()[0]:
                            fail("DB_FUNCTION_MISSING_" + proc.split("(")[0].upper())
                    cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(REQUIRED_ROLES),))
                    roles = {row[0] for row in cur.fetchall()}
                    for role in REQUIRED_ROLES:
                        if role not in roles:
                            fail("DB_RUNTIME_ROLE_MISSING_" + role.upper())
            evidence["postgresql_catalog_checked"] = True
        except Exception:
            fail("POSTGRESQL_RELEASE_AUDIT_FAILED")

    # Existence and syntax are necessary, not sufficient: cloud identities,
    # least-privilege negatives, live dispatcher/worker health and physical
    # publication must be accepted separately. Never infer GO from these probes.
    fail("INDEPENDENT_DISPATCHER_WORKER_IAM_ACCEPTANCE_REQUIRED")
    fail("LIVE_BRONZE_SILVER_GOLD_E2E_ACCEPTANCE_REQUIRED")
    return {"ready": False, "blockers": blockers, "evidence": evidence}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only processing release gate")
    parser.add_argument("--region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))
    parser.add_argument("--bucket", default=os.getenv("DATARISE_ARTIFACT_BUCKET"))
    parser.add_argument("--queue", default=os.getenv("DATARISE_PROCESSING_QUEUE_URL"))
    parser.add_argument("--verifier", default=os.getenv("DATARISE_VERIFIER_FUNCTION_NAME"))
    parser.add_argument("--account", default=os.getenv("DATARISE_EXPECTED_AWS_ACCOUNT"))
    args = parser.parse_args(argv)
    result = evaluate(region=args.region, bucket=args.bucket, queue=args.queue,
                      verifier=args.verifier, expected_account=args.account,
                      db_url=os.getenv("DATABASE_URL"))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
