import hmac
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta
from app.auth import repository as repo, email as mail
from app.auth.security import normalize_email, password_hash, password_matches, digest, protected_digest


class AuthError(Exception):
    def __init__(self, message, status=400):
        self.message=message;self.status=status


def rate(purpose, key, maximum, seconds):
    bucket=protected_digest('rate:'+purpose,key)
    window=int(time.time())//seconds
    with repo.transaction() as cursor:
        row=cursor.execute('''INSERT INTO auth_rate_limits(bucket_key,window_start,hits) VALUES(%s,%s,1)
          ON CONFLICT(bucket_key) DO UPDATE SET window_start=EXCLUDED.window_start,
          hits=CASE WHEN auth_rate_limits.window_start=EXCLUDED.window_start THEN auth_rate_limits.hits+1 ELSE 1 END RETURNING hits''',(bucket,window)).fetchone()
    if row['hits']>maximum:raise AuthError('Too many attempts. Please try again later.',429)


def profile(user_id):
    with repo.transaction() as cursor:
        row=repo.user(cursor,user_id=user_id)
        methods=cursor.execute('SELECT provider FROM auth_identities WHERE user_id=%s',(user_id,)).fetchall()
    return {**repo.principal(row),'has_password':bool(row['password_hash']),'login_methods':(['password'] if row['password_hash'] else [])+[r['provider'] for r in methods]}


def session(token):
    if not token or len(token)>128:return None
    with repo.transaction() as cursor:
        row=cursor.execute('''SELECT u.*,s.token_digest FROM auth_sessions s JOIN users u USING(user_id)
          WHERE token_digest=%s AND revoked_at IS NULL AND expires_at>NOW()
          AND last_seen_at>NOW()-INTERVAL '30 minutes' AND account_status='ACTIVE' ''',(digest(token),)).fetchone()
        if row:
            cursor.execute("UPDATE auth_sessions SET last_seen_at=NOW() WHERE token_digest=%s AND last_seen_at<NOW()-INTERVAL '5 minutes'",(row['token_digest'],))
    return repo.principal(row) if row else None


def issue_session(cursor, user_id):
    token=secrets.token_urlsafe(32)
    cursor.execute("INSERT INTO auth_sessions(token_digest,user_id,expires_at) VALUES(%s,%s,NOW()+INTERVAL '12 hours')",(digest(token),user_id))
    cursor.execute('UPDATE users SET last_login_at=NOW() WHERE user_id=%s',(user_id,))
    repo.event(cursor,'LOGIN_SUCCESS',user_id)
    return token


def login(email, password, ip):
    email=normalize_email(email)
    rate('login-ip',ip,30,900);rate('login-email',email,10,900)
    with repo.transaction() as cursor:
        row=repo.user(cursor,normalized_email=email)
    valid=password_matches(row['password_hash'] if row else None,password)
    if not valid or row['account_status']!='ACTIVE' or not row['email_verified']:
        with repo.transaction() as cursor:repo.event(cursor,'LOGIN_FAILURE',None,'INVALID_CREDENTIALS')
        raise AuthError('Email or password is incorrect, or email verification is incomplete.',401)
    with repo.transaction() as cursor:
        current=repo.user(cursor,user_id=row['user_id'])
        if current['password_hash']!=row['password_hash'] or current['account_status']!='ACTIVE':
            raise AuthError('Please sign in again.',401)
        token=issue_session(cursor,row['user_id'])
    return token,row['user_id']


def logout(token):
    if not token:return
    with repo.transaction() as cursor:
        principal=cursor.execute('SELECT user_id FROM auth_sessions WHERE token_digest=%s',(digest(token),)).fetchone()
        if not principal:return
        repo.user(cursor,user_id=principal['user_id'])
        row=cursor.execute('UPDATE auth_sessions SET revoked_at=NOW() WHERE token_digest=%s AND revoked_at IS NULL RETURNING user_id',(digest(token),)).fetchone()
        if row:repo.event(cursor,'LOGOUT',row['user_id'])


