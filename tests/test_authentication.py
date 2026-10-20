"""Phase 10: real isolated PostgreSQL schema; never migrate public during tests."""
import os
import sys
import uuid
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from app.main import app
from app.auth import service, repository, google
from app.auth.config import safe_return
from app.auth.security import password_hash, password_matches, new_csrf, valid_csrf, digest
from app.db import database


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('AUTH_MODE', 'session')
    monkeypatch.setenv('AUTH_SECRET', 'isolated-auth-test-secret-with-at-least-32-bytes')
    monkeypatch.setenv('AUTH_FRONTEND_URL', 'http://localhost:5173')
    monkeypatch.setenv('AUTH_BACKEND_URL', 'http://localhost:8000')
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    # Rate-limit assertions concern one fixed bucket. Real time may cross a
    # 15-minute boundary during a request loop; do not change global JWT time.
    monkeypatch.setattr(service, 'time', SimpleNamespace(time=lambda: 1_800_000_000.0))


@pytest.mark.parametrize('value', ['https://evil.example', '//evil.example', '/app/../login', '/%61pp/%2e%2e/x', '/app/\\evil', '/app/%0d%0aLocation:evil', '/login'])
def test_return_url_rejects_open_redirects(value):
    assert safe_return(value) == '/app'


def test_security_primitives(auth_env):
    encoded = password_hash('correct horse battery')
    assert encoded.startswith('$argon2id$') and password_matches(encoded, 'correct horse battery')
    assert not password_matches(encoded, 'wrong') and not password_matches(None, 'wrong')
    token = new_csrf()
    assert valid_csrf(token, token)
    assert not valid_csrf(token+'x', token+'x') and not valid_csrf(token, 'different')
    assert safe_return('/app/workspaces/1?tab=sql') == '/app/workspaces/1?tab=sql'


