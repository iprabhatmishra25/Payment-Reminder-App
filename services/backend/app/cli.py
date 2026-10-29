import argparse
import getpass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, func
from .db import Base, SessionLocal, User, Company, Invoice, Payment, Policy, ReminderRun, Reminder, ReminderItem, Audit, now
from .security import passwords, digest, audit
from .config import config


def seed_demo():
    if not config.demo or config.environment == 'production' or config.email_mode != 'preview':
        raise RuntimeError('Demo seed requires DEMO_MODE=true, development environment, and EMAIL_MODE=preview')
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Company)):
            print('Demo data already exists; no records changed.')
            return
        user = db.scalar(select(User).where(User.email == 'demo@duewise.example.com'))
        if not user:
            user = User(name='Prabhat Mishra', email='demo@duewise.example.com', role='admin', password_hash=passwords.hash('DemoOnly!2026'))
            db.add(user)
            db.flush()
        if not db.get(Policy, 1):
            db.add(Policy(id=1))
        names = [('Northstar Technologies','Technology'),('Meridian Logistics','Logistics'),('Evergreen Healthcare','Healthcare'),('Atlas Design Studio','Design'),('Summit Engineering','Engineering'),('Bluebird Media','Media'),('Nova Retail Group','Retail'),('Oakwood Consulting','Consulting'),('Pinnacle Systems','Technology'),('Horizon Foods','Food & Beverage'),('Silverline Finance','Finance'),('Bloom Creative','Design'),('Vertex Solutions','Technology'),('Cedar Infrastructure','Construction'),('Orbit Digital','Marketing'),('Brightpath Education','Education'),('Riverstone Hotels','Hospitality'),('Aster Manufacturing','Manufacturing')]
        month_start = now().date().replace(day=1)
        base = (month_start - timedelta(days=1)).replace(day=1)
        def instant(day):
            return datetime(base.year, base.month, day, 6, 0, tzinfo=timezone.utc)
        run = ReminderRun(month=base.strftime('%Y-%m'), trigger='demo', created_at=instant(16))
        db.add(run)
        db.flush()
        for i, (name, industry) in enumerate(names):
            company = Company(name=name, code=f'DW-{i+1:03}', contact_name=name.split()[0]+' Accounts', email=f'accounts{i+1}@example.com', industry=industry, created_at=instant(1))
            db.add(company)
            db.flush()
            amount = Decimal([48000,32500,78000,24000,56000,18500][i%6])
            invoice = Invoice(company_id=company.id, number=f'INV-{base:%y%m}-{i+1:03}', billing_month=base.strftime('%Y-%m'), issue_date=base, due_date=base.replace(day=12), amount=amount, created_at=instant(1))
            db.add(invoice)
            db.flush()
            if i < 14:
                reminder = Reminder(run_id=run.id, company_id=company.id, recipient=company.email, status='SENT', accepted_at=instant(16), created_at=instant(16), attempts=1, dedupe_key=f'demo:{company.id}:{base:%Y-%m}')
                db.add(reminder)
                db.flush()
                db.add(ReminderItem(reminder_id=reminder.id, invoice_id=invoice.id, balance_snapshot=amount))
            if i < 9 or i in (14,15):
                paid = amount
            elif i in (9,10):
                paid = amount * Decimal('0.4')
            else:
                paid = Decimal(0)
            if paid:
                db.add(Payment(invoice_id=invoice.id, amount=paid, paid_at=instant(20 if i < 14 else 10), recorded_at=instant(20 if i < 14 else 10), recorded_by=user.id, method='BANK_TRANSFER', reference=f'DEMO-RECEIPT-{i+1}', idempotency_key=f'demo-payment-{i+1}', request_hash=digest(str(i))))
        audit(db, user.id, 'demo.seeded', run.id, note='Illustrative records; no messages were sent')
        db.commit()
        print('Demo created. Login: demo@duewise.example.com / DemoOnly!2026')


def main():
    parser = argparse.ArgumentParser(description='Duewise administrative commands')
    parser.add_argument('command', choices=['create-admin','seed-demo','drain-preview','preview-worker','reset-password','disable-mfa'])
    parser.add_argument('--email')
    parser.add_argument('--name', default='Administrator')
    args = parser.parse_args()
    if args.command == 'seed-demo':
        return seed_demo()
    if args.command == 'drain-preview':
        from .jobs import drain_preview
        print(f'Processed {drain_preview()} preview jobs. No emails sent.')
        return
    if args.command == 'preview-worker':
        from .jobs import drain_preview,scan_schedule
        import time
        if config.email_mode!='preview':
            raise RuntimeError('Use Celery for live delivery; this local worker only supports preview mode.')
        print('Local preview worker running. No emails are sent. Press Ctrl+C to stop.')
        try:
            while True:
                with SessionLocal() as db:
                    scan_schedule(db)
                    from .db import RateLimit
                    heartbeat=db.get(RateLimit,'worker-heartbeat')
                    if heartbeat:heartbeat.started_at=now()
                    else:db.add(RateLimit(key='worker-heartbeat',count=0))
                    db.commit()
                drain_preview()
                time.sleep(3)
        except KeyboardInterrupt:return
    email = (args.email or input('Account email: ')).strip().lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if args.command == 'create-admin':
            from pydantic import TypeAdapter, EmailStr
            TypeAdapter(EmailStr).validate_python(email)
            if user:
                raise RuntimeError('Account already exists')
            password = getpass.getpass('New password (12+ characters): ')
            if len(password) < 12:
                raise RuntimeError('Password must contain at least 12 characters')
            user = User(name=args.name, email=email, password_hash=passwords.hash(password), role='admin')
            db.add(user)
            db.flush()
        elif not user:
            raise RuntimeError('Account not found')
        elif args.command == 'reset-password':
            password = getpass.getpass('New password (12+ characters): ')
            if len(password) < 12:
                raise RuntimeError('Password must contain at least 12 characters')
            user.password_hash = passwords.hash(password)
        elif args.command == 'disable-mfa':
            if input('Type DISABLE after independently verifying account ownership: ') != 'DISABLE':
                return
            user.mfa_secret = user.mfa_pending = None
            user.last_totp_step = 0
        if args.command != 'create-admin':
            from .db import AuthSession, AuthToken
            from sqlalchemy import update
            db.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(revoked=True))
            db.execute(update(AuthToken).where(AuthToken.user_id == user.id).values(used=True))
        audit(db, user.id, 'admin_cli.' + args.command, user.id)
        db.commit()
        print('Account updated. Existing sessions revoked when applicable.')


if __name__ == '__main__':
    main()