def masked(email):
    local,domain=email.split('@',1)
    return local[0]+'***@'+domain


def request_code(email, purpose, ip, signup=None, pending_hash=None, delivery=None):
    if not mail.configured():raise AuthError('Email delivery is temporarily unavailable.',503)
    email=normalize_email(email)
    rate('code-ip',ip,20,3600);rate('code-email:'+purpose,email,5,900)
    challenge=str(uuid.uuid4());code=f'{secrets.randbelow(1_000_000):06d}'
    password=pending_hash or (password_hash(signup['password']) if signup else None)
    with repo.transaction() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('auth:'+protected_digest(purpose,email),))
        recent=cursor.execute('''SELECT 1 FROM auth_challenges WHERE normalized_email=%s AND purpose=%s AND created_at>NOW()-INTERVAL '60 seconds' ''',(email,purpose)).fetchone()
        if recent:raise AuthError('Wait 60 seconds before requesting another code.',429)
        if signup:
            cursor.execute('''INSERT INTO users(owner_key,display_name,email,normalized_email) VALUES(%s,%s,%s,%s)
              ON CONFLICT(normalized_email) WHERE normalized_email IS NOT NULL DO NOTHING''',('usr:'+str(uuid.uuid4()),signup['display_name'].strip(),email,email))
        row=repo.user(cursor,normalized_email=email)
        # Email recovery may establish a first password on an already verified
        # Google account. The same one-use email proof and reset safeguards apply;
        # no Google identity, account ID or ownership metadata is replaced.
        recoverable = bool(row and row['email_verified'] and (row['password_hash'] or
            cursor.execute("SELECT 1 FROM auth_identities WHERE user_id=%s AND provider='google'", (row['user_id'],)).fetchone()))
        eligible=bool(row and row['account_status']=='ACTIVE' and (not row['email_verified'] if purpose=='VERIFY_EMAIL' else recoverable))
        cursor.execute('''UPDATE auth_challenges SET consumed_at=COALESCE(consumed_at,NOW()),completed_at=NOW()
          WHERE normalized_email=%s AND purpose=%s AND completed_at IS NULL''',(email,purpose))
        cursor.execute('''INSERT INTO auth_challenges(challenge_id,user_id,normalized_email,purpose,code_digest,expires_at,pending_password_hash,pending_display_name)
          VALUES(%s,%s,%s,%s,%s,NOW()+INTERVAL '10 minutes',%s,%s)''',
          (challenge,row['user_id'] if eligible else None,email,purpose,protected_digest('otp:'+challenge,code),password if eligible and purpose=='VERIFY_EMAIL' else None,signup['display_name'].strip() if signup else None))
        repo.event(cursor,'SIGNUP' if purpose=='VERIFY_EMAIL' else 'RESET_REQUESTED',row['user_id'] if eligible else None)
    if eligible:
        (delivery or deliver_code)(email,code,purpose)
    return {'challenge_id':challenge,'masked_email':masked(email),'resend_after':60,'expires_in':600,
            'message':"If an eligible account exists for this email, we've sent a verification code."}


def deliver_code(email, code, purpose):
    try:mail.send_code(email,code,purpose)
    except Exception:
        # Delivery errors cannot distinguish registered addresses in HTTP responses.
        with repo.transaction() as cursor:repo.event(cursor,'EMAIL_DELIVERY_FAILURE',None,'PROVIDER_UNAVAILABLE')


def resend(challenge_id, ip, delivery=None):
    with repo.transaction() as cursor:
        row=cursor.execute('SELECT * FROM auth_challenges WHERE challenge_id=%s',(challenge_id,)).fetchone()
    if not row or row['completed_at']:raise AuthError('Request a new code to continue.')
    return request_code(row['normalized_email'],row['purpose'],ip,pending_hash=row['pending_password_hash'],delivery=delivery)


