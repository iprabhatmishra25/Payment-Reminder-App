import calendar
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, func, or_, update
from sqlalchemy.exc import IntegrityError
from .db import Company, Invoice, Payment, Policy, ReminderRun, Reminder, ReminderItem, Outbox, now, utc
from .security import audit


def money(value):
    return format(Decimal(value or 0).quantize(Decimal('0.01')), '.2f')


def balance(db, invoice, cutoff=None):
    query = select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == invoice.id)
    if cutoff:
        query = query.where(Payment.paid_at <= cutoff, or_(Payment.reversed_at.is_(None), Payment.reversed_at > cutoff))
    else:
        query = query.where(Payment.reversed_at.is_(None))
    return Decimal(invoice.amount) - Decimal(db.scalar(query))


def invoice_view(db, invoice, company=None):
    company = company or db.get(Company, invoice.company_id)
    outstanding = balance(db, invoice)
    status = 'PAID' if outstanding == 0 else 'PARTIALLY_PAID' if outstanding < invoice.amount else 'UNPAID'
    policy = db.get(Policy, 1)
    local_today = now().astimezone(ZoneInfo(policy.timezone if policy else 'Asia/Kolkata')).date()
    return {'id': invoice.id, 'company_id': invoice.company_id, 'company_name': company.name, 'company_code': company.code, 'number': invoice.number, 'billing_month': invoice.billing_month, 'issue_date': str(invoice.issue_date), 'due_date': str(invoice.due_date), 'amount': money(invoice.amount), 'outstanding': money(outstanding), 'paid': money(invoice.amount - outstanding), 'currency': invoice.currency, 'status': status, 'lifecycle': invoice.lifecycle, 'overdue': invoice.lifecycle == 'POSTED' and outstanding > 0 and invoice.due_date < local_today, 'hold': invoice.hold}


def record_payment(db, invoice_id, data, key, user_id):
    if not key or not 8 <= len(key) <= 100:
        raise HTTPException(422, 'Provide an Idempotency-Key with 8–100 characters')
    payload = {'invoice_id': invoice_id, **data.model_dump(mode='json')}
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    previous = db.scalar(select(Payment).where(Payment.idempotency_key == key))
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(409, 'This request key was already used with different payment details')
        return previous, True
    if data.paid_at > now() + timedelta(seconds=30):
        raise HTTPException(422, 'Payment time cannot be in the future')
    # The conditional version update acquires a write lock on both supported databases.
    claimed = db.execute(update(Invoice).where(Invoice.id == invoice_id).values(version=Invoice.version + 1))
    if not claimed.rowcount:
        raise HTTPException(404, 'Invoice not found')
    invoice = db.get(Invoice, invoice_id, populate_existing=True)
    if invoice.lifecycle != 'POSTED':
        raise HTTPException(409, 'Payments require a posted invoice')
    if data.paid_at.date() < invoice.issue_date:
        raise HTTPException(422, 'Payment time cannot precede invoice issue date')
    # Recheck after acquiring the invoice lock in case another request committed meanwhile.
    previous = db.scalar(select(Payment).where(Payment.idempotency_key == key))
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(409, 'Request key conflict')
        return previous, True
    if data.amount > balance(db, invoice):
        raise HTTPException(409, 'Payment exceeds the current outstanding balance')
    payment = Payment(invoice_id=invoice_id, amount=data.amount, paid_at=data.paid_at, recorded_by=user_id, method=data.method, reference=data.reference, idempotency_key=key, request_hash=request_hash)
    db.add(payment)
    db.flush()
    audit(db, user_id, 'payment.recorded', payment.id, amount=money(payment.amount), invoice=invoice.number)
    db.commit()
    return payment, False


