import os
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse
from app.api.identity import get_current_user
from app.auth import service, schemas
from app.auth.config import require_secret, config
from app.auth.security import new_csrf, valid_csrf

router = APIRouter(prefix='/auth', tags=['authentication'])


def ready():
    try:
        settings = require_secret()
    except ValueError:
        raise service.AuthError('Sign-in configuration is incomplete.', 503) from None
    if settings.mode != 'session':
        raise service.AuthError('Sign-in is unavailable in development identity mode.', 503)
    return settings


def ip(request):
    # Do not trust arbitrary client forwarding headers.
    return request.client.host if request.client else 'unknown'


def set_session(response, token):
    settings = ready()
    response.set_cookie(settings.session_cookie, token, max_age=43200,
                        httponly=True, secure=settings.secure, samesite='lax', path='/')


@router.get('/config')
def configuration():
    settings = config()
    return {'google_available': bool(settings.mode == 'session' and len(settings.secret.encode()) >= 32
                                    and os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET')),
            'identity_mode': settings.mode}


@router.get('/csrf')
def csrf(request: Request, response: Response):
    settings = ready()
    existing = request.cookies.get(settings.csrf_cookie)
    token = existing if valid_csrf(existing, existing) else new_csrf()
    response.set_cookie(settings.csrf_cookie, token, max_age=86400, httponly=True,
                        secure=settings.secure, samesite='lax', path='/')
    return {'csrf_token': token}


@router.post('/signup')
def signup(payload: schemas.Signup, request: Request, background: BackgroundTasks):
    ready()
    return service.request_code(payload.email, 'VERIFY_EMAIL', ip(request), signup=payload.model_dump(),
                                delivery=lambda *args: background.add_task(service.deliver_code, *args))


@router.post('/login')
def login(payload: schemas.Login, request: Request, response: Response):
    ready()
    token, user_id = service.login(payload.email, payload.password, ip(request))
    set_session(response, token)
    return {'status': 'SIGNED_IN'}


@router.post('/forgot-password')
def forgot(payload: schemas.Email, request: Request, background: BackgroundTasks):
    ready()
    return service.request_code(payload.email, 'RESET_PASSWORD', ip(request),
                                delivery=lambda *args: background.add_task(service.deliver_code, *args))


@router.post('/resend')
def resend(payload: schemas.Challenge, request: Request, background: BackgroundTasks):
    ready()
    return service.resend(payload.challenge_id, ip(request),
                          delivery=lambda *args: background.add_task(service.deliver_code, *args))


@router.post('/verify')
def verify(payload: schemas.Verify, request: Request):
    ready()
    return service.verify_code(payload.challenge_id, payload.code, ip(request), payload.purpose)


@router.post('/reset-password')
def reset(payload: schemas.Reset, request: Request):
    ready()
    service.reset_password(payload.reset_token, payload.password, ip(request))
    return {'status': 'PASSWORD_UPDATED'}


@router.post('/logout')
def logout(request: Request, response: Response):
    settings = ready()
    service.logout(request.cookies.get(settings.session_cookie))
    response.delete_cookie(settings.session_cookie, path='/', secure=settings.secure, httponly=True, samesite='lax')
    return {'status': 'SIGNED_OUT'}


@router.patch('/profile')
def profile(payload: schemas.Profile, user: dict = Depends(get_current_user)):
    ready()
    return service.update_profile(user['user_id'], payload.display_name)


@router.post('/change-password')
def change(payload: schemas.Change, request: Request, response: Response, user: dict = Depends(get_current_user)):
    settings = ready()
    service.rate('password-change', str(user['user_id']), 5, 900)
    service.change_password(user['user_id'], payload.current_password, payload.password)
    response.delete_cookie(settings.session_cookie, path='/', secure=settings.secure, httponly=True, samesite='lax')
    return {'status': 'PASSWORD_UPDATED'}


async def auth_error(request, exc):
    return JSONResponse({'detail': exc.message}, status_code=exc.status)
