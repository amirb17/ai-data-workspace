import os
from dataclasses import dataclass
from urllib.parse import urlsplit, unquote


def safe_return(value: str | None) -> str:
    value = value or '/app'
    decoded = value
    for _ in range(3):
        decoded = unquote(decoded)
    if (len(value) > 2048 or any(c in decoded for c in ('\\', '\r', '\n', '\x00'))
            or decoded.startswith('//') or urlsplit(decoded).netloc
            or any(part in ('.', '..') for part in urlsplit(decoded).path.split('/'))
            or not (urlsplit(decoded).path == '/app' or urlsplit(decoded).path.startswith('/app/'))):
        return '/app'
    return value


@dataclass(frozen=True)
class AuthConfig:
    secret: str
    frontend: str
    backend: str
    secure: bool
    mode: str

    @property
    def session_cookie(self):
        return '__Host-datarise_session' if self.secure else 'datarise_session'

    @property
    def csrf_cookie(self):
        return '__Host-datarise_csrf' if self.secure else 'datarise_csrf'


def config() -> AuthConfig:
    development = os.getenv('APP_ENV') == 'development'
    # Preserve an explicitly enabled legacy local identity until deployment opts in.
    default = 'development' if development and os.getenv('ENABLE_DEV_IDENTITY', '').lower() == 'true' else 'session'
    mode = os.getenv('AUTH_MODE', default)
    if mode not in ('development', 'session') or (mode == 'development' and not development):
        raise ValueError('Invalid authentication mode')
    secure = os.getenv('AUTH_COOKIE_SECURE', 'false' if development else 'true').lower() == 'true'
    if not secure and not development:
        raise ValueError('Secure authentication cookies are required')
    frontend = os.getenv('AUTH_FRONTEND_URL', 'http://localhost:5173' if development else '').rstrip('/')
    backend = os.getenv('AUTH_BACKEND_URL', 'http://localhost:8000' if development else '').rstrip('/')
    for origin in (frontend, backend):
        parts = urlsplit(origin)
        if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password or parts.path or parts.query or parts.fragment:
            raise ValueError('Configure authentication origins')
        if parts.scheme != 'https' and (not development or parts.hostname not in ('localhost', '127.0.0.1')):
            raise ValueError('HTTPS is required')
    return AuthConfig(os.getenv('AUTH_SECRET', ''), frontend, backend, secure, mode)


def require_secret() -> AuthConfig:
    settings = config()
    if len(settings.secret.encode()) < 32:
        raise ValueError('Authentication provider configuration is incomplete')
    return settings
