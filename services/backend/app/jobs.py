"""Durable reminder processing. Preview mode never credits a successful send."""
import logging
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import httpx
from celery import Celery
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from .config import config
from .db import SessionLocal, Outbox, Reminder, ReminderItem, Invoice, Company, Policy, DeliveryAttempt, User, ReminderRun, AuthToken, WebhookEvent, now, utc
from .security import audit, decrypt
from .services import balance, money, queue_run, scheduled_due

logger = logging.getLogger(__name__)
celery = Celery('duewise', broker=config.redis_url)
celery.conf.update(timezone='UTC', task_serializer='json', accept_content=['json'], result_serializer='json', broker_connection_retry_on_startup=True, worker_prefetch_multiplier=1, task_ignore_result=True, beat_schedule={'scan-each-minute': {'task': 'duewise.tick', 'schedule': 60.0}})
celery.conf.task_annotations = {'duewise.process': {'rate_limit': '30/m'}}


class DeliveryError(Exception):
    def __init__(self, message, retry=False, uncertain=False):
        self.retry, self.uncertain = retry, uncertain
        super().__init__(message)


def send_email(body, key):
    if config.email_mode != 'resend':
        return None
    if config.demo:
        raise DeliveryError('Live delivery is disabled while DEMO_MODE=true')
    allowed = {value.strip().lower() for value in config.allowed_recipients.split(',') if value.strip()}
    if allowed and any(value.lower() not in allowed for value in body['to']):
        raise DeliveryError('Recipient is outside the configured delivery allowlist')
    try:
        response = httpx.post('https://api.resend.com/emails', json=body, headers={'Authorization': f'Bearer {config.email_api_key}', 'Idempotency-Key': key}, timeout=20)
    except httpx.TransportError:
        raise DeliveryError('Provider response unknown; review provider history before retry', uncertain=True)
    if response.status_code == 429:
        raise DeliveryError('Provider rate limit reached', retry=True)
    if response.status_code >= 500:
        raise DeliveryError('Provider response uncertain; review delivery before retry', uncertain=True)
    if not response.is_success:
        raise DeliveryError(f'Provider rejected request ({response.status_code}); review sender configuration and recipient')
    return response.json()['id']