def preview_reminders(db, company_ids=None):
    policy = db.get(Policy, 1)
    today = now().astimezone(ZoneInfo(policy.timezone)).date()
    totals = select(Payment.invoice_id.label('invoice_id'), func.sum(Payment.amount).label('paid')).where(Payment.reversed_at.is_(None)).group_by(Payment.invoice_id).subquery()
    query = select(Invoice, Company, (Invoice.amount - func.coalesce(totals.c.paid, 0)).label('outstanding')).join(Company, Company.id == Invoice.company_id).outerjoin(totals, totals.c.invoice_id == Invoice.id).where(Invoice.lifecycle == 'POSTED', Invoice.due_date < today, Invoice.amount > func.coalesce(totals.c.paid, 0))
    if company_ids is not None:
        query = query.where(Company.id.in_(company_ids))
    rows = db.execute(query.order_by(Company.name, Invoice.due_date)).all()
    grouped, skipped = {}, []
    for invoice, company, outstanding in rows:
        if company.paused or invoice.hold or not company.email:
            skipped.append({'company': company.name, 'invoice': invoice.number, 'reason': 'Reminders paused' if company.paused else 'Invoice on hold' if invoice.hold else 'Missing email'})
            continue
        entry = grouped.setdefault(company.id, {'company_id': company.id, 'company_name': company.name, 'recipient': company.email, 'total': Decimal(0), 'invoices': []})
        entry['total'] += outstanding
        entry['invoices'].append({'id': invoice.id, 'number': invoice.number, 'due_date': str(invoice.due_date), 'balance': money(outstanding)})
    month = now().astimezone(ZoneInfo(policy.timezone)).strftime('%Y-%m')
    items = []
    for company_id, item in grouped.items():
        item['total'] = money(item['total'])
        existing = db.scalar(select(Reminder).where(Reminder.dedupe_key == f'monthly:{company_id}:{month}:INR:email'))
        item['already_queued'] = bool(existing)
        items.append(item)
    return {'companies': items, 'skipped': skipped, 'month': month, 'subject': policy.subject, 'message': policy.message}


def queue_run(db, user_id=None, company_ids=None, trigger='manual', request_key=None):
    preview = preview_reminders(db, company_ids)
    run = ReminderRun(month=preview['month'], trigger=trigger, request_key=request_key)
    db.add(run)
    db.flush()
    queued = 0
    for item in preview['companies']:
        if item['already_queued']:
            continue
        reminder = Reminder(run_id=run.id, company_id=item['company_id'], recipient=item['recipient'], dedupe_key=f"monthly:{item['company_id']}:{preview['month']}:INR:email")
        try:
            with db.begin_nested():
                db.add(reminder)
                db.flush()
                for invoice in item['invoices']:
                    db.add(ReminderItem(reminder_id=reminder.id, invoice_id=invoice['id'], balance_snapshot=Decimal(invoice['balance'])))
                db.add(Outbox(kind='reminder', aggregate_id=reminder.id, payload={'subject': preview['subject'], 'message': preview['message']}))
                queued += 1
        except IntegrityError:
            continue
    audit(db, user_id, 'reminders.queued', run.id, queued=queued, trigger=trigger)
    db.commit()
    return {'run_id': run.id, 'queued': queued, 'skipped': len(preview['skipped']), 'deduplicated': len(preview['companies']) - queued}


