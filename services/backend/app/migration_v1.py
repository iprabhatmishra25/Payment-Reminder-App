from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, event, ForeignKey, String, DateTime, Date, Numeric, Boolean, Integer, Text, JSON, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def uid():
    return str(uuid4())


def initial_posted_at(context):
    values = context.get_current_parameters()
    return None if values.get('lifecycle') == 'DRAFT' else values.get('created_at') or now()


class Base(DeclarativeBase):
    pass




class User(Base):
    __tablename__ = 'app_users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default='accounts')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_pending: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_totp_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("role IN ('admin','accounts','viewer')"),)


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('app_users.id'))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuthToken(Base):
    __tablename__ = 'auth_tokens'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('app_users.id'))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class RateLimit(Base):
    __tablename__ = 'rate_limits'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    count: Mapped[int] = mapped_column(Integer, default=0)


class Company(Base):
    __tablename__ = 'companies'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    contact_name: Mapped[str] = mapped_column(String(120), default='')
    email: Mapped[str] = mapped_column(String(254))
    industry: Mapped[str] = mapped_column(String(80), default='Other')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invoice(Base):
    __tablename__ = 'invoices'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey('companies.id'))
    number: Mapped[str] = mapped_column(String(50), unique=True)
    billing_month: Mapped[str] = mapped_column(String(7), index=True)
    issue_date: Mapped[datetime] = mapped_column(Date)
    due_date: Mapped[datetime] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default='INR')
    lifecycle: Mapped[str] = mapped_column(String(10), default='POSTED')
    hold: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=initial_posted_at, nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint('amount > 0'), CheckConstraint('due_date >= issue_date'), CheckConstraint("currency = 'INR'"), Index('ix_invoice_company_month', 'company_id', 'billing_month'))


class Payment(Base):
    __tablename__ = 'payments'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    invoice_id: Mapped[str] = mapped_column(ForeignKey('invoices.id'), index=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    recorded_by: Mapped[str] = mapped_column(ForeignKey('app_users.id'))
    method: Mapped[str] = mapped_column(String(30))
    reference: Mapped[str] = mapped_column(String(150), default='')
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (CheckConstraint('amount > 0'),)


class Policy(Base):
    __tablename__ = 'reminder_policies'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    day: Mapped[int] = mapped_column(Integer, default=5)
    time: Mapped[str] = mapped_column(String(5), default='10:00')
    timezone: Mapped[str] = mapped_column(String(80), default='Asia/Kolkata')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    catchup_hours: Mapped[int] = mapped_column(Integer, default=48)
    subject: Mapped[str] = mapped_column(String(160), default='A friendly reminder about your outstanding invoices')
    message: Mapped[str] = mapped_column(Text, default='Please arrange payment for the invoices below. If you have recently paid, thank you — please share the payment reference with our accounts team.')
    __table_args__ = (CheckConstraint('day >= 1 AND day <= 31'),)


class ReminderRun(Base):
    __tablename__ = 'reminder_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    month: Mapped[str] = mapped_column(String(7))
    trigger: Mapped[str] = mapped_column(String(15))
    request_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reminder(Base):
    __tablename__ = 'reminders'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey('reminder_runs.id'))
    company_id: Mapped[str] = mapped_column(ForeignKey('companies.id'))
    recipient: Mapped[str] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(20), default='QUEUED', index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(250), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReminderItem(Base):
    __tablename__ = 'reminder_items'
    reminder_id: Mapped[str] = mapped_column(ForeignKey('reminders.id'), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey('invoices.id'), primary_key=True)
    balance_snapshot: Mapped[float] = mapped_column(Numeric(14, 2))
    __table_args__ = (Index('ix_reminder_item_invoice', 'invoice_id'),)


class DeliveryAttempt(Base):
    __tablename__ = 'delivery_attempts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reminder_id: Mapped[str] = mapped_column(ForeignKey('reminders.id'))
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('reminder_id', 'number'),)


class Outbox(Base):
    __tablename__ = 'outbox_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(30))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Audit(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('app_users.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class WebhookEvent(Base):
    __tablename__ = 'email_webhook_events'
    id: Mapped[str] = mapped_column(String(150), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(150), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


metadata = Base.metadata