def process_one(event_id):
    with SessionLocal() as db:
        claimed = db.execute(update(Outbox).where(Outbox.id == event_id, Outbox.done.is_(False), Outbox.available_at <= now()).values(available_at=now() + timedelta(minutes=3)))
        if not claimed.rowcount:
            db.rollback()
            return
        db.commit()
        event = db.get(Outbox, event_id)
        reminder = db.get(Reminder, event.aggregate_id) if event.kind == 'reminder' else None
        if reminder and reminder.status == 'SENDING':
            # A previous worker disappeared during external I/O. Never blindly resend.
            reminder.status, reminder.error, event.done = 'UNKNOWN', 'Worker stopped during send. Check provider history before resending.', True
            db.commit()
            return
        if reminder and (reminder.accepted_at or reminder.status in ('SENT', 'DELIVERED', 'BOUNCED', 'CANCELLED', 'UNKNOWN')):
            event.done = True
            db.commit()
            return
        if reminder:
            company = db.get(Company, reminder.company_id)
            items = db.execute(select(ReminderItem, Invoice).join(Invoice, Invoice.id == ReminderItem.invoice_id).where(ReminderItem.reminder_id == reminder.id)).all()
            eligible = [(item, invoice, balance(db, invoice)) for item, invoice in items if invoice.lifecycle == 'POSTED' and not invoice.hold]
            eligible = [row for row in eligible if row[2] > 0]
            if not eligible or company.paused:
                reminder.status, event.done = 'CANCELLED', True
                db.commit()
                return
            if company.email != reminder.recipient:
                reminder.status, reminder.error, event.done = 'FAILED', 'Billing contact changed since preview. Review the recipient before delivery.', True
                db.commit()
                return
            body = event.payload.get('prepared_body')
            if not body:
                lines = [f'Hello {company.contact_name or company.name},', '', event.payload['message'], '']
                for item, invoice, outstanding in eligible:
                    item.balance_snapshot = outstanding
                    lines.append(f'{invoice.number} | Due {invoice.due_date} | INR {money(outstanding)}')
                lines += ['', f'Total outstanding: INR {money(sum((r[2] for r in eligible), Decimal(0)))}', '', 'Accounts team']
                body = {'from': config.email_from, 'to': [reminder.recipient], 'subject': event.payload['subject'], 'text': '\n'.join(lines)}
                event.payload = {**event.payload, 'prepared_body': body, 'prepared_at': now().isoformat()}
            elif utc(now()) - datetime_from_iso(event.payload['prepared_at']) > timedelta(hours=23):
                reminder.status, reminder.error, event.done = 'UNKNOWN', 'Prepared request is older than the safe provider retry window. Review manually.', True
                db.commit()
                return
            # Keep only invoice links actually included in the final message.
            if not event.attempts:
                included = {row[1].id for row in eligible}
                for item, invoice in items:
                    if invoice.id not in included:
                        db.delete(item)
            reminder.status, reminder.attempts = 'SENDING', reminder.attempts + 1
        elif event.kind == 'password_reset':
            user = db.get(User, event.aggregate_id)
            body = {'from': config.email_from, 'to': [user.email], 'subject': 'Reset your Duewise password', 'text': 'Paste this recovery token into Duewise within 20 minutes:\n\n' + decrypt(event.payload['encrypted_token']) + '\n\nIf you did not request this, ignore this message.'}
        else:
            event.done = True
            db.commit()
            return
        event.attempts += 1
        db.commit()  # Persist the send claim and immutable body before external I/O.
        try:
            provider_id = send_email(body, 'duewise/' + event.id)
            if reminder:
                reminder.status = 'SENT' if provider_id else 'PREVIEW'
                reminder.provider_id, reminder.error = provider_id, None
                if provider_id:
                    reminder.accepted_at = now()
                    from .webhooks import delivery_status
                    event_types=list(db.scalars(select(WebhookEvent.event_type).where(WebhookEvent.provider_id==provider_id)))
                    reminder.status=delivery_status(reminder.status,event_types)
                db.add(DeliveryAttempt(reminder_id=reminder.id, number=reminder.attempts, status=reminder.status))
                audit(db, None, 'reminder.' + reminder.status.lower(), reminder.id)
            event.done = True
            if event.kind == 'password_reset':
                event.payload = {}  # Remove encrypted recovery material after delivery.
        except DeliveryError as exc:
            if reminder:
                reminder.status = 'UNKNOWN' if exc.uncertain else 'QUEUED' if exc.retry and event.attempts < 5 else 'FAILED'
                reminder.error = str(exc)
                db.add(DeliveryAttempt(reminder_id=reminder.id, number=reminder.attempts, status=reminder.status))
            event.done = not exc.retry or event.attempts >= 5
            event.available_at = now() + timedelta(seconds=min(60 * 2**event.attempts, 1800))
            if event.kind == 'password_reset' and event.done:
                event.payload = {}
            logger.warning('Email outcome event_id=%s status=%s', event.id, reminder.status if reminder else 'recovery-failed')
        db.commit()


def datetime_from_iso(value):
    from datetime import datetime
    return utc(datetime.fromisoformat(value))


@celery.task(name='duewise.process')
def process(event_id):
    process_one(event_id)


@celery.task(name='duewise.tick')
def tick():
    with SessionLocal() as db:
        scan_schedule(db)
        pending = list(db.scalars(select(Outbox.id).where(Outbox.done.is_(False), Outbox.available_at <= now()).order_by(Outbox.available_at).limit(200)))
        # A heartbeat is visible from the monitoring endpoint; no contact data is logged.
        from .db import RateLimit
        heartbeat = db.get(RateLimit, 'worker-heartbeat')
        if heartbeat:
            heartbeat.started_at = now()
        else:
            db.add(RateLimit(key='worker-heartbeat', count=0))
        db.commit()
    for event_id in pending:
        process.delay(event_id)


def scan_schedule(db):
    policy = db.get(Policy, 1)
    if policy and scheduled_due(policy):
        month = now().astimezone(ZoneInfo(policy.timezone)).strftime('%Y-%m')
        key = 'scheduled:' + month
        if not db.scalar(select(ReminderRun).where(ReminderRun.request_key == key)):
            try:
                queue_run(db, trigger='scheduled', request_key=key)
            except IntegrityError:
                db.rollback()


def drain_preview():
    if config.email_mode != 'preview':
        raise RuntimeError('The local drain command is allowed only with EMAIL_MODE=preview')
    with SessionLocal() as db:
        pending = list(db.scalars(select(Outbox.id).where(Outbox.done.is_(False), Outbox.available_at <= now())))
    for event_id in pending:
        process_one(event_id)
    return len(pending)
