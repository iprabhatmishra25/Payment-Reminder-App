import csv
import io
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo
import pyotp
from fastapi import FastAPI, Depends, HTTPException, Request, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import select, func, update, text
from sqlalchemy.exc import IntegrityError
from .config import config
from .db import SessionLocal, User, AuthSession, AuthToken, Company, Invoice, Payment, Policy, Reminder, ReminderRun, ReminderItem, Outbox, Audit, WebhookEvent, RateLimit, now, utc
from .schemas import Login, MFA, CompanyInput, CompanyUpdate, InvoiceInput, PaymentInput, Reason, RunInput, PolicyInput, UserInput, Reset, Strict
from .security import authenticated, current, writer, admin, passwords, dummy_hash, verify_password, digest, new_session, user_view, audit, rate_limit, encrypt, decrypt, check_totp
from .services import balance, money, invoice_view, record_payment, preview_reminders, queue_run, monthly_report
from pydantic import EmailStr, Field
from typing import Literal


@asynccontextmanager
async def lifespan(app):
    if config.environment == 'production':
        if config.demo or config.database_url.startswith('sqlite') or not config.encryption_key:
            raise RuntimeError('Production requires PostgreSQL, ENCRYPTION_KEY, and DEMO_MODE=false')
    if config.email_mode not in ('preview', 'resend'):
        raise RuntimeError('EMAIL_MODE must be preview or resend')
    if config.email_mode == 'resend' and (not config.email_from or not config.email_api_key):
        raise RuntimeError('Live delivery requires EMAIL_FROM and EMAIL_API_KEY')
    with SessionLocal() as db:
        db.execute(text('SELECT 1'))
        if config.environment == 'production' and db.scalar(select(Audit.id).where(Audit.action == 'demo.seeded').limit(1)):
            raise RuntimeError('A demo database cannot be promoted to production. Use a clean database.')
        if not db.get(Policy, 1):
            db.add(Policy(id=1))
            db.commit()
    yield


app = FastAPI(title='Duewise Payment Reminder API', version='0.1.0', lifespan=lifespan, docs_url='/docs' if config.environment != 'production' else None, openapi_url='/openapi.json' if config.environment != 'production' else None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'], allow_methods=['GET','POST','PATCH','PUT','DELETE'], allow_headers=['Authorization','Content-Type','Idempotency-Key'])


@app.middleware('http')
async def security_headers(request, call_next):
    request_id = secrets.token_hex(8)
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store', 'X-Request-ID': request_id})
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(status_code=409, content={'detail': 'This record conflicts with an existing record. Check the code, invoice number, or request key.'})


def database():
    with SessionLocal() as db:
        yield db


DB = Annotated[object, Depends(database)]
MONTH = r'^\d{4}-(0[1-9]|1[0-2])$'


def client_key(request):
    return request.client.host if request.client else 'unknown'


@app.get('/health/live')
def live():
    return {'status': 'ok', 'demo': config.demo, 'version': '0.1.0'}


@app.get('/health/ready')
def ready(db: DB):
    db.execute(text('SELECT 1'))
    return {'status': 'ok'}


@app.post('/api/v1/auth/login')
def login(data: Login, request: Request, db: DB):
    rate_limit('login-ip:' + client_key(request), 30)
    rate_limit('login-email:' + data.email.lower(), 10)
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    valid = verify_password(data.password, user.password_hash if user else dummy_hash)
    if not user or not valid or not user.active:
        raise HTTPException(401, 'Email or password is incorrect')
    if user.mfa_secret:
        raw = secrets.token_urlsafe(32)
        db.add(AuthToken(user_id=user.id, token_hash=digest(raw), purpose='mfa', expires_at=now() + timedelta(minutes=5)))
        db.commit()
        return {'mfa_required': True, 'challenge': raw}
    audit(db, user.id, 'auth.login', user.id)
    return new_session(db, user)


@app.post('/api/v1/auth/mfa/verify')
def mfa_verify(data: MFA, request: Request, db: DB):
    rate_limit('mfa:' + client_key(request), 15)
    challenge = db.scalar(select(AuthToken).where(AuthToken.token_hash == digest(data.challenge)).with_for_update())
    if not challenge or challenge.used or challenge.purpose != 'mfa' or challenge.attempts >= 5 or utc(challenge.expires_at) <= now():
        raise HTTPException(401, 'MFA challenge expired. Sign in again.')
    user = db.scalar(select(User).where(User.id == challenge.user_id).with_for_update())
    challenge.attempts += 1
    if not user.active or not user.mfa_secret or not check_totp(user, data.code):
        db.commit()
        raise HTTPException(401, 'Invalid or already used authenticator code')
    challenge.used = True
    audit(db, user.id, 'auth.mfa_verified', user.id)
    return new_session(db, user, True)


class RefreshInput(Strict):
    refresh_token: str = Field(min_length=20, max_length=200)


@app.post('/api/v1/auth/refresh')
def refresh(data: RefreshInput, db: DB):
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == digest(data.refresh_token)).with_for_update())
    if not session or session.revoked or utc(session.expires_at) <= now() or utc(session.last_seen) < now() - timedelta(minutes=30):
        raise HTTPException(401, 'Session expired')
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, 'Account unavailable')
    access, refresh_token = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    session.access_hash, session.refresh_hash = digest(access), digest(refresh_token)
    session.access_expires, session.last_seen = now() + timedelta(minutes=15), now()
    db.commit()
    return {'access_token': access, 'refresh_token': refresh_token, 'user': user_view(user)}


