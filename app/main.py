from fastapi import FastAPI
from app.db.database import get_connection
from app.api.files import router as files_router
app = FastAPI(
    title="AI Data Workspace",
    version="0.1.0"
)
app.include_router(files_router)

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