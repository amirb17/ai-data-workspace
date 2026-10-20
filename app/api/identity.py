import os
from fastapi import HTTPException, Request
from app.db.user_repository import get_or_create_dev_user
from app.auth.config import config, require_secret


def get_current_user(request: Request = None):
    """Authoritative principal; legacy identity requires explicit local opt-in."""
    try:
        settings = config()
        if settings.mode == 'session':
            require_secret()
            from app.auth.service import session
            user = session(request.cookies.get(settings.session_cookie) if request else None)
            if not user:
                raise HTTPException(status_code=401, detail='Sign in to continue.')
            return user
    except ValueError as exc:
        raise HTTPException(status_code=503, detail='Authentication configuration is incomplete.') from exc
    if os.getenv("APP_ENV") != "development" or os.getenv("ENABLE_DEV_IDENTITY", "false").lower() != "true":
        raise HTTPException(status_code=503, detail="Development identity is disabled; authentication is not configured")
    key = os.getenv("DEV_USER_KEY", "").strip()
    if not key or len(key) > 240:
        raise HTTPException(status_code=503, detail="Configure a stable DEV_USER_KEY for development")
    try:
        return get_or_create_dev_user("dev:" + key, os.getenv("DEV_USER_NAME", "Development user"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Development identity database is unavailable; verify migrations and configuration") from exc