@app.get('/api/v1/auth/me')
def me(auth=Depends(authenticated)):
    return user_view(auth[0])


@app.post('/api/v1/auth/logout')
def logout(db: DB, auth=Depends(authenticated)):
    db.execute(update(AuthSession).where(AuthSession.id == auth[1].id).values(revoked=True))
    db.commit()
    return {'ok': True}


class PasswordInput(Strict):
    password: str = Field(max_length=256)


@app.post('/api/v1/auth/mfa/setup')
def mfa_setup(data: PasswordInput, db: DB, auth=Depends(authenticated)):
    user = db.get(User, auth[0].id)
    rate_limit('mfa-setup:' + user.id, 5)
    if user.mfa_secret:
        raise HTTPException(409, 'MFA is already enabled')
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(401, 'Password is incorrect')
    raw = pyotp.random_base32()
    user.mfa_pending = encrypt(raw)
    db.commit()
    return {'secret': raw, 'uri': pyotp.TOTP(raw).provisioning_uri(user.email, issuer_name='Duewise')}


class CodeInput(Strict):
    code: str = Field(pattern=r'^\d{6}$')


@app.post('/api/v1/auth/mfa/confirm')
def mfa_confirm(data: CodeInput, db: DB, auth=Depends(authenticated)):
    rate_limit('mfa-confirm:' + auth[0].id, 5)
    user = db.get(User, auth[0].id)
    if not user.mfa_pending or not pyotp.TOTP(decrypt(user.mfa_pending)).verify(data.code, valid_window=1):
        raise HTTPException(422, 'Invalid authenticator code')
    user.mfa_secret, user.mfa_pending = user.mfa_pending, None
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != auth[1].id).values(revoked=True))
    db.execute(update(AuthSession).where(AuthSession.id == auth[1].id).values(mfa_verified=True))
    audit(db, user.id, 'auth.mfa_enabled', user.id)
    db.commit()
    return {'ok': True}


class EmailInput(Strict):
    email: EmailStr


@app.post('/api/v1/auth/forgot-password')
def forgot(data: EmailInput, request: Request, db: DB):
    rate_limit('reset-ip:' + client_key(request), 5)
    rate_limit('reset-email:' + data.email.lower(), 3)
    user = db.scalar(select(User).where(User.email == data.email.lower(), User.active.is_(True)))
    if user and config.encryption_key and config.email_mode == 'resend':
        raw = secrets.token_urlsafe(40)
        db.add(AuthToken(user_id=user.id, token_hash=digest(raw), purpose='reset', expires_at=now() + timedelta(minutes=20)))
        db.add(Outbox(kind='password_reset', aggregate_id=user.id, payload={'encrypted_token': encrypt(raw)}))
        db.commit()
    return {'message': 'If an active account exists and recovery delivery is configured, recovery instructions will be sent.'}


