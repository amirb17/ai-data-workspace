import logging

from app.lambda_worker import service
from app.processing_worker.messages import parse_job_message

logger = logging.getLogger(__name__)


def execute_job(job_id: str) -> None:
    service.execute_job(job_id)


def lambda_handler(event, context):
    records = event.get("Records") if isinstance(event, dict) else None

    if not isinstance(records, list):
        raise ValueError("Records must be a list")

    failures = []

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Expected an SQS record with a messageId")
        message_id = record.get("messageId")

        if (
            record.get("eventSource") != "aws:sqs"
            or not isinstance(message_id, str)
            or not message_id
        ):
            raise ValueError("Expected an SQS record with a messageId")

        try:
            job_id = parse_job_message(record["body"])

            execute_job(job_id)

        except Exception:
            # Raw executor/driver exceptions can contain credentials or paths.
            logger.warning("SQS job was not acknowledged")
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