def monthly_report(db, month, cutoff=None, outcome=None, search='', page=1, page_size=50):
    cutoff = cutoff or now()
    # Four bounded aggregation queries avoid per-company and per-invoice query loops.
    invoices = db.execute(select(Invoice, Company).join(Company, Company.id == Invoice.company_id).where(Invoice.billing_month == month, Invoice.created_at <= cutoff, Invoice.posted_at <= cutoff, or_(Invoice.voided_at.is_(None), Invoice.voided_at > cutoff), Invoice.lifecycle != 'DRAFT')).all()
    payment_cohort = [Invoice.billing_month == month, Payment.paid_at <= cutoff, or_(Payment.reversed_at.is_(None), Payment.reversed_at > cutoff)]
    totals = dict(db.execute(select(Payment.invoice_id, func.sum(Payment.amount)).join(Invoice, Invoice.id == Payment.invoice_id).where(*payment_cohort).group_by(Payment.invoice_id)).all())
    settlement = dict(db.execute(select(Payment.invoice_id, func.max(Payment.paid_at)).join(Invoice, Invoice.id == Payment.invoice_id).where(*payment_cohort).group_by(Payment.invoice_id)).all())
    reminded = dict(db.execute(select(Reminder.company_id, func.min(Reminder.accepted_at)).join(ReminderItem, ReminderItem.reminder_id == Reminder.id).join(Invoice, Invoice.id == ReminderItem.invoice_id).where(Invoice.billing_month == month, Reminder.accepted_at.is_not(None), Reminder.accepted_at <= cutoff).group_by(Reminder.company_id)).all())
    companies = {}
    billed = collected = pending = Decimal(0)
    policy = db.get(Policy, 1)
    today = cutoff.astimezone(ZoneInfo(policy.timezone)).date()
    overdue = Decimal(0)
    for invoice, company in invoices:
        paid = Decimal(totals.get(invoice.id, 0))
        outstanding = Decimal(invoice.amount) - paid
        billed += invoice.amount
        collected += paid
        pending += outstanding
        if invoice.due_date < today:
            overdue += outstanding
        entry = companies.setdefault(company.id, {'id': company.id, 'name': company.name, 'code': company.code, 'email': company.email, 'industry': company.industry, 'billed': Decimal(0), 'paid': Decimal(0), 'outstanding': Decimal(0), 'last_payment': None, 'invoice_count': 0})
        entry['billed'] += invoice.amount
        entry['paid'] += paid
        entry['outstanding'] += outstanding
        entry['invoice_count'] += 1
        if invoice.id in settlement:
            t = utc(settlement[invoice.id])
            if entry['last_payment'] is None or t > entry['last_payment']:
                entry['last_payment'] = t
    counts = {'paid_after': 0, 'pending_after': 0, 'paid_other': 0, 'pending_unreminded': 0, 'reconciled': 0}
    rows = []
    for entry in companies.values():
        first = reminded.get(entry['id'])
        if first:
            if entry['outstanding'] > 0:
                status = 'pending_after'
            elif entry['last_payment'] and entry['last_payment'] > utc(first):
                status = 'paid_after'
            else:
                status = 'reconciled'
        else:
            status = 'paid_other' if entry['outstanding'] == 0 else 'pending_unreminded'
        counts[status] += 1
        entry.update(outcome=status, reminder_at=utc(first).isoformat() if first else None, last_payment=entry['last_payment'].isoformat() if entry['last_payment'] else None)
        for key in ('billed', 'paid', 'outstanding'):
            entry[key] = money(entry[key])
        if (not outcome or outcome == 'all' or status == outcome) and (not search or search.lower() in (entry['name'] + entry['code']).lower()):
            rows.append(entry)
    rows.sort(key=lambda row: (-Decimal(row['outstanding']), row['name']))
    denominator = counts['paid_after'] + counts['pending_after']
    return {'billing_month': month, 'as_of': cutoff.isoformat(), 'timezone': policy.timezone, 'count_unit': 'distinct_companies', 'reminder_basis': 'provider_accepted', 'billed': money(billed), 'collected': money(collected), 'outstanding': money(pending), 'overdue': money(overdue), 'company_count': len(companies), **counts, 'reminded_count': denominator, 'paid_after_rate': round(counts['paid_after'] / denominator * 100, 1) if denominator else 0, 'companies': rows[(page - 1) * page_size:page * page_size], 'total': len(rows), 'page': page, 'page_size': page_size}


def scheduled_due(policy, instant=None):
    local = (instant or now()).astimezone(ZoneInfo(policy.timezone))
    hour, minute = map(int, policy.time.split(':'))
    day = min(policy.day, calendar.monthrange(local.year, local.month)[1])
    scheduled = local.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
    return policy.enabled and scheduled <= local <= scheduled + timedelta(hours=policy.catchup_hours)
