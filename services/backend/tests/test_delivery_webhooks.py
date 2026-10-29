import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from sqlalchemy import select,func
from app.db import now,Reminder,ReminderRun,WebhookEvent,Outbox,Policy
from app.config import config
from app.jobs import process_one


def signature(secret,body,event_id='event-001',timestamp=None):
    timestamp=str(timestamp or int(time.time()))
    sig=base64.b64encode(hmac.new(base64.b64decode(secret.removeprefix('whsec_')),event_id.encode()+b'.'+timestamp.encode()+b'.'+body,hashlib.sha256).digest()).decode()
    return {'svix-id':event_id,'svix-timestamp':timestamp,'svix-signature':'v1,'+sig,'Content-Type':'application/json'}


def test_valid_signed_event_dedup_and_out_of_order_delivery(env,sample,monkeypatch):
    secret='whsec_'+base64.b64encode(b'webhook-secret-for-tests').decode()
    monkeypatch.setattr(config,'email_webhook_secret',secret)
    with env['factory']() as db:
        run=ReminderRun(month=sample['month'],trigger='test');db.add(run);db.flush()
        reminder=Reminder(run_id=run.id,company_id=sample['company_id'],recipient='billing@example.com',status='SENT',dedupe_key='signed-test',accepted_at=now(),provider_id='provider-123');db.add(reminder);db.commit();reminder_id=reminder.id
    body=json.dumps({'type':'email.delivered','data':{'email_id':'provider-123'}}).encode()
    headers=signature(secret,body)
    assert env['client'].post('/api/v1/webhooks/email',content=body,headers=headers).status_code==200
    assert env['client'].post('/api/v1/webhooks/email',content=body,headers=headers).json()['duplicate']
    sent=json.dumps({'type':'email.sent','data':{'email_id':'provider-123'}}).encode()
    env['client'].post('/api/v1/webhooks/email',content=sent,headers=signature(secret,sent,'event-002'))
    with env['factory']() as db:
        assert db.get(Reminder,reminder_id).status=='DELIVERED'
        assert db.scalar(select(func.count()).select_from(WebhookEvent))==2


def test_invalid_and_expired_signatures_rejected(env,monkeypatch):
    secret='whsec_'+base64.b64encode(b'webhook-secret-for-tests').decode()
    monkeypatch.setattr(config,'email_webhook_secret',secret)
    body=b'{"type":"email.delivered","data":{"email_id":"x"}}'
    headers=signature(secret,body,timestamp=int(time.time())-1000)
    assert env['client'].post('/api/v1/webhooks/email',content=body,headers=headers).status_code==400
    headers=signature(secret,body);headers['svix-signature']='v1,invalid'
    assert env['client'].post('/api/v1/webhooks/email',content=body,headers=headers).status_code==400


def test_retry_preview_rebuilds_payload_from_current_policy(env,sample):
    c=env['client'];h=env['headers']['admin']
    c.post('/api/v1/reminder-runs',headers=h,json={})
    with env['factory']() as db:
        event_id=db.scalar(select(Outbox.id));reminder_id=db.scalar(select(Reminder.id))
    process_one(event_id)
    assert c.post('/api/v1/reminders/'+reminder_id+'/retry',headers=h).status_code==200
    with env['factory']() as db:
        events=list(db.scalars(select(Outbox).order_by(Outbox.available_at)))
        assert len(events)==2
        assert db.get(Outbox,event_id).done
        new=db.scalar(select(Outbox).where(Outbox.done.is_(False)))
        assert 'prepared_body' not in new.payload


def test_worker_crash_claim_goes_unknown(env,sample):
    env['client'].post('/api/v1/reminder-runs',headers=env['headers']['admin'],json={})
    with env['factory']() as db:
        event=db.scalar(select(Outbox));event.available_at=now()-timedelta(minutes=5)
        reminder=db.get(Reminder,event.aggregate_id);reminder.status='SENDING';db.commit();event_id=event.id
    process_one(event_id)
    with env['factory']() as db:assert db.scalar(select(Reminder)).status=='UNKNOWN'


def test_unknown_delivery_requires_admin_review_before_retry(env,sample):
    c=env['client'];h=env['headers']['admin']
    c.post('/api/v1/reminder-runs',headers=h,json={})
    with env['factory']() as db:
        reminder=db.scalar(select(Reminder));reminder.status='UNKNOWN';db.commit();reminder_id=reminder.id
    assert c.post('/api/v1/reminders/'+reminder_id+'/retry',headers=h).status_code==409
    body={'outcome':'NOT_SENT','reason':'Verified no matching message in provider history'}
    assert c.post('/api/v1/reminders/'+reminder_id+'/resolve',headers=env['headers']['accounts'],json=body).status_code==403
    assert c.post('/api/v1/reminders/'+reminder_id+'/resolve',headers=h,json=body).status_code==200
    assert c.post('/api/v1/reminders/'+reminder_id+'/retry',headers=h).status_code==200


def test_successful_provider_acceptance_updates_history(env,sample,monkeypatch):
    from app import jobs
    env['client'].post('/api/v1/reminder-runs',headers=env['headers']['admin'],json={})
    with env['factory']() as db:event_id=db.scalar(select(Outbox.id))
    monkeypatch.setattr(jobs,'send_email',lambda *_:'provider-acceptance-test')
    process_one(event_id)
    with env['factory']() as db:
        reminder=db.scalar(select(Reminder))
        assert reminder.status=='SENT' and reminder.accepted_at is not None
        assert reminder.provider_id=='provider-acceptance-test'


def test_scheduled_scan_only_creates_one_monthly_run(env,sample,monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs,'scheduled_due',lambda _:True)
    with env['factory']() as db:
        jobs.scan_schedule(db)
        jobs.scan_schedule(db)
        assert db.scalar(select(func.count()).select_from(ReminderRun))==1
        assert db.scalar(select(func.count()).select_from(Reminder))==1
