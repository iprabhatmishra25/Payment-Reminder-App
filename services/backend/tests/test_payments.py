from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from sqlalchemy import select,func
from app.db import Payment,now


def pay(env,sample,amount='4000.00',key='request-0001',role='admin'):
    return env['client'].post(f"/api/v1/invoices/{sample['invoice_id']}/payments",headers={**env['headers'][role],'Idempotency-Key':key},json={'amount':amount,'paid_at':(now()-timedelta(days=1)).isoformat(),'method':'BANK_TRANSFER','reference':'REFERENCE-123'})


def test_partial_payment_and_exact_balance(env,sample):
    response=pay(env,sample)
    assert response.status_code==201
    assert response.json()['invoice']['outstanding']=='6000.00'
    assert response.json()['invoice']['status']=='PARTIALLY_PAID'


def test_full_payment(env,sample):
    response=pay(env,sample,'10000.00')
    assert response.json()['invoice']['status']=='PAID'
    assert response.json()['invoice']['outstanding']=='0.00'


def test_duplicate_request_returns_same_payment(env,sample):
    # Preserve precisely the same original request body, including timestamp.
    body={'amount':'4000.00','paid_at':(now()-timedelta(days=1)).isoformat(),'method':'BANK_TRANSFER','reference':'REF'}
    headers={**env['headers']['admin'],'Idempotency-Key':'same-request-key'}
    url=f"/api/v1/invoices/{sample['invoice_id']}/payments"
    a=env['client'].post(url,headers=headers,json=body)
    b=env['client'].post(url,headers=headers,json=body)
    assert (a.status_code,b.status_code)==(201,200)
    assert a.json()['id']==b.json()['id']
    with env['factory']() as db: assert db.scalar(select(func.count()).select_from(Payment))==1


def test_key_payload_conflict(env,sample):
    assert pay(env,sample).status_code==201
    assert pay(env,sample,'3000.00').status_code==409


def test_overpayment_rejected(env,sample):
    assert pay(env,sample,'10000.01').status_code==409


def test_invalid_negative_zero_and_precision(env,sample):
    for value in ('0','-1','0.001','NaN','Infinity'):
        assert pay(env,sample,value).status_code==422


def test_future_timestamp_rejected(env,sample):
    result=env['client'].post(f"/api/v1/invoices/{sample['invoice_id']}/payments",headers={**env['headers']['admin'],'Idempotency-Key':'future-time-key'},json={'amount':'10.00','paid_at':(now()+timedelta(days=1)).isoformat()})
    assert result.status_code==422


def test_viewer_cannot_write(env,sample):
    assert pay(env,sample,role='viewer').status_code==403


def test_reversal_preserves_history_and_restores_balance(env,sample):
    result=pay(env,sample)
    payment_id=result.json()['id']
    assert env['client'].post('/api/v1/payments/'+payment_id+'/reverse',headers=env['headers']['accounts'],json={'reason':'Incorrect bank reference'}).status_code==403
    assert env['client'].post('/api/v1/payments/'+payment_id+'/reverse',headers=env['headers']['admin'],json={'reason':'Incorrect bank reference'}).status_code==200
    invoices=env['client'].get('/api/v1/invoices',headers=env['headers']['admin']).json()
    assert invoices['items'][0]['outstanding']=='10000.00'
    with env['factory']() as db:
        assert db.get(Payment,payment_id).reversed_at is not None


def test_simultaneous_payments_cannot_overpay(env,sample):
    def submit(i):return pay(env,sample,'7000.00',key=f'concurrent-key-{i}').status_code
    with ThreadPoolExecutor(max_workers=2) as pool: statuses=list(pool.map(submit,[1,2]))
    assert sorted(statuses)==[201,409]
    with env['factory']() as db: assert db.scalar(select(func.sum(Payment.amount)))==7000


def test_draft_invoice_rejects_payment(env,sample):
    from app.db import Invoice
    with env['factory']() as db:
        db.get(Invoice,sample['invoice_id']).lifecycle='DRAFT';db.commit()
    assert pay(env,sample).status_code==409
