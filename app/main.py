import os
import logging
import secrets
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.identity import get_current_user
from app.db.database import get_connection
from app.api.files import router as files_router
from app.api.analytics import router as analytics_router

from app.api.workspaces import router as workspaces_router
from app.api.incremental import router as incremental_router
from app.api.dataset_analytics import router as dataset_analytics_router
from app.api.dataset_profiles import router as dataset_profiles_router
from app.api.dataset_understanding import router as dataset_understanding_router
from app.api.workspace_understanding import router as workspace_understanding_router
from app.api.relationships import router as relationships_router
from app.api.workspace_model import router as workspace_model_router
from app.api.metrics import router as metrics_router
from app.api.workspace_analytics import router as workspace_analytics_router
from app.api.workspace_questions import router as workspace_questions_router
from app.api.sql_analytics import router as sql_analytics_router
from starlette.middleware.sessions import SessionMiddleware
from app.api.auth import router as auth_router, auth_error
from app.auth.google import router as google_router
from app.auth.service import AuthError, profile
from app.auth.config import config
from app.auth.middleware import AuthBoundary, RedactAuthQuery
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
app = FastAPI(
    title="AI Data Workspace",
    version="0.1.0"
)
settings = config()
app.add_middleware(SessionMiddleware, secret_key=settings.secret or secrets.token_urlsafe(32),
    session_cookie='__Host-datarise_oidc' if settings.secure else 'datarise_oidc',
    max_age=600, same_site='lax', https_only=settings.secure)
app.add_middleware(AuthBoundary)
origins = [settings.frontend]
if settings.mode == 'development':
    origins = list(dict.fromkeys(origins + ['http://localhost:5173', 'http://127.0.0.1:5173']))
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-CSRF-Token"])
logging.getLogger('uvicorn.access').addFilter(RedactAuthQuery())
for sensitive_logger in ('authlib', 'httpx', 'httpcore'):
    logging.getLogger(sensitive_logger).setLevel(logging.WARNING)
app.add_exception_handler(AuthError, auth_error)
app.include_router(auth_router)
app.include_router(google_router)

@app.exception_handler(RequestValidationError)
async def safe_validation(request, exc):
    if request.url.path.startswith('/auth/'):
        return JSONResponse({'detail': 'Check the fields and password confirmation. Passwords must contain 12–128 characters.'}, status_code=422)
    return await request_validation_exception_handler(request, exc)

@app.get("/me")
def current_me(user: dict = Depends(get_current_user)):
    mode = config().mode
    return {**(profile(user['user_id']) if mode == 'session' else user), "identity_mode": mode}

app.include_router(files_router)
app.include_router(workspaces_router)
app.include_router(workspace_model_router)
app.include_router(incremental_router)
app.include_router(analytics_router)
app.include_router(dataset_analytics_router)
app.include_router(dataset_profiles_router)
app.include_router(dataset_understanding_router)
app.include_router(workspace_understanding_router)
app.include_router(relationships_router)
app.include_router(metrics_router)
app.include_router(workspace_analytics_router)
app.include_router(workspace_questions_router)
app.include_router(sql_analytics_router)

@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


@app.get("/health/db")
def database_health_check():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()

    return {
        "database": "connected",
        "result": result[0]
    }
