"""Explicit public HTTP surface. All unreviewed routes fail closed.

This is an outer ASGI boundary, not a replacement for session, CSRF or ownership.
The policy binds a method AND registered path AND actual endpoint callable.
"""
import os
import logging
from ipaddress import ip_address, ip_network
from dataclasses import dataclass
from importlib import import_module
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Match

MODE = 'csv-readonly'
PROCESSING_MODE = 'csv-processing'

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
# Creating a workspace or a dataset is a separate, explicit opt-in.
# No upload, processing, AI or warehouse route is added.
PROCESSING_ROUTES = {
    ('POST', '/files/uploads/{upload_id}/jobs'): 'app.api.processing_jobs.admit_stage',
}
PROFILE_REFRESH_ROUTES = {
    ('POST', D + '/profile/refresh'): 'app.api.dataset_profiles.refresh',
}
ANALYTICS_READ_ROUTES = {
    ('GET', D + '/analytics'): 'app.api.dataset_analytics.read',
}
ANALYTICS_REFRESH_ROUTES = {
    ('POST', D + '/analytics/refresh'): 'app.api.dataset_analytics.refresh',
}
SQL_EXECUTION_ROUTES = {
    ('POST', W + '/sql/validate'): 'app.api.sql_analytics.validate',
    ('POST', W + '/sql/execute'): 'app.api.sql_analytics.execute',
    ('POST', W + '/sql/builder'): 'app.api.sql_analytics.builder',
    ('GET', W + '/sql/executions'): 'app.api.sql_analytics.executions',
    ('GET', W + '/sql/executions/{execution_id}'): 'app.api.sql_analytics.execution',
    ('GET', W + '/sql/queries'): 'app.api.sql_analytics.queries',
    ('POST', W + '/sql/queries'): 'app.api.sql_analytics.save',
    ('PATCH', W + '/sql/queries/{query_id}'): 'app.api.sql_analytics.update',
    ('DELETE', W + '/sql/queries/{query_id}'): 'app.api.sql_analytics.delete',
}
SQL_CATALOG_ROUTES = {
    ('GET', W + '/sql/catalog'): 'app.api.sql_analytics.read_catalog',
}
INITIAL_APPEND_ROUTES = {
    ('POST', D + '/incremental/initial-append-policy/uploads/{upload_id}'): 'app.api.incremental.initial_append_policy',
}
RULE_REVIEW_ROUTES = {
    ('GET', '/files/dataset-version-files/{dataset_version_file_id}/business-rules/suggestions'): 'app.api.files.get_rule_suggestions',
    ('POST', '/files/dataset-version-files/{dataset_version_file_id}/business-rules'): 'app.api.files.submit_business_rules',
    ('POST', '/files/dataset-version-files/{dataset_version_file_id}/business-rules/finalize'): 'app.api.files.finalize_rules',
}
UPLOAD_ROUTES = {
    ('POST', '/files/initiate'): 'app.api.files.initiate_upload',
    ('POST', '/files/complete'): 'app.api.files.complete_upload',
}
WORKSPACE_CREATE = {
    ('POST', '/workspaces'): 'app.api.workspaces.create_workspace_endpoint',
    ('POST', W + '/datasets'): 'app.api.workspaces.create_dataset_endpoint',
}


@dataclass(frozen=True)
class LaunchSettings:
    enabled: bool = False
    hosts: tuple[str, ...] = ()
    trusted_cloudfront_vpc_origin: bool = False
    workspace_creation_enabled: bool = False
    csv_upload_enabled: bool = False
    processing_enabled: bool = False
    rule_review_enabled: bool = False
    initial_append_enabled: bool = False
    analytics_read_enabled: bool = False
    analytics_refresh_enabled: bool = False
    sql_catalog_enabled: bool = False
    profile_refresh_enabled: bool = False
    sql_execution_enabled: bool = False


