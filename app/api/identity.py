import os
from fastapi import HTTPException
from app.db.user_repository import get_or_create_dev_user


def get_current_user():
    """Single configured development principal, not authentication."""
    if os.getenv("APP_ENV") != "development" or os.getenv("ENABLE_DEV_IDENTITY", "false").lower() != "true":
        raise HTTPException(status_code=503, detail="Development identity is disabled; authentication is not configured")
    key = os.getenv("DEV_USER_KEY", "").strip()
    if not key or len(key) > 240:
        raise HTTPException(status_code=503, detail="Configure a stable DEV_USER_KEY for development")
    try:
        return get_or_create_dev_user("dev:" + key, os.getenv("DEV_USER_NAME", "Development user"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Development identity database is unavailable; verify migrations and configuration") from exc
