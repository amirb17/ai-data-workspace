from fastapi import APIRouter, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.sql_analytics import SqlRequest, SaveQuery, DeleteQuery, BuilderRequest
from app.processing.sql_builder import build
from app.processing.sql_validation import SqlBlocked
from app.services import sql_catalog_service as catalog, sql_analytics_service as service
from app.db import sql_repository as repository

router=APIRouter(prefix='/workspaces/{workspace_id}/sql',tags=['SQL Analytics'])

@router.post('/builder')
def builder(workspace_id:int,request:BuilderRequest,user:dict=Depends(get_current_user)):
    def operation():
        current=catalog.snapshot(workspace_id,user)
        try: return dict(workspace_id=workspace_id,status='VALID',sql_text=build(request,current),diagnostics=[])
        except SqlBlocked as exc: return dict(workspace_id=workspace_id,status='INVALID',diagnostics=[exc.diagnostic()])
    return public_call(operation)

@router.get('/catalog')
def read_catalog(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:catalog.snapshot(workspace_id,user))

@router.post('/validate')
def validate(workspace_id:int,request:SqlRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:service.validate_query(workspace_id,user,request))

@router.post('/execute')
def execute(workspace_id:int,request:SqlRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:service.execute_query(workspace_id,user,request))

@router.get('/queries')
def queries(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:repository.queries(workspace_id,user))

@router.post('/queries')
def save(workspace_id:int,request:SaveQuery,user:dict=Depends(get_current_user)):
    return public_call(lambda:repository.save(workspace_id,user,request))

@router.patch('/queries/{query_id}')
def update(workspace_id:int,query_id:int,request:SaveQuery,user:dict=Depends(get_current_user)):
    return public_call(lambda:repository.save(workspace_id,user,request,query_id))

@router.delete('/queries/{query_id}')
def delete(workspace_id:int,query_id:int,request:DeleteQuery,user:dict=Depends(get_current_user)):
    return public_call(lambda:repository.delete(workspace_id,user,query_id,request))

@router.get('/executions')
def executions(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:service.history(workspace_id,user))

@router.get('/executions/{execution_id}')
def execution(workspace_id:int,execution_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:service.history(workspace_id,user,execution_id))
