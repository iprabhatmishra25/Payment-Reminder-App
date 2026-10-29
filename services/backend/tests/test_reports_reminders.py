from datetime import datetime,timedelta,timezone
from decimal import Decimal
from sqlalchemy import select,func
from app.db import now,Reminder,ReminderRun,ReminderItem,Invoice,Payment,Outbox,Policy
from app.services import monthly_report,scheduled_due
from app.jobs import process_one,tick,DeliveryError


def seed_reminder(env,sample,amount='10000.00',payment_after=True):
    with env['factory']() as db:
        run=ReminderRun(month=sample['month'],trigger='test');db.add(run);db.flush()
        reminder=Reminder(run_id=run.id,company_id=sample['company_id'],recipient='billing@example.com',status='SENT',dedupe_key='test-reminder-key',accepted_at=now()-timedelta(days=5));db.add(reminder);db.flush()
        db.add(ReminderItem(reminder_id=reminder.id,invoice_id=sample['invoice_id'],balance_snapshot=Decimal('10000')))
        if amount:
            db.add(Payment(invoice_id=sample['invoice_id'],amount=Decimal(amount),paid_at=now()-timedelta(days=2 if payment_after else 8),recorded_by=env['admin_id'],method='BANK_TRANSFER',idempotency_key='seed-payment',request_hash='seed'))
        db.commit()


def test_fully_paid_after_reminder(env,sample):
    seed_reminder(env,sample)
    with env['factory']() as db:
        report=monthly_report(db,sample['month'])
        assert report['paid_after']==1 and report['pending_after']==0 and report['paid_after_rate']==100


def test_partial_payment_remains_pending(env,sample):
    seed_reminder(env,sample,'4000')
    with env['factory']() as db:
        result=monthly_report(db,sample['month'])
        assert result['pending_after']==1 and result['outstanding']=='6000.00'


def test_company_multiple_invoices_counted_once_and_pending(env,sample):
    seed_reminder(env,sample)
    with env['factory']() as db:
        original=db.get(Invoice,sample['invoice_id'])
        db.add(Invoice(company_id=sample['company_id'],number='INV-002',billing_month=sample['month'],issue_date=original.issue_date,due_date=original.due_date,amount=Decimal(2000),created_at=original.created_at))
        db.commit();result=monthly_report(db,sample['month'])
        assert result['company_count']==1 and result['pending_after']==1 and result['paid_after']==0


def test_backdated_receipt_is_not_paid_after(env,sample):
    seed_reminder(env,sample,payment_after=False)
    with env['factory']() as db:
        result=monthly_report(db,sample['month'])
        assert result['reconciled']==1 and result['reminded_count']==0


def test_no_reminders_empty_chart(env,sample):
    with env['factory']() as db:
        result=monthly_report(db,sample['month'])
        assert result['reminded_count']==0 and result['paid_after_rate']==0 and result['pending_unreminded']==1


def test_historical_cutoff_ignores_later_payment(env,sample):
    seed_reminder(env,sample)
    with env['factory']() as db:
        result=monthly_report(db,sample['month'],now()-timedelta(days=3))
        assert result['pending_after']==1 and result['collected']=='0.00'


def test_historical_report_excludes_later_posted_invoice(env,sample):
    with env['factory']() as db:
        invoice=db.get(Invoice,sample['invoice_id'])
        invoice.posted_at=now()
        db.commit()
        result=monthly_report(db,sample['month'],now()-timedelta(days=2))
        assert result['company_count']==0 and result['billed']=='0.00'


def test_preview_does_not_send(env,sample):
    c=env['client'];h=env['headers']['admin']
    response=c.post('/api/v1/reminder-runs/preview',headers=h,json={})
    assert response.status_code==200 and len(response.json()['companies'])==1
    with env['factory']() as db: assert db.scalar(select(func.count()).select_from(Reminder))==0


def test_monthly_run_is_deduplicated_and_preview_not_counted(env,sample):
    c=env['client'];h=env['headers']['admin']
    a=c.post('/api/v1/reminder-runs',headers=h,json={});b=c.post('/api/v1/reminder-runs',headers=h,json={})
    assert a.json()['queued']==1 and b.json()['queued']==0
    with env['factory']() as db: event_id=db.scalar(select(Outbox.id))
    process_one(event_id)
    process_one(event_id)
    with env['factory']() as db:
        reminder=db.scalar(select(Reminder));assert reminder.status=='PREVIEW' and reminder.accepted_at is None
        assert monthly_report(db,sample['month'])['reminded_count']==0


def test_paid_before_worker_is_cancelled(env,sample):
    env['client'].post('/api/v1/reminder-runs',headers=env['headers']['admin'],json={})
    with env['factory']() as db:
        event_id=db.scalar(select(Outbox.id));db.add(Payment(invoice_id=sample['invoice_id'],amount=10000,paid_at=now(),recorded_by=env['admin_id'],method='CASH',idempotency_key='paid-before-worker',request_hash='seed'));db.commit()
    process_one(event_id)
    with env['factory']() as db:assert db.scalar(select(Reminder)).status=='CANCELLED'


def test_provider_timeout_is_unknown_not_automatic_resend(env,sample,monkeypatch):
    from app import jobs
    env['client'].post('/api/v1/reminder-runs',headers=env['headers']['admin'],json={})
    with env['factory']() as db:event_id=db.scalar(select(Outbox.id))
    def timeout(*args):raise DeliveryError('Provider response uncertain',uncertain=True)
    monkeypatch.setattr(jobs,'send_email',timeout)
    process_one(event_id)
    with env['factory']() as db:
        reminder=db.scalar(select(Reminder));assert reminder.status=='UNKNOWN' and db.get(Outbox,event_id).done


def test_schedule_calendar_and_timezone_boundaries():
    policy=Policy(day=31,time='10:00',timezone='Asia/Kolkata',enabled=True,catchup_hours=48)
    assert scheduled_due(policy,datetime(2028,2,29,4,30,tzinfo=timezone.utc))
    assert not scheduled_due(policy,datetime(2028,2,29,4,29,tzinfo=timezone.utc))
    policy.enabled=False
    assert not scheduled_due(policy,datetime(2028,2,29,4,30,tzinfo=timezone.utc))
