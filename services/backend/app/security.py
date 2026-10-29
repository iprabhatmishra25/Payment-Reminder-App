import hashlib
import secrets
import time
from datetime import timedelta
from typing import Annotated
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .config import config
from .db import SessionLocal, User, AuthSession, AuthToken, RateLimit, Audit, now, utc

passwords = PasswordHasher()
dummy_hash = passwords.hash('a-dummy-password-for-constant-work')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cipher():
    if not config.encryption_key:
        raise HTTPException(503, 'Set ENCRYPTION_KEY before using MFA or recovery email')
    return Fernet(config.encryption_key.encode())


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode()


def verify_password(raw, hashed):
    try:
        return passwords.verify(hashed, raw)
    except VerificationError:
        return False


def audit(db, user_id, action, entity_id, **details):
    db.add(Audit(user_id=user_id, action=action, entity_id=str(entity_id), details=details))


def rate_limit(key, maximum=10, seconds=900):
    raw_key = key
    key = digest(key)
    with SessionLocal() as db:
        # Conditional increment is atomic on PostgreSQL and SQLite; windows are fixed.
        cutoff = now() - timedelta(seconds=seconds)
        db.execute(update(RateLimit).where(RateLimit.key == key, RateLimit.started_at < cutoff).values(count=0, started_at=now()))
        row = db.execute(update(RateLimit).where(RateLimit.key == key, RateLimit.count < maximum).values(count=RateLimit.count + 1))
        if row.rowcount:
            db.commit()
            return
        if db.get(RateLimit, key):
            db.rollback()
            raise HTTPException(429, 'Too many attempts. Please try again later.')
        try:
            db.add(RateLimit(key=key, count=1))
            db.commit()
        except IntegrityError:
            db.rollback()
            return rate_limit(raw_key, maximum, seconds)


def new_session(db, user, verified=False):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    session = AuthSession(user_id=user.id, access_hash=digest(access), refresh_hash=digest(refresh), access_expires=now() + timedelta(minutes=15), expires_at=now() + timedelta(hours=12), mfa_verified=verified)
    db.add(session)
    db.commit()
    return {'access_token': access, 'refresh_token': refresh, 'expires_in': 900, 'user': user_view(user), 'mfa_setup_required': config.environment == 'production' and user.role != 'viewer' and not user.mfa_secret}


def user_view(user):
    return {'id': user.id, 'name': user.name, 'email': user.email, 'role': user.role, 'mfa_enabled': bool(user.mfa_secret)}


def authenticated(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Please sign in')
    with SessionLocal() as db:
        session = db.scalar(select(AuthSession).where(AuthSession.access_hash == digest(authorization[7:])))
        if not session or session.revoked or utc(session.access_expires) <= now() or utc(session.expires_at) <= now() or utc(session.last_seen) < now() - timedelta(minutes=30):
            raise HTTPException(401, 'Your session has expired. Please sign in again.')
        user = db.get(User, session.user_id)
        if not user or not user.active:
            raise HTTPException(401, 'Account is unavailable')
        session.last_seen = now()
        db.commit()
        return user, session


def current(auth=Depends(authenticated)):
    user, session = auth
    if config.environment == 'production' and user.role != 'viewer' and (not user.mfa_secret or not session.mfa_verified):
        raise HTTPException(403, 'Complete MFA setup before accessing payment data')
    return user


def writer(user=Depends(current)):
    if user.role not in ('admin', 'accounts'):
        raise HTTPException(403, 'Accounts or administrator permission is required')
    return user


def admin(user=Depends(current)):
    if user.role != 'admin':
        raise HTTPException(403, 'Administrator permission is required')
    return user


def check_totp(user, code):
    totp = pyotp.TOTP(decrypt(user.mfa_secret))
    step = int(time.time()) // 30
    for offset in (-1, 0, 1):
        candidate = step + offset
        if candidate > user.last_totp_step and secrets.compare_digest(totp.at(candidate * 30), code):
            user.last_totp_step = candidate
            return True
    return False
