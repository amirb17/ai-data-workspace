"""Explicit asynchronous admission; existing authentication/CSRF middleware applies."""
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import psycopg

from app.api.identity import get_current_user
from app.processing_worker.dispatch import admit

router = APIRouter(prefix='/files', tags=['processing-jobs'])


class StageJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['BRONZE','SILVER','GOLD']
    idempotency_key: str = Field(min_length=1,max_length=128,strict=True)


@router.post('/uploads/{upload_id}/jobs', status_code=202)
def admit_stage(upload_id: int, request: StageJobRequest, user=Depends(get_current_user)):
    if os.getenv('DATARISE_PROCESSING_DEPLOYMENT_APPROVED') != 'true':
        raise HTTPException(503, 'Asynchronous processing is disabled')
    try:
        job = admit(user['user_id'], upload_id, request.operation, request.idempotency_key)
        return {'job_id':str(job)}
    except psycopg.errors.InsufficientPrivilege as exc:
        raise HTTPException(403, 'Upload access denied') from exc
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(409, 'Idempotency key belongs to another request') from exc
    except Exception as exc:
        raise HTTPException(503, 'Stage admission unavailable') from exc


def reject_managed_synchronous(upload_id, user):
    if os.getenv('DATARISE_PROCESSING_READERS') != 'true':
        return
    from app.db.database import get_connection
    with get_connection() as conn:
        if conn.execute('SELECT task20_managed(%s,%s)', (user['user_id'],upload_id)).fetchone()[0]:
            raise HTTPException(409, 'This source uses durable processing; submit a stage job')