def test_additive_migration_on_populated_legacy_schema(auth_env):
    if os.getenv('RUN_DB_TESTS')!='1':pytest.skip('Set RUN_DB_TESTS=1')
    schema='test_phase10_migration_'+uuid.uuid4().hex
    with database.get_connection() as conn:conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with psycopg.connect(database.DATABASE_URL,options='-c search_path='+schema) as conn:
            for path in ('0001_baseline.sql','0007_identity_upload_sessions.sql'):
                conn.execute((Path('migrations')/path).read_text(encoding='utf-8'))
            original=conn.execute("INSERT INTO users(owner_key,display_name) VALUES('dev:preserved','Existing') RETURNING user_id,owner_key,display_name,created_at").fetchone()
            workspace=conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES('Existing','dev:preserved') RETURNING workspace_id").fetchone()[0]
            conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES(%s,'Orders','dev:preserved')",(workspace,))
            constraints_before=conn.execute("SELECT COUNT(*) FROM pg_constraint WHERE connamespace=current_schema()::regnamespace AND contype<>'n'").fetchone()[0]
            conn.execute(Path('migrations/0027_authentication.sql').read_text(encoding='utf-8'))
            assert conn.execute('SELECT user_id,owner_key,display_name,created_at FROM users').fetchone()==original
            assert conn.execute('SELECT COUNT(*) FROM workspaces').fetchone()[0]==1
            assert conn.execute('SELECT COUNT(*) FROM datasets').fetchone()[0]==1
            indexes=conn.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() AND (tablename LIKE 'auth_%' OR indexname='users_normalized_email_key')").fetchone()[0]
            assert indexes==14
            assert conn.execute("SELECT COUNT(*) FROM pg_sequences WHERE schemaname=current_schema() AND sequencename LIKE 'auth_%'").fetchone()[0]==2
            assert conn.execute("SELECT COUNT(*) FROM pg_constraint WHERE connamespace=current_schema()::regnamespace AND contype<>'n'").fetchone()[0]-constraints_before==25
            columns={row[0] for row in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='users'")}
            assert {'email','normalized_email','password_hash','email_verified','avatar_url','account_status','updated_at','last_login_at'}<=columns
    finally:
        with database.get_connection() as conn:conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.parametrize('changed', [{}, {'iss':'https://evil.example'}, {'aud':'wrong-client'}, {'nonce':'wrong-nonce'}, {'exp':1}])
def test_oidc_real_signed_claim_validation(changed):
    from authlib.integrations.starlette_client import OAuth
    from joserfc.jwk import RSAKey
    from joserfc import jwt
    key=RSAKey.generate_key(2048,parameters={'kid':'fixture'})
    claims={'iss':'https://accounts.google.com','aud':'fixture-client','sub':'stable-subject',
            'iat':int(time.time()),'exp':int(time.time())+600,'nonce':'fixture-nonce','email_verified':True}
    encoded=jwt.encode({'alg':'RS256','kid':'fixture'}, {**claims,**changed}, key)
    remote=OAuth().register('fixture',client_id='fixture-client',issuer='https://accounts.google.com',
        jwks={'keys':[key.as_dict(private=False)]},id_token_signing_alg_values_supported=['RS256'])
    if changed:
        with pytest.raises(Exception):asyncio.run(remote.parse_id_token({'id_token':encoded},nonce='fixture-nonce'))
    else:
        assert asyncio.run(remote.parse_id_token({'id_token':encoded},nonce='fixture-nonce'))['sub']=='stable-subject'


@pytest.fixture
def context(auth_env, monkeypatch):
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('Set RUN_DB_TESTS=1 for isolated auth integration tests')
    schema = 'test_phase10_'+uuid.uuid4().hex
    original = database.get_connection
    with original() as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def connect():
        return psycopg.connect(database.DATABASE_URL, options=f'-c search_path={schema}')
    try:
        with connect() as connection:
            for migration in sorted(Path('migrations').glob('*.sql')):
                connection.execute(migration.read_text(encoding='utf-8'))
        for name, module in list(sys.modules.items()):
            if name.startswith('app.') and hasattr(module, 'get_connection'):
                monkeypatch.setattr(module, 'get_connection', connect)
        sent = []
        monkeypatch.setattr(service.mail, 'configured', lambda: True)
        monkeypatch.setattr(service.mail, 'send_code', lambda email, code, purpose: sent.append((email,code,purpose)))
        yield connect, sent
    finally:
        with original() as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def client():
    result = TestClient(app)
    csrf = result.get('/auth/csrf')
    assert csrf.status_code == 200
    result.headers.update({'Origin': 'http://localhost:5173', 'X-CSRF-Token': csrf.json()['csrf_token']})
    return result


def signup(c, sent, email='alice@example.com'):
    response = c.post('/auth/signup', json={'display_name':'Alice','email':email,'password':'correct horse battery','confirm_password':'correct horse battery'})
    assert response.status_code == 200, response.text
    challenge = response.json()['challenge_id']
    assert sent[-1][0] == email
    return challenge


def verified(c, sent, email='alice@example.com'):
    challenge = signup(c, sent, email)
    assert c.post('/auth/verify', json={'challenge_id':challenge,'purpose':'VERIFY_EMAIL','code':sent[-1][1]}).status_code == 200
    response = c.post('/auth/login', json={'email':email,'password':'correct horse battery'})
    assert response.status_code == 200, response.text
    return c.get('/me').json()


def test_signup_session_logout_and_safe_errors(context):
    connect,sent = context
    c = client()
    assert c.get('/me').status_code == 401
    challenge = signup(c,sent)
    assert 'code' not in c.post('/auth/resend',json={'challenge_id':challenge}).json()
    assert c.post('/auth/resend',json={'challenge_id':challenge}).status_code == 429
    otp = sent[-1][1]
    assert c.post('/auth/login',json={'email':'alice@example.com','password':'correct horse battery'}).status_code == 401
    assert c.post('/auth/verify',json={'challenge_id':challenge,'purpose':'VERIFY_EMAIL','code':otp}).status_code == 200
    assert c.post('/auth/verify',json={'challenge_id':challenge,'purpose':'VERIFY_EMAIL','code':otp}).status_code == 400
    assert c.post('/auth/login',json={'email':'ALICE@EXAMPLE.COM','password':'correct horse battery'}).status_code == 200
    me = c.get('/me').json()
    assert me['email']=='alice@example.com' and me['identity_mode']=='session' and 'password_hash' not in me
    with connect() as conn:
        stored=conn.execute('SELECT token_digest FROM auth_sessions').fetchone()[0]
        assert stored == digest(c.cookies.get('datarise_session'))
    assert c.post('/auth/logout',json={}).status_code == 200
    assert c.get('/me').status_code == 401
    bad = c.post('/auth/signup',json={'email':'bad','password':'never-echo-this-secret','confirm_password':'mismatch'})
    assert bad.status_code==422 and 'never-echo' not in bad.text


def test_reset_replay_revokes_sessions_and_enumeration(context):
    connect,sent=context
    c=client();verified(c,sent)
    existing=c.post('/auth/forgot-password',json={'email':'alice@example.com'}).json()
    count=len(sent)
    missing=c.post('/auth/forgot-password',json={'email':'missing@example.com'}).json()
    assert len(sent)==count
    assert existing.keys()==missing.keys() and existing['message']==missing['message']
    response=c.post('/auth/verify',json={'challenge_id':existing['challenge_id'],'purpose':'RESET_PASSWORD','code':sent[-1][1]})
    assert response.status_code==200
    payload={'reset_token':response.json()['reset_token'],'password':'another safe password','confirm_password':'another safe password'}
    assert c.post('/auth/reset-password',json=payload).status_code==200
    assert c.post('/auth/reset-password',json=payload).status_code==400
    assert c.get('/me').status_code==401
    assert c.post('/auth/login',json={'email':'alice@example.com','password':'correct horse battery'}).status_code==401
    assert c.post('/auth/login',json={'email':'alice@example.com','password':'another safe password'}).status_code==200


def test_verified_google_first_password_preserves_identity(context):
    connect,sent=context
    token=google.resolve_identity({'sub':'first-password-subject','email':'recovery@gmail.com','email_verified':True})
    user=service.session(token)
    c=client()
    c.cookies.set('datarise_session',token,domain='testserver.local',path='/')
    assert c.get('/me').json()['has_password'] is False
    response=c.post('/auth/forgot-password',json={'email':'recovery@gmail.com'})
    assert response.status_code==200 and len(sent)==1
    challenge=response.json()['challenge_id']
    grant=c.post('/auth/verify',json={'challenge_id':challenge,'purpose':'RESET_PASSWORD','code':sent[-1][1]})
    assert grant.status_code==200
    payload={'reset_token':grant.json()['reset_token'],'password':'first safe password','confirm_password':'different password'}
    assert c.post('/auth/reset-password',json=payload).status_code==422
    payload['confirm_password']=payload['password']
    assert c.post('/auth/reset-password',json=payload).status_code==200
    assert c.post('/auth/reset-password',json=payload).status_code==400
    assert c.get('/me').status_code==401
    assert c.post('/auth/login',json={'email':'recovery@gmail.com','password':'first safe password'}).status_code==200
    assert c.get('/me').json()['user_id']==user['user_id']
    assert set(c.get('/me').json()['login_methods'])=={'password','google'}
    assert service.session(google.resolve_identity({'sub':'first-password-subject','email':'recovery@gmail.com','email_verified':True}))['user_id']==user['user_id']
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM users').fetchone()[0]==1
        assert conn.execute('SELECT password_hash FROM users').fetchone()[0].startswith('$argon2id$')
        conn.execute("UPDATE users SET account_status='DISABLED'")
    before=len(sent)
    with connect() as conn:
        conn.execute("UPDATE auth_challenges SET created_at=NOW()-INTERVAL '2 minutes'")
    assert c.post('/auth/forgot-password',json={'email':'recovery@gmail.com'}).status_code==200
    assert len(sent)==before


def test_reset_resend_supersedes_code_and_delivery_failure_is_generic(context,monkeypatch):
    connect,sent=context;c=client();verified(c,sent)
    first=c.post('/auth/forgot-password',json={'email':'alice@example.com'}).json()
    old=sent[-1][1]
    assert c.post('/auth/resend',json={'challenge_id':first['challenge_id']}).status_code==429
    with connect() as conn:
        conn.execute("UPDATE auth_challenges SET created_at=NOW()-INTERVAL '2 minutes'")
    replacement=c.post('/auth/resend',json={'challenge_id':first['challenge_id']})
    assert replacement.status_code==200
    assert c.post('/auth/verify',json={'challenge_id':first['challenge_id'],'purpose':'RESET_PASSWORD','code':old}).status_code==400
    with connect() as conn:
        conn.execute("UPDATE auth_challenges SET created_at=NOW()-INTERVAL '20 minutes',expires_at=NOW()-INTERVAL '1 minute' WHERE challenge_id=%s",(replacement.json()['challenge_id'],))
    assert c.post('/auth/verify',json={'challenge_id':replacement.json()['challenge_id'],'purpose':'RESET_PASSWORD','code':sent[-1][1]}).status_code==400
    def unavailable(*args):raise RuntimeError('private-provider-message')
    monkeypatch.setattr(service.mail,'send_code',unavailable)
    response=c.post('/auth/forgot-password',json={'email':'alice@example.com'})
    unknown=c.post('/auth/forgot-password',json={'email':'missing@example.com'})
    assert response.status_code==unknown.status_code==200
    assert response.json()['message']==unknown.json()['message']
    assert 'private-provider-message' not in response.text
    with connect() as conn:
        assert conn.execute("SELECT category FROM auth_security_events WHERE event_type='EMAIL_DELIVERY_FAILURE'").fetchone()[0]=='PROVIDER_UNAVAILABLE'


@pytest.mark.parametrize('endpoint', ['request', 'verify', 'resend'])
def test_recovery_endpoint_rate_limits(context, endpoint):
    connect,sent=context;c=client()
    if endpoint=='request':
        responses=[c.post('/auth/forgot-password',json={'email':f'absent{index}@example.com'}) for index in range(21)]
        assert all(response.status_code==200 for response in responses[:-1])
    elif endpoint=='verify':
        challenge=str(uuid.uuid4())
        responses=[c.post('/auth/verify',json={'challenge_id':challenge,'purpose':'RESET_PASSWORD','code':'000000'}) for _ in range(31)]
        assert all(response.status_code==400 for response in responses[:-1])
    else:
        verified(c,sent)
        challenge=c.post('/auth/forgot-password',json={'email':'alice@example.com'}).json()['challenge_id']
        responses=[c.post('/auth/resend',json={'challenge_id':challenge}) for _ in range(5)]
        assert all(response.status_code==429 for response in responses)
    assert responses[-1].status_code==429
    assert 'Too many attempts' in responses[-1].json()['detail']
    assert 'hits' not in responses[-1].text and 'bucket' not in responses[-1].text


def test_rate_fixed_window_rollover(context, monkeypatch):
    clock=[1799.0]
    monkeypatch.setattr(service, 'time', SimpleNamespace(time=lambda: clock[0]))
    service.rate('rollover-proof','synthetic',1,900)
    with pytest.raises(service.AuthError) as first:
        service.rate('rollover-proof','synthetic',1,900)
    assert first.value.status==429
    clock[0]=1800.0
    service.rate('rollover-proof','synthetic',1,900)
    with pytest.raises(service.AuthError) as second:
        service.rate('rollover-proof','synthetic',1,900)
    assert second.value.status==429
    connect,_=context
    with connect() as conn:
        assert conn.execute('SELECT window_start,hits FROM auth_rate_limits').fetchone()==(2,2)


def test_attempts_expiry_duplicate_and_rate(context):
    connect,sent=context;c=client();challenge=signup(c,sent)
    code=sent[-1][1];wrong='000000' if code!='000000' else '111111'
    for _ in range(5):
        assert c.post('/auth/verify',json={'challenge_id':challenge,'purpose':'VERIFY_EMAIL','code':wrong}).status_code==400
    assert c.post('/auth/verify',json={'challenge_id':challenge,'purpose':'VERIFY_EMAIL','code':code}).status_code==400
    with connect() as conn:
        assert conn.execute('SELECT attempts FROM auth_challenges WHERE challenge_id=%s',(challenge,)).fetchone()[0]==5
        conn.execute("UPDATE auth_challenges SET created_at=NOW()-INTERVAL '2 minutes'")
    duplicate=signup(c,sent)
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1
        conn.execute("UPDATE auth_challenges SET created_at=NOW()-INTERVAL '20 minutes',expires_at=NOW()-INTERVAL '1 minute' WHERE challenge_id=%s",(duplicate,))
    assert c.post('/auth/verify',json={'challenge_id':duplicate,'purpose':'VERIFY_EMAIL','code':sent[-1][1]}).status_code==400
    for _ in range(10): service.rate('test','same',10,900)
    with pytest.raises(service.AuthError) as error: service.rate('test','same',10,900)
    assert error.value.status==429


def test_session_disabled_tampering_expiry_csrf(context):
    connect,sent=context;c=client();me=verified(c,sent)
    token=c.cookies.get('datarise_session')
    assert service.session(token+'tampered') is None
    with connect() as conn:conn.execute("UPDATE auth_sessions SET last_seen_at=NOW()-INTERVAL '31 minutes'")
    assert c.get('/me').status_code==401
    c.post('/auth/login',json={'email':'alice@example.com','password':'correct horse battery'})
    with connect() as conn:conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(me['user_id'],))
    assert c.get('/me').status_code==401
    known=c.post('/auth/login',json={'email':'alice@example.com','password':'wrong'});unknown=c.post('/auth/login',json={'email':'unknown@example.com','password':'wrong'})
    assert known.status_code==unknown.status_code==401 and known.json()==unknown.json()
    assert TestClient(app).post('/auth/login',json={'email':'alice@example.com','password':'wrong'}).status_code==403
    assert c.post('/auth/logout',json={},headers={'Origin':'https://evil.example'}).status_code==403


