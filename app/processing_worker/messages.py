"""The shared queue payload contract contains no client-supplied execution scope."""
import json
from uuid import UUID


def parse_job_message(body: str) -> str:
    if not isinstance(body, str):
        raise ValueError('Job body must be a JSON string')
    payload = json.loads(body)
    if not isinstance(payload, dict) or set(payload) != {'job_id'}:
        raise ValueError('Job body must contain only job_id')
    job_id = payload['job_id']
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError('A valid job UUID is required')
    return str(UUID(job_id))
