"""Explicit read-only public HTTP surface. All unreviewed routes fail closed.

This is an outer ASGI boundary, not a replacement for session, CSRF or ownership.
The policy binds a method AND registered path AND actual endpoint callable.
"""
import os
from dataclasses import dataclass
from importlib import import_module
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Match

MODE = 'csv-readonly'

# Deliberately no prefix grants, automatic GET grants, or processing exceptions.
AUTH = {
    ('GET', '/auth/config'): 'app.api.auth.configuration',
    ('GET', '/auth/csrf'): 'app.api.auth.csrf',
    ('POST', '/auth/login'): 'app.api.auth.login',
    ('POST', '/auth/signup'): 'app.api.auth.signup',
    ('POST', '/auth/forgot-password'): 'app.api.auth.forgot',
    ('POST', '/auth/resend'): 'app.api.auth.resend',
    ('POST', '/auth/verify'): 'app.api.auth.verify',
    ('POST', '/auth/reset-password'): 'app.api.auth.reset',
    ('POST', '/auth/logout'): 'app.api.auth.logout',
    ('PATCH', '/auth/profile'): 'app.api.auth.profile',
    ('POST', '/auth/change-password'): 'app.api.auth.change',
    ('GET', '/auth/google/start'): 'app.auth.google.start',
    ('GET', '/auth/google/callback'): 'app.auth.google.callback',
    ('GET', '/me'): 'app.main.current_me',
}
W = '/workspaces/{workspace_id}'
D = W + '/datasets/{dataset_id}'
P = D + '/product'
POLICY = {**AUTH,
    ('GET', '/workspaces'): 'app.api.workspaces.list_workspaces_endpoint',
    ('GET', W): 'app.api.workspaces.get_workspace_endpoint',
    ('GET', W + '/datasets'): 'app.api.workspaces.list_datasets_endpoint',
    ('GET', D): 'app.api.workspaces.get_dataset_endpoint',
    ('GET', D + '/processing'): 'app.api.workspaces.dataset_processing_endpoint',
    ('GET', D + '/profile'): 'app.api.dataset_profiles.read',
    ('GET', D + '/profile/readiness'): 'app.api.dataset_profiles.status',
    ('GET', D + '/analytics/readiness'): 'app.api.dataset_analytics.status',
    ('GET', '/files/uploads/{upload_id}/processing-context'): 'app.api.files.processing_context',
    ('GET', P + '/capabilities'): 'app.api.product_preview.capabilities',
    ('GET', P + '/jobs'): 'app.api.product_preview.jobs',
    ('POST', P + '/uploads/{upload_id}/csv-preview'): 'app.api.product_preview.preview',
    ('GET', P + '/uploads/{upload_id}/gold-assessment'): 'app.api.product_preview.gold',
}


@dataclass(frozen=True)
class LaunchSettings:
    enabled: bool = False
    hosts: tuple[str, ...] = ()


def launch_settings():
    mode = os.getenv('DATARISE_PUBLIC_LAUNCH_MODE', 'off')
    if mode == 'off': return LaunchSettings()
    if mode != MODE: raise ValueError('Invalid public launch mode')
    from app.auth.config import require_secret
    settings = require_secret()
    if (os.getenv('APP_ENV') != 'production' or settings.mode != 'session'
        or not settings.secure or os.getenv('ENABLE_DEV_IDENTITY', 'false') != 'false'):
        raise ValueError('Public launch requires production session authentication')
    for flag in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS', 'DATARISE_DISPATCH_APPROVED'):
        if os.getenv(flag) != 'false': raise ValueError('Public launch requires disabled processing flags')
    hosts = tuple(os.getenv('DATARISE_PUBLIC_HOSTS', '').split(','))
    # Exact hostnames only: no wildcard, ports, URLs or proxy-header trust.
    import re
    if not hosts or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', h) or '..' in h for h in hosts):
        raise ValueError('Configure exact public hostnames')
    if urlsplit(settings.backend).hostname not in hosts:
        raise ValueError('Backend origin must be a trusted public host')
    return LaunchSettings(True, hosts)


def permitted(route, method):
    name = POLICY.get((method, getattr(route,'path',None)))
    if name is None: return False
    module, attr = name.rsplit('.', 1)
    return route.endpoint is getattr(import_module(module), attr)


def http_routes(routes):
    """Resolve installed FastAPI's lazy includes with their effective prefixes.

    Older eager APIRoutes are already leaves. Do not reconstruct prefixes or use
    OpenAPI (which omits hidden routes). Unknown route kinds receive no grant.
    """
    for route in routes:
        included = getattr(route, 'effective_candidates', None)
        if callable(included): yield from http_routes(included())
        else: yield route


