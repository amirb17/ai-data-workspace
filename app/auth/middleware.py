import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.auth.config import config
from app.auth.security import valid_csrf


class RedactAuthQuery(logging.Filter):
    """Uvicorn's access logger otherwise records the provider callback code."""
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if str(args[2]).startswith('/auth/'):
                args[2] = str(args[2]).split('?', 1)[0]
                record.args = tuple(args)
        return True


class AuthBoundary(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings = config()
        if settings.mode == 'session' and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if (request.headers.get('origin') != settings.frontend
                    or not valid_csrf(request.headers.get('x-csrf-token'), request.cookies.get(settings.csrf_cookie))):
                return JSONResponse({'detail': 'Refresh the page and try again.'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store, private'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
