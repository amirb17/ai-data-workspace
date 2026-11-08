"""Offline website configuration checks. Never connects or prints secrets."""
import json
import os


def check(environ):
    from urllib.parse import urlsplit
    reasons=[]
    if environ.get('APP_ENV')!='production' or environ.get('AUTH_MODE')!='session': reasons.append('PUBLIC_SESSION_MODE_REQUIRED')
    if environ.get('ENABLE_DEV_IDENTITY','false').lower()!='false': reasons.append('DEVELOPMENT_IDENTITY_MUST_BE_DISABLED')
    if environ.get('AUTH_COOKIE_SECURE')!='true': reasons.append('SECURE_COOKIES_REQUIRED')
    if len(environ.get('AUTH_SECRET','').encode())<32: reasons.append('SESSION_SECRET_CONFIGURATION_REQUIRED')
    for key in ('AUTH_FRONTEND_URL','AUTH_BACKEND_URL'):
        try:
            value=urlsplit(environ.get(key,''))
            valid=value.scheme=='https' and value.netloc and not value.username and not value.password and value.path in ('','/') and not value.query and not value.fragment
        except ValueError: valid=False
        if not valid: reasons.append(key+'_INVALID')
    for key in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED','DATARISE_PROCESSING_READERS','DATARISE_DISPATCH_APPROVED'):
        if environ.get(key)!='false': reasons.append(key+'_MUST_BE_FALSE')
    return dict(configuration_checks_passed=not reasons,hosting_approved=False,processing_approved=False,reasons=reasons)


if __name__=='__main__':
    result=check(os.environ)
    print(json.dumps(result,sort_keys=True))
    raise SystemExit(int(not result['configuration_checks_passed']))