async def launch_authority(request: Request):
    """Extra current-owner validation on public scoped reads, before handlers.

    Original handler dependencies still run. This is intentionally not a new
    identity provider or a client-selected permission.
    """
    if not request.scope.get('datarise.public_launch'): return
    params = request.path_params
    if 'workspace_id' not in params: return
    from app.api.identity import get_current_user
    from app.services.product_preview_service import authorize
    from app.db.database import repository_transaction
    def check():
        user = get_current_user(request)
        try:
            w = int(params['workspace_id'])
            if not 0 < w <= 9007199254740991: raise ValueError()
            if 'dataset_id' in params:
                d = int(params['dataset_id'])
                if not 0 < d <= 9007199254740991: raise ValueError()
                authorize(w, d, user)
            else:
                with repository_transaction() as conn:
                    found = conn.execute('''SELECT 1 FROM workspaces w JOIN users u ON u.owner_key=w.owner
                        WHERE w.workspace_id=%s AND u.user_id=%s AND u.owner_key=%s
                        AND u.account_status='ACTIVE' AND w.status='ACTIVE' ''', (w,user['user_id'],user['owner_key'])).fetchone()
                    if not found: raise PermissionError()
        except (PermissionError, ValueError): raise HTTPException(404, 'Scope unavailable') from None
    await run_in_threadpool(check)


def error_response(status):
    messages = {401:'Sign in to continue.', 403:'This action is unavailable in the public read-only release.',
                404:'Resource unavailable.', 413:'Request exceeds its byte limit.', 422:'Invalid request fields.',
                429:'Too many requests; retry later.'}
    return JSONResponse({'detail':messages.get(status,'Request could not be completed safely; retry later.')}, status_code=status)


class PublicLaunchBoundary:
    def __init__(self, app, *, settings, routes):
        self.app, self.settings, self.routes = app, settings, routes

    async def __call__(self, scope, receive, send):
        if not self.settings.enabled or scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket': return await send({'type':'websocket.close','code':1008})
        # ECS/ALB health probing is HTTP inside the isolated VPC. Permit only
        # the registered GET /health endpoint; no prefix grants, no DB access,
        # and no trust in a caller-controlled forwarded scheme or hostname.
        # In production, the task SG must restrict port 8000 to the ALB SG.
        if (scope.get('method') == 'GET' and scope.get('path') == '/health'
            and len([1 for k, _ in scope.get('headers', []) if k.lower() == b'host']) == 1
            and not scope.get('query_string')):
            matched_health = next(
                (route for route in http_routes(self.routes)
                 if route.matches(scope)[0] == Match.FULL), None
            )
            if (matched_health is not None
                and getattr(matched_health, 'path', None) == '/health'
                and getattr(matched_health, 'endpoint', None)
                    is getattr(import_module('app.main'), 'health_check')):
                return await self.app(scope, receive, send)
        request = Request(scope)
        # TLS scheme must come from the server/trusted proxy configuration, never
        # from a header interpreted here. Untrusted forwarding headers are ignored.
        try: host = request.url.hostname
        except ValueError: host = None
        status = None
        if (host not in self.settings.hosts or scope.get('scheme') != 'https'
            or sum(k.lower()==b'host' for k,_ in scope.get('headers',[])) != 1): status = 400
        path = scope['path']
        if any(c in path for c in ('\\', '%', '//')) or any(p in ('.','..') for p in path.split('/')): status = 404
        method = scope['method']
        if method == 'OPTIONS': method = request.headers.get('access-control-request-method', '')
        probe = {**scope, 'method':method}
        matched = next((r for r in http_routes(self.routes) if r.matches(probe)[0] == Match.FULL), None)
        if status is None and (matched is None or not permitted(matched, method)): status = 403
        if status is None and scope['method'] == 'OPTIONS':
            from app.auth.config import config
            if request.headers.get('origin') != config().frontend: status = 403
        limit = 2800000 if matched and matched.path.endswith('/csv-preview') else 65536
        length = request.headers.get('content-length')
        if status is None and length is not None:
            try:
                if int(length) < 0 or int(length) > limit: status = 413
            except ValueError: status = 400
        error = None
        started = False
        too_large = False
        async def safe_send(message):
            nonlocal error, started
            if message['type'] == 'http.response.start':
                started = True
                if too_large: message = {**message,'status':413}
                if message['status'] >= 400:
                    error = error_response(message['status']).body
                    message = {**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower() not in (b'content-length', b'content-type')] + [(b'content-type',b'application/json')]}
                headers = [(b'cache-control',b'no-store, private'),(b'x-content-type-options',b'nosniff'),
                           (b'referrer-policy',b'no-referrer'),(b'x-frame-options',b'DENY'),
                           (b'content-security-policy',b"default-src 'none'; frame-ancestors 'none'"),
                           (b'permissions-policy',b'camera=(), microphone=(), geolocation=()')]
                if scope.get('scheme') == 'https': headers.append((b'strict-transport-security',b'max-age=31536000'))
                names = {k for k,_ in headers}
                message = {**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower() not in names]+headers}
            elif message['type'] == 'http.response.body' and error is not None:
                message = {**message,'body':b'' if message.get('more_body') else error}
            await send(message)
        if status is not None: return await error_response(status)(scope, receive, safe_send)
        scope['datarise.public_launch'] = True
        total = 0
        async def bounded_receive():
            nonlocal total, too_large
            message = await receive()
            if message['type'] == 'http.request':
                total += len(message.get('body',b''))
                if total > limit:
                    too_large = True
                    raise HTTPException(413, 'Request exceeds its byte limit')
            return message
        try:
            await self.app(scope, bounded_receive, safe_send)
        except Exception:
            if not started: await error_response(503)(scope, receive, safe_send)
            elif error is not None: return  # Sanitized server-error response already sent.
            else: raise
