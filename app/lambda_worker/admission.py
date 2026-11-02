"""Lightweight authenticated Lambda admission; no transformation or dispatch SDK."""
import json
from app.processing_worker.dispatch import admit


def admit_event(event, *, authenticate, enabled=False):
    if not enabled:
        raise RuntimeError('Job admission requires deployment approval')
    principal = authenticate(event)  # Trusted session/authorizer adapter, never body claims.
    if not principal or type(principal.get('user_id')) is not int:
        raise PermissionError('Authenticated principal required')
    body = json.loads(event['body'])
    if not isinstance(body, dict) or set(body) != {'upload_id','operation','idempotency_key'}:
        raise ValueError('Invalid admission request')
    if type(body['upload_id']) is not int or body['upload_id'] <= 0:
        raise ValueError('Invalid upload identity')
    if (body['operation'] not in ('BRONZE','SILVER','GOLD')
        or not isinstance(body['idempotency_key'], str)
        or not 1 <= len(body['idempotency_key'].strip()) <= 128
        or len(body['idempotency_key']) > 128):
        raise ValueError('Invalid stage admission')
    job_id = admit(principal['user_id'], body['upload_id'], body['operation'], body['idempotency_key'])
    return {'job_id': str(job_id)}