@app.post('/api/v1/auth/reset-password')
def reset(data: Reset, request: Request, db: DB):
    rate_limit('reset-consume:' + client_key(request), 10)
    token = db.scalar(select(AuthToken).where(AuthToken.token_hash == digest(data.token), AuthToken.purpose == 'reset').with_for_update())
    if not token or token.used or utc(token.expires_at) <= now():
        raise HTTPException(422, 'Recovery token is invalid or expired')
    user = db.get(User, token.user_id)
    user.password_hash, token.used = passwords.hash(data.password), True
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(revoked=True))
    db.execute(update(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == 'reset').values(used=True))
    audit(db, user.id, 'auth.password_reset', user.id)
    db.commit()
    return {'ok': True}


@app.get('/api/v1/auth/sessions')
def sessions(db: DB, auth=Depends(authenticated)):
    return [{'id': s.id, 'current': s.id == auth[1].id, 'created_at': s.created_at, 'last_seen': s.last_seen} for s in db.scalars(select(AuthSession).where(AuthSession.user_id == auth[0].id, AuthSession.revoked.is_(False), AuthSession.expires_at > now()))]


@app.delete('/api/v1/auth/sessions/{session_id}')
def revoke_session(session_id: str, db: DB, auth=Depends(authenticated)):
    result = db.execute(update(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == auth[0].id).values(revoked=True))
    if not result.rowcount:
        raise HTTPException(404, 'Session not found')
    db.commit()
    return {'ok': True}


@app.get('/api/v1/meta')
def metadata(db: DB, user=Depends(current)):
    latest = db.scalar(select(func.max(Invoice.billing_month)))
    return {'demo': config.demo, 'email_mode': config.email_mode, 'default_month': latest or now().strftime('%Y-%m'), 'timezone': db.get(Policy, 1).timezone}


@app.get('/api/v1/companies')
def companies(db: DB, q: str = '', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current)):
    query = select(Company).where(Company.name.ilike('%' + q[:160] + '%') | Company.code.ilike('%' + q[:160] + '%'))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Company.name).offset((page-1)*page_size).limit(page_size)).all()
    return {'items': [{'id': c.id, 'name': c.name, 'code': c.code, 'email': c.email, 'contact_name': c.contact_name, 'industry': c.industry, 'active': c.active, 'paused': c.paused} for c in rows], 'total': total}


@app.post('/api/v1/companies', status_code=201)
def create_company(data: CompanyInput, db: DB, user=Depends(writer)):
    company = Company(**data.model_dump())
    db.add(company)
    db.flush()
    audit(db, user.id, 'company.created', company.id, name=company.name)
    db.commit()
    return {'id': company.id}


@app.patch('/api/v1/companies/{company_id}')
def update_company(company_id: str, data: CompanyUpdate, db: DB, user=Depends(writer)):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404, 'Company not found')
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(company, key, value)
    audit(db, user.id, 'company.updated', company.id, fields=list(data.model_fields_set))
    db.commit()
    return {'ok': True}