def launch_settings():
    mode = os.getenv('DATARISE_PUBLIC_LAUNCH_MODE', 'off')
    if mode == 'off': return LaunchSettings()
    if mode not in (MODE, PROCESSING_MODE): raise ValueError('Invalid public launch mode')
    from app.auth.config import require_secret
    settings = require_secret()
    if (os.getenv('APP_ENV') != 'production' or settings.mode != 'session'
        or not settings.secure or os.getenv('ENABLE_DEV_IDENTITY', 'false') != 'false'):
        raise ValueError('Public launch requires production session authentication')
    processing = mode == PROCESSING_MODE
    for flag in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS'):
        expected = 'true' if processing else 'false'
        if os.getenv(flag) != expected:
            raise ValueError('Public processing flags differ from reviewed launch mode')
    if os.getenv('DATARISE_DISPATCH_APPROVED') != 'false':
        raise ValueError('The public API must never have dispatcher authority')
    hosts = tuple(os.getenv('DATARISE_PUBLIC_HOSTS', '').split(','))
    # Exact hostnames only: no wildcard, ports, URLs or proxy-header trust.
    import re
    if not hosts or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', h) or '..' in h for h in hosts):
        raise ValueError('Configure exact public hostnames')
    if urlsplit(settings.backend).hostname not in hosts:
        raise ValueError('Backend origin must be a trusted public host')
    trust = os.getenv('DATARISE_TRUST_CLOUDFRONT_VPC_ORIGIN', 'false')
    if trust not in ('true', 'false'):
        raise ValueError('Invalid trusted CloudFront VPC origin setting')
    creation = os.getenv('DATARISE_PUBLIC_WORKSPACE_CREATION', 'false')
    if creation not in ('true', 'false'):
        raise ValueError('Invalid public workspace creation opt-in')
    upload = os.getenv('DATARISE_PUBLIC_CSV_UPLOAD', 'false')
    if upload not in ('true', 'false'):
        raise ValueError('Invalid public CSV upload opt-in')
    if upload == 'true':
        from app.config import AWS_REGION, S3_BUCKET_NAME
        if (creation != 'true' or AWS_REGION != os.getenv('AWS_REGION')
            or not os.getenv('S3_BUCKET_NAME') or S3_BUCKET_NAME != os.getenv('S3_BUCKET_NAME')):
            raise ValueError('Public upload requires reviewed workspace creation and S3 runtime settings')
    if processing and (creation != 'true' or upload != 'true'):
        raise ValueError('Public processing requires approved CSV uploads and workspace creation')
    rule_review = os.getenv('DATARISE_PUBLIC_RULE_REVIEW', 'false')
    if rule_review not in ('true', 'false'):
        raise ValueError('Invalid rule review opt-in')
    if rule_review == 'true' and not processing:
        raise ValueError('Public rule review requires approved processing mode')
    initial_append = os.getenv('DATARISE_PUBLIC_INITIAL_APPEND', 'false')
    if initial_append not in ('true','false'):
        raise ValueError('Invalid initial append opt-in')
    if initial_append == 'true' and not (processing and rule_review == 'true'):
        raise ValueError('Initial APPEND requires approved processing and rule review')
    analytics_read = os.getenv('DATARISE_PUBLIC_ANALYTICS_READ', 'false')
    analytics_refresh = os.getenv('DATARISE_PUBLIC_ANALYTICS_REFRESH', 'false')
    sql_catalog = os.getenv('DATARISE_PUBLIC_SQL_CATALOG', 'false')
    profile_refresh = os.getenv('DATARISE_PUBLIC_PROFILE_REFRESH', 'false')
    sql_execution = os.getenv('DATARISE_PUBLIC_SQL_EXECUTION', 'false')
    if any(v not in ('true', 'false') for v in (analytics_read, analytics_refresh, sql_catalog, profile_refresh, sql_execution)):
        raise ValueError('Invalid analytics or SQL public opt-in')
    if analytics_refresh == 'true' and analytics_read != 'true':
        raise ValueError('Analytics refresh requires analytics read opt-in')
    if sql_execution == 'true' and sql_catalog != 'true':
        raise ValueError('SQL execution requires approved SQL catalog')
    if profile_refresh == 'true' and analytics_read != 'true':
        raise ValueError('Profile refresh requires analytics read opt-in')
    if sql_catalog == 'true' and analytics_read != 'true':
        raise ValueError('SQL catalog requires analytics read opt-in')
    return LaunchSettings(True, hosts, trust == 'true', creation == 'true', upload == 'true',
                          processing, rule_review == 'true', initial_append == 'true',
                          analytics_read == 'true', analytics_refresh == 'true', sql_catalog == 'true',
                          profile_refresh == 'true', sql_execution == 'true')


