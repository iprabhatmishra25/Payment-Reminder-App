from datetime import timedelta
import pyotp
from sqlalchemy import select
from app.db import AuthSession,AuthToken,User,Outbox,now
from app.security import digest,encrypt


def test_login_and_logout_revokes_access(env):
    c=env['client'];r=c.post('/api/v1/auth/login',json={'email':'admin@example.com','password':'TestPassword!2026'})
    assert r.status_code==200
    h={'Authorization':'Bearer '+r.json()['access_token']}
    assert c.post('/api/v1/auth/logout',headers=h).status_code==200
    assert c.get('/api/v1/companies',headers=h).status_code==401


def test_missing_and_expired_sessions_rejected(env):
    c=env['client'];assert c.get('/api/v1/companies').status_code==401
    with env['factory']() as db:
        session=db.scalar(select(AuthSession).where(AuthSession.user_id==env['admin_id']));session.access_expires=now()-timedelta(seconds=1);db.commit()
    assert c.get('/api/v1/companies',headers=env['headers']['admin']).status_code==401


def test_refresh_rotates_secret_and_invalidates_old_refresh(env):
    c=env['client'];r=c.post('/api/v1/auth/login',json={'email':'admin@example.com','password':'TestPassword!2026'}).json()
    refreshed=c.post('/api/v1/auth/refresh',json={'refresh_token':r['refresh_token']})
    assert refreshed.status_code==200
    assert c.post('/api/v1/auth/refresh',json={'refresh_token':r['refresh_token']}).status_code==401


def test_viewer_and_accounts_cannot_manage_users(env):
    for role in ('viewer','accounts'):
        assert env['client'].get('/api/v1/users',headers=env['headers'][role]).status_code==403


def test_login_rate_limit(env):
    c=env['client']
    for i in range(10):assert c.post('/api/v1/auth/login',json={'email':'admin@example.com','password':'wrong-password'}).status_code==401
    assert c.post('/api/v1/auth/login',json={'email':'admin@example.com','password':'wrong-password'}).status_code==429


def test_mfa_enrollment_and_challenge(env):
    c=env['client'];h=env['headers']['admin']
    setup=c.post('/api/v1/auth/mfa/setup',headers=h,json={'password':'TestPassword!2026'})
    assert setup.status_code==200
    secret=setup.json()['secret'];code=pyotp.TOTP(secret).now()
    assert c.post('/api/v1/auth/mfa/confirm',headers=h,json={'code':code}).status_code==200
    login=c.post('/api/v1/auth/login',json={'email':'admin@example.com','password':'TestPassword!2026'}).json()
    assert login['mfa_required']
    result=c.post('/api/v1/auth/mfa/verify',json={'challenge':login['challenge'],'code':code})
    assert result.status_code==200
    assert c.post('/api/v1/auth/mfa/verify',json={'challenge':login['challenge'],'code':code}).status_code==401


def test_reset_token_single_use_and_session_revocation(env):
    with env['factory']() as db:
        db.add(AuthToken(user_id=env['admin_id'],token_hash=digest('a-long-one-time-reset-token'),purpose='reset',expires_at=now()+timedelta(minutes=20)));db.commit()
    body={'token':'a-long-one-time-reset-token','password':'ReplacementPassword!2026'}
    assert env['client'].post('/api/v1/auth/reset-password',json=body).status_code==200
    assert env['client'].post('/api/v1/auth/reset-password',json=body).status_code==422
    assert env['client'].get('/api/v1/companies',headers=env['headers']['admin']).status_code==401


def test_reset_does_not_reveal_account_existence(env):
    a=env['client'].post('/api/v1/auth/forgot-password',json={'email':'admin@example.com'})
    b=env['client'].post('/api/v1/auth/forgot-password',json={'email':'absent@example.com'})
    assert a.status_code==b.status_code==200 and a.json()==b.json()


def test_cannot_revoke_another_users_session(env):
    with env['factory']() as db:session=db.scalar(select(AuthSession).where(AuthSession.user_id==env['admin_id']))
    assert env['client'].delete('/api/v1/auth/sessions/'+session.id,headers=env['headers']['viewer']).status_code==404


def test_csv_export_restricted_and_escaped(env,sample):
    assert env['client'].get('/api/v1/reports/monthly/export.csv?billing_month='+sample['month'],headers=env['headers']['viewer']).status_code==403
    from app.db import Company
    with env['factory']() as db:db.get(Company,sample['company_id']).name='=FORMULA()';db.commit()
    result=env['client'].get('/api/v1/reports/monthly/export.csv?billing_month='+sample['month'],headers=env['headers']['admin'])
    assert result.status_code==200 and "'=FORMULA()" in result.text