def test_google_account_resolution(context):
    connect,sent=context;c=client();me=verified(c,sent,'alice@gmail.com')
    claims={'sub':'google-stable-subject','email':'alice@gmail.com','email_verified':True,'name':'Alice'}
    token=google.resolve_identity(claims)
    assert service.session(token)['user_id']==me['user_id']
    assert google.resolve_identity({**claims,'email':'changed@gmail.com'})
    with pytest.raises(service.AuthError):google.resolve_identity({**claims,'sub':'other','email_verified':False})
    other=google.resolve_identity({'sub':'second','email':'bob@example.com','email_verified':True})
    assert service.session(other)['user_id']!=me['user_id']
    assert c.get('/auth/google/callback?state=tampered&code=never-return-this',follow_redirects=False).headers['location']=='http://localhost:5173/login?error=google'


def test_google_state_binding_callback_failure_and_replay(context,monkeypatch):
    from authlib.integrations.starlette_client import OAuth
    connect,sent=context;c=client()
    remote=OAuth().register('fixture',client_id='fixture-client',authorize_url='https://accounts.google.com/authorize',
        access_token_url='https://accounts.google.com/token',client_kwargs={'scope':'openid email profile','code_challenge_method':'S256'})
    called=[]
    async def fetch(**kwargs):
        called.append(kwargs)
        raise RuntimeError('provider error containing a synthetic-secret-code')
    monkeypatch.setattr(remote,'fetch_access_token',fetch)
    monkeypatch.setattr(google,'provider',lambda:remote)
    start=c.get('/auth/google/start?return_to=https://evil.example',follow_redirects=False)
    from urllib.parse import urlsplit,parse_qs
    parameters=parse_qs(urlsplit(start.headers['location']).query)
    state=parameters['state'][0]
    assert parameters['code_challenge_method']==['S256'] and parameters['nonce']
    # The DB state alone is insufficient without the signed browser handshake cookie.
    other=client()
    denied=other.get('/auth/google/callback?state='+state+'&code=fake',follow_redirects=False)
    assert denied.status_code==303 and not called
    start=c.get('/auth/google/start',follow_redirects=False)
    state=parse_qs(urlsplit(start.headers['location']).query)['state'][0]
    failed=c.get('/auth/google/callback?state='+state+'&code=fake',follow_redirects=False)
    assert failed.headers['location']=='http://localhost:5173/login?error=google'
    assert len(called)==1 and 'synthetic-secret-code' not in failed.text
    assert 'code_verifier' in called[0]
    c.get('/auth/google/callback?state='+state+'&code=fake',follow_redirects=False)
    assert len(called)==1
    async def unavailable(*args,**kwargs):raise RuntimeError('private provider diagnostics')
    monkeypatch.setattr(remote,'authorize_redirect',unavailable)
    failed_start=c.get('/auth/google/start',follow_redirects=False)
    assert failed_start.headers['location']=='http://localhost:5173/login?error=google'
    assert 'private provider diagnostics' not in failed_start.text