@app.get('/api/v1/invoices')
def invoices(db: DB, q: str = '', month: str | None = Query(None, pattern=MONTH), company_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current)):
    query = select(Invoice, Company).join(Company, Company.id == Invoice.company_id).where(Invoice.number.ilike('%' + q[:100] + '%') | Company.name.ilike('%' + q[:100] + '%'))
    if month:
        query = query.where(Invoice.billing_month == month)
    if company_id:
        query = query.where(Invoice.company_id == company_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Invoice.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {'items': [invoice_view(db, invoice, company) for invoice, company in rows], 'total': total}


@app.post('/api/v1/invoices', status_code=201)
def create_invoice(data: InvoiceInput, db: DB, user=Depends(writer)):
    company = db.get(Company, data.company_id)
    if not company or not company.active:
        raise HTTPException(409, 'Choose an active company')
    invoice = Invoice(**data.model_dump())
    db.add(invoice)
    db.flush()
    audit(db, user.id, 'invoice.created', invoice.id, number=invoice.number, amount=money(invoice.amount))
    db.commit()
    return invoice_view(db, invoice, company)


@app.post('/api/v1/invoices/{invoice_id}/post')
def post_invoice(invoice_id: str, db: DB, user=Depends(writer)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice or invoice.lifecycle != 'DRAFT':
        raise HTTPException(409, 'Only draft invoices can be posted')
    invoice.lifecycle, invoice.posted_at = 'POSTED', now()
    audit(db, user.id, 'invoice.posted', invoice.id)
    db.commit()
    return invoice_view(db, invoice)


@app.post('/api/v1/invoices/{invoice_id}/void')
def void_invoice(invoice_id: str, data: Reason, db: DB, user=Depends(admin)):
    db.execute(update(Invoice).where(Invoice.id == invoice_id).values(version=Invoice.version+1))
    invoice = db.get(Invoice, invoice_id)
    if not invoice or invoice.lifecycle == 'VOID':
        raise HTTPException(409, 'Invoice is unavailable or already void')
    if balance(db, invoice) != invoice.amount:
        raise HTTPException(409, 'Reverse active payments before voiding an invoice')
    invoice.lifecycle, invoice.voided_at = 'VOID', now()
    audit(db, user.id, 'invoice.voided', invoice.id, reason=data.reason)
    db.commit()
    return {'ok': True}


@app.post('/api/v1/invoices/{invoice_id}/payments')
def create_payment(invoice_id: str, data: PaymentInput, db: DB, idempotency_key: Annotated[str | None, Header()] = None, user=Depends(writer)):
    payment, reused = record_payment(db, invoice_id, data, idempotency_key, user.id)
    return JSONResponse(status_code=200 if reused else 201, content={'id': payment.id, 'reused': reused, 'invoice': invoice_view(db, db.get(Invoice, invoice_id))})


@app.get('/api/v1/payments')
def payments(db: DB, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current)):
    total = db.scalar(select(func.count()).select_from(Payment))
    rows = db.execute(select(Payment, Invoice, Company).join(Invoice, Invoice.id == Payment.invoice_id).join(Company, Company.id == Invoice.company_id).order_by(Payment.recorded_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {'items': [{'id': p.id, 'invoice_number': i.number, 'company_name': c.name, 'amount': money(p.amount), 'paid_at': utc(p.paid_at).isoformat(), 'method': p.method, 'reference': p.reference, 'reversed': bool(p.reversed_at)} for p, i, c in rows], 'total': total}


@app.post('/api/v1/payments/{payment_id}/reverse')
def reverse_payment(payment_id: str, data: Reason, db: DB, user=Depends(admin)):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, 'Payment not found')
    db.execute(update(Invoice).where(Invoice.id == payment.invoice_id).values(version=Invoice.version+1))
    db.refresh(payment)
    if payment.reversed_at:
        raise HTTPException(409, 'Payment is already reversed')
    payment.reversed_at, payment.reversal_reason = now(), data.reason
    audit(db, user.id, 'payment.reversed', payment.id, reason=data.reason)
    db.commit()
    return {'ok': True}


@app.get('/api/v1/reminder-policy')
def get_policy(db: DB, user=Depends(current)):
    policy = db.get(Policy, 1)
    return {**{field: getattr(policy, field) for field in PolicyInput.model_fields}, 'email_mode': config.email_mode}


@app.put('/api/v1/reminder-policy')
def put_policy(data: PolicyInput, db: DB, user=Depends(admin)):
    policy = db.get(Policy, 1)
    for key, value in data.model_dump().items():
        setattr(policy, key, value)
    audit(db, user.id, 'reminder_policy.updated', '1')
    db.commit()
    return {'ok': True}


@app.post('/api/v1/reminder-runs/preview')
def preview(data: RunInput, db: DB, user=Depends(writer)):
    return {**preview_reminders(db, data.company_ids), 'email_mode': config.email_mode}


@app.post('/api/v1/reminder-runs', status_code=202)
def run_reminders(data: RunInput, db: DB, user=Depends(writer)):
    rate_limit('send:' + user.id, 10, 60)
    return queue_run(db, user.id, data.company_ids)


@app.get('/api/v1/reminders')
def reminders(db: DB, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current)):
    total = db.scalar(select(func.count()).select_from(Reminder))
    rows = db.execute(select(Reminder, Company).join(Company, Company.id == Reminder.company_id).order_by(Reminder.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {'items': [{'id': r.id, 'company_name': c.name, 'recipient': r.recipient, 'status': r.status, 'created_at': utc(r.created_at).isoformat(), 'accepted_at': utc(r.accepted_at).isoformat() if r.accepted_at else None, 'attempts': r.attempts, 'error': r.error} for r, c in rows], 'total': total}


@app.get('/api/v1/reminder-runs/{run_id}')
def run_status(run_id: str, db: DB, user=Depends(current)):
    run = db.get(ReminderRun, run_id)
    if not run:
        raise HTTPException(404, 'Reminder run not found')
    counts = dict(db.execute(select(Reminder.status,func.count()).where(Reminder.run_id == run.id).group_by(Reminder.status)).all())
    return {'id': run.id, 'month': run.month, 'trigger': run.trigger, 'counts': counts, 'total': sum(counts.values())}


@app.get('/api/v1/operations/status')
def operations_status(db: DB, user=Depends(current)):
    heartbeat = db.get(RateLimit, 'worker-heartbeat')
    return {'email_mode': config.email_mode, 'pending_jobs': db.scalar(select(func.count()).select_from(Outbox).where(Outbox.done.is_(False))), 'failed_reminders': db.scalar(select(func.count()).select_from(Reminder).where(Reminder.status.in_(['FAILED','UNKNOWN','BOUNCED']))), 'last_worker_heartbeat': utc(heartbeat.started_at).isoformat() if heartbeat else None, 'worker_healthy': bool(heartbeat and utc(heartbeat.started_at) > now()-timedelta(minutes=5))}


@app.post('/api/v1/webhooks/email')
async def email_webhook(request: Request, db: DB):
    from .webhooks import verify, delivery_status
    if request.headers.get('content-length','0').isdecimal() and int(request.headers.get('content-length','0'))>100000:
        raise HTTPException(413,'Webhook too large')
    body=await request.body()
    if len(body)>100000:
        raise HTTPException(413,'Webhook too large')
    event_id,data=verify(body,request.headers,config.email_webhook_secret)
    if db.get(WebhookEvent,event_id):
        return {'ok':True,'duplicate':True}
    provider_id=data.get('data',{}).get('email_id')
    event_type=data.get('type','')
    if not isinstance(provider_id,str) or len(provider_id)>150 or event_type not in ('email.delivered','email.bounced','email.complained','email.sent','email.delivery_delayed'):
        return {'ok':True,'ignored':True}
    try:
        with db.begin_nested():
            db.add(WebhookEvent(id=event_id,provider_id=provider_id,event_type=event_type))
            db.flush()
    except IntegrityError:
        return {'ok':True,'duplicate':True}
    reminder=db.scalar(select(Reminder).where(Reminder.provider_id==provider_id).with_for_update())
    if reminder:
        types=list(db.scalars(select(WebhookEvent.event_type).where(WebhookEvent.provider_id==provider_id)))
        reminder.status=delivery_status(reminder.status,types)
    db.commit()
    return {'ok':True}


@app.post('/api/v1/reminders/{reminder_id}/retry')
def retry_reminder(reminder_id: str, db: DB, user=Depends(writer)):
    reminder = db.scalar(select(Reminder).where(Reminder.id == reminder_id).with_for_update())
    if not reminder or reminder.status not in ('FAILED', 'PREVIEW') or reminder.accepted_at:
        raise HTTPException(409, 'Only failed or preview reminders can be retried; uncertain sends require review')
    if reminder.attempts >= 5:
        raise HTTPException(409, 'Retry limit reached. Review delivery configuration.')
    company = db.get(Company, reminder.company_id)
    policy = db.get(Policy, 1)
    # An explicitly reviewed retry is a new provider request with current configuration.
    db.execute(update(Outbox).where(Outbox.aggregate_id == reminder.id, Outbox.kind == 'reminder').values(done=True))
    reminder.recipient, reminder.status, reminder.error = company.email, 'QUEUED', None
    db.add(Outbox(kind='reminder', aggregate_id=reminder.id, payload={'subject': policy.subject, 'message': policy.message}))
    audit(db, user.id, 'reminder.retried', reminder.id)
    db.commit()
    return {'ok': True}


class ResolveDelivery(Strict):
    outcome: Literal['SENT','NOT_SENT']
    reason: str = Field(min_length=10,max_length=500)
    accepted_at: datetime | None = None
    provider_id: str | None = Field(default=None,max_length=150)


@app.post('/api/v1/reminders/{reminder_id}/resolve')
def resolve_delivery(reminder_id: str,data: ResolveDelivery,db: DB,user=Depends(admin)):
    reminder=db.scalar(select(Reminder).where(Reminder.id==reminder_id).with_for_update())
    if not reminder or reminder.status!='UNKNOWN':
        raise HTTPException(409,'Only uncertain deliveries need this review')
    if data.outcome=='SENT':
        if not data.accepted_at or data.accepted_at.tzinfo is None or data.accepted_at>now():
            raise HTTPException(422,'Provide the actual provider acceptance time with timezone, no later than now')
        reminder.status,reminder.accepted_at,reminder.provider_id='SENT',data.accepted_at,data.provider_id
    else:
        reminder.status='FAILED'
    reminder.error='Manually reviewed: '+data.reason
    db.execute(update(Outbox).where(Outbox.aggregate_id==reminder.id).values(done=True))
    audit(db,user.id,'reminder.resolved',reminder.id,outcome=data.outcome,reason=data.reason)
    db.commit()
    return {'ok':True}


@app.get('/api/v1/reports/monthly')
def report(db: DB, billing_month: str = Query(pattern=MONTH), as_of: datetime | None = None, outcome: str | None = None, q: str = '', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user=Depends(current)):
    if as_of and (as_of.tzinfo is None or as_of > now() + timedelta(seconds=30)):
        raise HTTPException(422, 'Use a timezone-aware cutoff no later than now')
    return monthly_report(db, billing_month, as_of, outcome, q, page, page_size)


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


@app.get('/api/v1/reports/monthly/export.csv')
def export(db: DB, billing_month: str = Query(pattern=MONTH), user=Depends(writer)):
    cutoff = now()
    def rows():
        stream = io.StringIO()
        csv_writer = csv.writer(stream)
        csv_writer.writerow(['Company','Code','Billed INR','Paid INR','Outstanding INR','Outcome','Status as of'])
        yield stream.getvalue()
        page = 1
        while True:
            result = monthly_report(db, billing_month, cutoff, page=page, page_size=200)
            for row in result['companies']:
                stream.seek(0)
                stream.truncate(0)
                csv_writer.writerow([csv_safe(row[k]) for k in ('name','code','billed','paid','outstanding','outcome')] + [cutoff.isoformat()])
                yield stream.getvalue()
            if page*200 >= result['total']:
                break
            page += 1
    return StreamingResponse(rows(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="duewise-{billing_month}.csv"'})


@app.get('/api/v1/reports/trend')
def trend(db: DB, user=Depends(current)):
    policy = db.get(Policy, 1)
    zone = ZoneInfo(policy.timezone)
    current_date = now().astimezone(zone)
    result = []
    for offset in range(5, -1, -1):
        month_index = current_date.year * 12 + current_date.month - 1 - offset
        year, index = divmod(month_index, 12)
        start = datetime(year, index+1, 1, tzinfo=zone)
        next_year, next_index = divmod(month_index+1, 12)
        end = datetime(next_year, next_index+1, 1, tzinfo=zone)
        amount = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.paid_at >= start, Payment.paid_at < end, Payment.reversed_at.is_(None)))
        result.append({'month': start.strftime('%b'), 'amount': money(amount)})
    return result


@app.get('/api/v1/audit-logs')
def audit_logs(db: DB, page: int = Query(1, ge=1), user=Depends(admin)):
    logs = db.scalars(select(Audit).order_by(Audit.created_at.desc()).offset((page-1)*50).limit(50)).all()
    return [{'id': a.id, 'action': a.action, 'entity_id': a.entity_id, 'details': a.details, 'created_at': utc(a.created_at).isoformat()} for a in logs]


@app.get('/api/v1/users')
def users(db: DB, user=Depends(admin)):
    return [{**user_view(u), 'active': u.active} for u in db.scalars(select(User).order_by(User.name))]


@app.post('/api/v1/users', status_code=201)
def create_user(data: UserInput, db: DB, user=Depends(admin)):
    created = User(name=data.name, email=data.email.lower(), role=data.role, password_hash=passwords.hash(data.password))
    db.add(created)
    db.flush()
    audit(db, user.id, 'user.created', created.id, role=data.role)
    db.commit()
    return user_view(created)


class UserUpdate(Strict):
    role: str | None = Field(None, pattern=r'^(admin|accounts|viewer)$')
    active: bool | None = None


@app.patch('/api/v1/users/{user_id}')
def update_user(user_id: str, data: UserUpdate, db: DB, user=Depends(admin)):
    if user.id == user_id:
        raise HTTPException(409, 'Ask another administrator to change your own role or active state')
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, 'User not found')
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(target, key, value)
    db.execute(update(AuthSession).where(AuthSession.user_id == target.id).values(revoked=True))
    audit(db, user.id, 'user.updated', target.id)
    db.commit()
    return {'ok': True}