def permitted(route, method, settings=None):
    path = getattr(route, 'path', None)
    if (method, path) in PROCESSING_ROUTES:
        if settings is None or not settings.processing_enabled:
            return False
        name = PROCESSING_ROUTES[(method, path)]
    elif (method, path) in PROFILE_REFRESH_ROUTES:
        if settings is None or not settings.profile_refresh_enabled: return False
        name = PROFILE_REFRESH_ROUTES[(method,path)]
    elif (method, path) in ANALYTICS_READ_ROUTES:
        if settings is None or not settings.analytics_read_enabled: return False
        name = ANALYTICS_READ_ROUTES[(method, path)]
    elif (method, path) in ANALYTICS_REFRESH_ROUTES:
        if settings is None or not settings.analytics_refresh_enabled: return False
        name = ANALYTICS_REFRESH_ROUTES[(method, path)]
    elif (method, path) in SQL_EXECUTION_ROUTES:
        if settings is None or not settings.sql_execution_enabled: return False
        name = SQL_EXECUTION_ROUTES[(method,path)]
    elif (method, path) in SQL_CATALOG_ROUTES:
        if settings is None or not settings.sql_catalog_enabled: return False
        name = SQL_CATALOG_ROUTES[(method, path)]
    elif (method, path) in INITIAL_APPEND_ROUTES:
        if settings is None or not settings.initial_append_enabled:
            return False
        name = INITIAL_APPEND_ROUTES[(method,path)]
    elif (method, path) in RULE_REVIEW_ROUTES:
        if settings is None or not settings.processing_enabled or not settings.rule_review_enabled:
            return False
        name = RULE_REVIEW_ROUTES[(method, path)]
    elif (method, path) in UPLOAD_ROUTES:
        if settings is None or not settings.csv_upload_enabled:
            return False
        name = UPLOAD_ROUTES[(method, path)]
    elif (method, path) in WORKSPACE_CREATE:
        if settings is None or not settings.workspace_creation_enabled:
            return False
        name = WORKSPACE_CREATE[(method, path)]
    else:
        name = POLICY.get((method, path))
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
    from app.services.product_preview_service import authorize, authorize_read
    from app.db.database import repository_transaction
    def check():
        user = get_current_user(request)
        try:
            w = int(params['workspace_id'])
            if not 0 < w <= 9007199254740991: raise ValueError()
            if 'dataset_id' in params:
                d = int(params['dataset_id'])
                if not 0 < d <= 9007199254740991: raise ValueError()
                # GET handlers independently enforce ownership; do not require
                # UPDATE privileges merely to lock rows in a read-only guard.
                # Keep the locking guard for any approved non-GET operation.
                if (request.method == 'GET' or
                    (request.method == 'POST' and
                     getattr(request.scope.get('route'), 'path', None)
                     in (D + '/incremental/initial-append-policy/uploads/{upload_id}',
                         D + '/analytics/refresh',
                         D + '/profile/refresh'))):
                    # The dedicated handlers revalidate owner and acquire their
                    # own publication locks; the outer guard needs SELECT only.
                    authorize_read(w, d, user)
                else:
                    authorize(w, d, user)
            else:
                with repository_transaction() as conn:
                    found = conn.execute('''SELECT 1 FROM workspaces w JOIN users u ON u.owner_key=w.owner
                        WHERE w.workspace_id=%s AND u.user_id=%s AND u.owner_key=%s
                        AND u.account_status='ACTIVE' AND w.status='ACTIVE' ''', (w,user['user_id'],user['owner_key'])).fetchone()
                    if not found: raise PermissionError()
        except (PermissionError, ValueError): raise HTTPException(404, 'Scope unavailable') from None
    await run_in_threadpool(check)


def trusted_cloudfront_https(scope, settings):
    """Only recognize CloudFront viewer HTTPS through the ALB-only VPC path.

    This setting MUST only be enabled with: (1) task SG ingress solely from
    private ALB SG; (2) private ALB SG ingress solely from CloudFront's VPC
    origin managed SG; (3) CloudFront HTTPS-only viewer behavior and origin
    policy which adds CloudFront-Forwarded-Proto. A header alone is not trust.
    The ALB HTTP listener must independently report x-forwarded-proto=http.
    """
    if not settings.trusted_cloudfront_vpc_origin or scope.get('scheme') != 'http':
        return False
    if scope.get('type') != 'http':
        return False
    headers = scope.get('headers', ())
    def exactly_one(key):
        matches = [value for name, value in headers if name.lower() == key]
        return matches[0] if len(matches) == 1 else None

    host = exactly_one(b'host')
    viewer_protocol = exactly_one(b'cloudfront-forwarded-proto')
    alb_protocol = exactly_one(b'x-forwarded-proto')
    client = scope.get('client')
    if (host is None or viewer_protocol != b'https' or alb_protocol != b'http'
            or not client or not isinstance(client[0], str)):
        return False
    try:
        if not ip_address(client[0]) in ip_network('172.31.0.0/16'):
            return False
        host_text = host.decode('ascii')
    except (ValueError, UnicodeDecodeError):
        return False
    return host_text in settings.hosts


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
        # A CloudFront-generated HTTPS signal is accepted only under the
        # opt-in VPC/ALB network boundary and matching independent ALB protocol.
        # Ignore generic X-Forwarded-Proto=https; do not enable Uvicorn proxy trust.
        if trusted_cloudfront_https(scope, self.settings):
            scope = {**scope, 'scheme': 'https'}
        request = Request(scope)
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
        if status is None and (matched is None or not permitted(matched, method, self.settings)): status = 403
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
        except Exception as exc:
            # Log only the exception class, never exception text/traceback: auth
            # exceptions can contain SQL parameters or provider credentials.
            # The public response remains generic and unchanged.
            logging.getLogger('datarise.public_launch').error(
                'Public request handler failed: method=%s route=%s exception_class=%s',
                method, getattr(matched, 'path', 'unknown'),
                type(exc).__name__,
            )
            if not started: await error_response(503)(scope, receive, safe_send)
            elif error is not None: return  # Sanitized server-error response already sent.
            else: raise