def test_duplicate_verified_signup_profile_and_password_change(context):
    connect,sent=context;c=client();me=verified(c,sent)
    before=len(sent)
    with connect() as conn:conn.execute("UPDATE auth_challenges SET created_at=NOW()-INTERVAL '2 minutes'")
    signup_response=c.post('/auth/signup',json={'display_name':'Intruder','email':'alice@example.com','password':'different attacker pass','confirm_password':'different attacker pass'})
    assert signup_response.status_code==200 and len(sent)==before
    assert c.get('/me').json()['display_name']=='Alice'
    assert c.patch('/auth/profile',json={'display_name':'Alice Updated'}).status_code==200
    assert c.get('/me').json()['display_name']=='Alice Updated'
    assert c.patch('/auth/profile',json={'display_name':'   '}).status_code==422
    first=c.get('/auth/csrf').json()['csrf_token']
    second=c.get('/auth/csrf').json()['csrf_token']
    assert first==second,'Concurrent tabs reuse a valid CSRF cookie'
    changed=c.post('/auth/change-password',json={'current_password':'correct horse battery','password':'new correct passphrase','confirm_password':'new correct passphrase'})
    assert changed.status_code==200
    assert c.get('/me').status_code==401
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL').fetchone()[0]==0


def test_migration_objects_preserve_legacy_ownership(context):
    connect,_=context
    with connect() as conn:
        tables={row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'auth_%'")}
        assert tables=={'auth_identities','auth_sessions','auth_challenges','auth_rate_limits','auth_oauth_states','auth_security_events'}
        assert conn.execute("SELECT COUNT(*) FROM pg_trigger WHERE tgrelid='users'::regclass AND tgname='auth_user_updated_at'").fetchone()[0]==1
        user_id=conn.execute("INSERT INTO users(owner_key,display_name) VALUES('dev:legacy','Legacy') RETURNING user_id").fetchone()[0]
        conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES('Legacy','dev:legacy')")
        assert conn.execute('SELECT owner FROM workspaces').fetchone()[0]=='dev:legacy'
        assert conn.execute('SELECT normalized_email FROM users WHERE user_id=%s',(user_id,)).fetchone()[0] is None


def test_auth_writers_lock_user_before_challenge_or_session(context,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    connect,sent=context;c=client();challenge=signup(c,sent)
    with connect() as conn:user_id=conn.execute('SELECT user_id FROM users').fetchone()[0]
    waiting=Event();original=repository.user
    def user(cursor,**lookup):
        if current_thread().name.startswith('auth-lock-order'):waiting.set()
        return original(cursor,**lookup)
    monkeypatch.setattr(repository,'user',user)
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='auth-lock-order') as pool:
        with connect() as holder:
            holder.execute('SELECT user_id FROM users WHERE user_id=%s FOR UPDATE',(user_id,))
            pending=pool.submit(service.verify_code,uuid.UUID(challenge),sent[-1][1],'lock-test','VERIFY_EMAIL')
            assert waiting.wait(5)
            # Verification must wait for the user without holding the challenge.
            holder.execute('SELECT challenge_id FROM auth_challenges WHERE challenge_id=%s FOR UPDATE NOWAIT',(challenge,))
        assert pending.result(timeout=5)['status']=='VERIFIED'
        token,_=service.login('alice@example.com','correct horse battery','lock-test')
        waiting.clear()
        with connect() as holder:
            holder.execute('SELECT user_id FROM users WHERE user_id=%s FOR UPDATE',(user_id,))
            pending=pool.submit(service.logout,token)
            assert waiting.wait(5)
            holder.execute('SELECT token_digest FROM auth_sessions WHERE token_digest=%s FOR UPDATE NOWAIT',(digest(token),))
        pending.result(timeout=5)


def test_cross_user_guessed_resource_ids(context):
    connect,sent=context;a=client();owner=verified(a,sent)
    ws=a.post('/workspaces',json={'workspace_name':'Private A','owner':'spoof'}).json()['workspace_id']
    ds=a.post(f'/workspaces/{ws}/datasets',json={'dataset_name':'Orders'}).json()['dataset_id']
    b=client();verified(b,sent,'bob@example.com')
    assert b.get('/workspaces').json()['workspaces']==[]
    denied=[('GET',f'/workspaces/{ws}',None),('POST',f'/workspaces/{ws}/datasets',{'dataset_name':'intrusion'}),
      ('GET',f'/workspaces/{ws}/datasets',None),('GET',f'/workspaces/{ws}/datasets/{ds}',None),
      ('POST','/files/initiate',{'workspace_id':ws,'dataset_id':ds,'file_name':'a.csv','file_size':10}),
      ('POST',f'/workspaces/{ws}/datasets/{ds}/processing/pending',{}),
      ('GET',f'/workspaces/{ws}/datasets/{ds}/profile',None),('GET',f'/workspaces/{ws}/relationships',None),
      ('GET',f'/workspaces/{ws}/metrics',None),('POST',f'/workspaces/{ws}/analytics/refresh',{}),
      ('POST',f'/workspaces/{ws}/questions',{'question':'How many orders?'}),('GET',f'/workspaces/{ws}/questions/answers',None),
      ('GET',f'/workspaces/{ws}/questions/answers/999',None),('POST',f'/workspaces/{ws}/sql/execute',{'sql_text':'SELECT 1'}),
      ('GET',f'/workspaces/{ws}/sql/queries',None),('GET',f'/workspaces/{ws}/sql/executions',None),
      ('PATCH',f'/workspaces/{ws}/sql/queries/999',{'name':'stolen','sql_text':'SELECT 1'}),
      ('DELETE',f'/workspaces/{ws}/sql/queries/999',{'confirmed':True,'expected_version':1})]
    for method,path,payload in denied:
        result=b.request(method,path,**({'json':payload} if payload is not None else {}))
        assert result.status_code in (403,404), (method,path,result.status_code,result.text)
    assert a.get(f'/workspaces/{ws}').status_code==200
    with connect() as conn:
        assert conn.execute('SELECT owner FROM workspaces').fetchone()[0]==owner['owner_key']