def verify_code(challenge_id, code, ip, purpose):
    rate('verify-ip',ip,30,900)
    failure=False;token=None
    with repo.transaction() as cursor:
        identity=cursor.execute('SELECT user_id FROM auth_challenges WHERE challenge_id=%s',(challenge_id,)).fetchone()
        # All auth writers lock user before challenge/session, including resends.
        # Re-read the challenge after obtaining the lock to reject superseded codes.
        user=repo.user(cursor,user_id=identity['user_id']) if identity and identity['user_id'] else None
        row=cursor.execute('SELECT * FROM auth_challenges WHERE challenge_id=%s FOR UPDATE',(challenge_id,)).fetchone()
        now=datetime.now(timezone.utc)
        if not row or row['purpose']!=purpose or row['consumed_at'] or row['expires_at']<=now or row['attempts']>=5:
            failure=True
        else:
            cursor.execute('UPDATE auth_challenges SET attempts=attempts+1 WHERE challenge_id=%s',(challenge_id,))
            if not row['user_id'] or not hmac.compare_digest(row['code_digest'],protected_digest('otp:'+str(challenge_id),code)):
                failure=True
            else:
                if user['account_status']!='ACTIVE':failure=True
                elif purpose=='VERIFY_EMAIL':
                    if not row['pending_password_hash'] or user['email_verified']:failure=True
                    else:
                        cursor.execute('UPDATE users SET email_verified=TRUE,password_hash=%s,display_name=COALESCE(%s,display_name) WHERE user_id=%s',(row['pending_password_hash'],row['pending_display_name'],user['user_id']))
                        cursor.execute('UPDATE auth_challenges SET consumed_at=NOW(),completed_at=NOW(),pending_password_hash=NULL WHERE challenge_id=%s',(challenge_id,))
                        repo.event(cursor,'EMAIL_VERIFIED',user['user_id'])
                else:
                    token=secrets.token_urlsafe(32)
                    cursor.execute("UPDATE auth_challenges SET consumed_at=NOW(),grant_digest=%s,grant_expires_at=NOW()+INTERVAL '10 minutes' WHERE challenge_id=%s",(digest(token),challenge_id))
    if failure:raise AuthError('The code is invalid or expired. Request a new code if needed.')
    return {'status':'VERIFIED',**({'reset_token':token} if token else {})}


def reset_password(token, password, ip):
    rate('reset-ip',ip,20,900)
    encoded=password_hash(password)
    with repo.transaction() as cursor:
        query="SELECT * FROM auth_challenges WHERE grant_digest=%s AND purpose='RESET_PASSWORD' AND consumed_at IS NOT NULL AND completed_at IS NULL AND grant_expires_at>NOW()"
        preview=cursor.execute(query,(digest(token),)).fetchone()
        row=repo.user(cursor,user_id=preview['user_id']) if preview else None
        challenge=cursor.execute(query+' FOR UPDATE',(digest(token),)).fetchone()
        if not challenge:raise AuthError('This reset request has expired. Request a new code.')
        if row['account_status']!='ACTIVE':raise AuthError('This reset request has expired. Request a new code.')
        cursor.execute('UPDATE users SET password_hash=%s WHERE user_id=%s',(encoded,row['user_id']))
        cursor.execute('UPDATE auth_challenges SET completed_at=NOW(),grant_digest=NULL,grant_expires_at=NULL WHERE challenge_id=%s',(challenge['challenge_id'],))
        cursor.execute('UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL',(row['user_id'],))
        repo.event(cursor,'RESET_COMPLETED',row['user_id'])


def change_password(user_id, current, replacement):
    encoded=password_hash(replacement)
    with repo.transaction() as cursor:
        row=repo.user(cursor,user_id=user_id)
        if not row['password_hash'] or not password_matches(row['password_hash'],current):raise AuthError('Current password is incorrect.')
        cursor.execute('UPDATE users SET password_hash=%s WHERE user_id=%s',(encoded,user_id))
        cursor.execute('UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL',(user_id,))
        repo.event(cursor,'PASSWORD_CHANGED',user_id)


def update_profile(user_id, display_name):
    with repo.transaction() as cursor:cursor.execute('UPDATE users SET display_name=%s WHERE user_id=%s',(display_name.strip(),user_id))
    return profile(user_id)
