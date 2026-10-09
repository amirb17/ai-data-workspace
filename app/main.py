import os
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
app = FastAPI(
    title="AI Data Workspace",
    version="0.1.0"
)
if os.getenv("APP_ENV") == "development":
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Content-Type"])

@app.get("/me")
def development_me(user: dict = Depends(get_current_user)):
    return {**user, "identity_mode": "development"}

app.include_router(files_router)
app.include_router(workspaces_router)
app.include_router(incremental_router)
app.include_router(analytics_router)
app.include_router(dataset_analytics_router)
app.include_router(dataset_profiles_router)
app.include_router(dataset_understanding_router)
app.include_router(workspace_understanding_router)
app.include_router(relationships_router)

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
