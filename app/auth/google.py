"""Google OIDC via Authlib. Provider tokens never become application sessions."""
import os
import secrets
import uuid
from urllib.parse import urlsplit
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from app.auth import repository as repo, service
from app.auth.config import require_secret, safe_return
from app.auth.security import digest, normalize_email
from app.api.auth import ready

router = APIRouter(prefix='/auth/google', tags=['authentication'])


def provider():
    require_secret()
    if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):
        raise service.AuthError('Google sign-in is not configured.', 503)
    oauth = OAuth()
    return oauth.register('google', client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile', 'code_challenge_method': 'S256', 'timeout': 10})


def resolve_identity(claims, link_user_id=None):
    if claims.get('email_verified') is not True or not claims.get('sub') or len(claims['sub']) > 255:
        raise service.AuthError('Google could not verify this email.')
    email = normalize_email(claims['email'])
    with repo.transaction() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('google:'+email,))
        identity = cursor.execute("SELECT user_id FROM auth_identities WHERE provider='google' AND subject=%s", (claims['sub'],)).fetchone()
        row = repo.user(cursor, user_id=identity['user_id']) if identity else repo.user(cursor, normalized_email=email)
        if link_user_id and (not row or row['user_id'] != link_user_id or row['normalized_email'] != email):
            raise service.AuthError('Sign in with the Google account matching your verified email.')
        if row and (row['account_status'] != 'ACTIVE' or not row['email_verified']):
            raise service.AuthError('Verify your email or recover this account before connecting Google.')
        if not identity:
            # Google is authoritative for Gmail/hosted accounts. Other domains require
            # the existing account's authenticated session before linking.
            authoritative = email.endswith('@gmail.com') or bool(claims.get('hd'))
            if row and not authoritative and not link_user_id:
                raise service.AuthError('Sign in first, then connect Google in Settings.')
            if not row:
                picture = claims.get('picture', '')
                host = urlsplit(picture).hostname or ''
                avatar = picture if urlsplit(picture).scheme == 'https' and (host.endswith('.googleusercontent.com') or host.endswith('.ggpht.com')) else None
                row = cursor.execute('''INSERT INTO users(owner_key,display_name,email,normalized_email,email_verified,avatar_url)
                  VALUES(%s,%s,%s,%s,TRUE,%s) RETURNING *''',
                  ('usr:'+str(uuid.uuid4()), str(claims.get('name') or email.split('@')[0])[:100], email, email, avatar)).fetchone()
                repo.event(cursor, 'SIGNUP', row['user_id'])
            cursor.execute("INSERT INTO auth_identities(user_id,provider,subject) VALUES(%s,'google',%s)", (row['user_id'], claims['sub']))
            repo.event(cursor, 'GOOGLE_LINKED', row['user_id'])
        return service.issue_session(cursor, row['user_id'])


@router.get('/start')
async def start(request: Request, return_to: str = '/app', connect: bool = False):
    settings = ready()
    if settings.mode != 'session':
        raise service.AuthError('Google sign-in is unavailable in development identity mode.', 503)
    google = provider()
    await run_in_threadpool(service.rate, 'google-ip', request.client.host if request.client else 'unknown', 20, 900)
    current = await run_in_threadpool(service.session, request.cookies.get(settings.session_cookie))
    if connect and not current:
        return RedirectResponse(settings.frontend+'/login', status_code=303)
    state = secrets.token_urlsafe(32)
    with repo.transaction() as cursor:
        cursor.execute("INSERT INTO auth_oauth_states(state_digest,return_to,link_user_id,expires_at) VALUES(%s,%s,%s,NOW()+INTERVAL '10 minutes')",
                       (digest(state), safe_return(return_to), current['user_id'] if current else None))
    request.session.clear()
    try:
        return await google.authorize_redirect(request, settings.backend+'/auth/google/callback', state=state, prompt='select_account')
    except Exception:
        request.session.clear()
        return RedirectResponse(settings.frontend+'/login?error=google',status_code=303)


@router.get('/callback')
async def callback(request: Request):
    settings = ready()
    try:
        state = request.query_params.get('state', '')
        if not state or len(state) > 128:
            raise service.AuthError('Invalid sign-in request.')
        # One-use database state supplements the signed browser state/nonce/PKCE
        # validated by Authlib. No caller-supplied profile is accepted.
        with repo.transaction() as cursor:
            pending = cursor.execute('''UPDATE auth_oauth_states SET consumed_at=NOW()
              WHERE state_digest=%s AND consumed_at IS NULL AND expires_at>NOW() RETURNING *''', (digest(state),)).fetchone()
        if not pending:
            raise service.AuthError('Invalid sign-in request.')
        token = await provider().authorize_access_token(request)
        claims = token.get('userinfo')
        if not claims:
            raise service.AuthError('Google identity is unavailable.')
        if pending['link_user_id']:
            current = await run_in_threadpool(service.session, request.cookies.get(settings.session_cookie))
            if not current or current['user_id'] != pending['link_user_id']:
                raise service.AuthError('Sign in again before connecting Google.')
        session_token = await run_in_threadpool(resolve_identity, claims, pending['link_user_id'])
        from app.api.auth import set_session
        response = RedirectResponse(settings.frontend+safe_return(pending['return_to']), status_code=303)
        set_session(response, session_token)
        return response
    except Exception:
        # Provider exception text can contain codes/tokens. Never return or log it.
        return RedirectResponse(settings.frontend+'/login?error=google', status_code=303)
    finally:
        request.session.clear()
