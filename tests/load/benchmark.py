"""Disposable SQLite scale check. This is not a PostgreSQL production benchmark."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'services/backend'))
from sqlalchemy import create_engine,insert
from sqlalchemy.orm import Session
from app.db import Base,Company,Invoice,Payment,User,Policy,now
from app.services import monthly_report


def main():
    with tempfile.TemporaryDirectory(prefix='duewise-scale-') as directory:
        engine=create_engine('sqlite:///'+str(Path(directory)/'scale.db'))
        Base.metadata.create_all(engine)
        instant=now()-timedelta(days=15)
        month=instant.strftime('%Y-%m')
        companies=[{'id':str(uuid4()),'name':f'Company {i:05}','code':f'C-{i:05}','email':f'accounts{i}@example.com','industry':'Technology'} for i in range(10000)]
        invoices=[]
        for offset in range(12):
            month_index=instant.year*12+instant.month-1-offset
            year,index=divmod(month_index,12)
            issue=instant.replace(year=year,month=index+1,day=1)
            for i,company in enumerate(companies):
                invoices.append({'id':str(uuid4()),'company_id':company['id'],'number':f'INV-{year}-{index+1:02}-{i:05}','billing_month':f'{year}-{index+1:02}','issue_date':issue.date(),'due_date':issue.date()+timedelta(days=10),'amount':Decimal('1000.00'),'created_at':issue})
        user_id=str(uuid4())
        payments=[{'invoice_id':i['id'],'amount':Decimal('1000.00'),'paid_at':instant,'recorded_by':user_id,'method':'BANK_TRANSFER','idempotency_key':f'benchmark-{n}','request_hash':'benchmark'} for n,i in enumerate(invoices) if n%5<3]
        with Session(engine) as db:
            db.execute(insert(User),[{'id':user_id,'name':'Benchmark Only','email':'benchmark@example.com','role':'admin','password_hash':'disabled-no-login'}]);db.add(Policy(id=1));db.commit()
            for model,rows in [(Company,companies),(Invoice,invoices),(Payment,payments)]:
                for offset in range(0,len(rows),2000):db.execute(insert(model),rows[offset:offset+2000])
                db.commit()
            start=time.perf_counter();result=monthly_report(db,month);duration=time.perf_counter()-start
            assert result['company_count']==10000
            assert result['billed']=='10000000.00'
            assert result['collected']=='6000000.00'
            assert result['outstanding']=='4000000.00'
            print({'database':'SQLite disposable local check','companies':len(companies),'invoices':len(invoices),'payments':len(payments),'monthly_report_seconds':round(duration,3),'assertions':'passed'})
        engine.dispose()


if __name__=='__main__':main()
