import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('APP_ENV','development')
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet
from app import db as models, main, security, jobs
from app.config import config


@pytest.fixture
def env(tmp_path,monkeypatch):
    url=os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tmp_path}/test.db'
    engine=create_engine(url,connect_args={'check_same_thread':False,'timeout':30} if url.startswith('sqlite') else {})
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    for module in (models,main,security,jobs):
        monkeypatch.setattr(module,'SessionLocal',factory)
    monkeypatch.setattr(config,'environment','development')
    monkeypatch.setattr(config,'demo',False)
    monkeypatch.setattr(config,'email_mode','preview')
    monkeypatch.setattr(config,'encryption_key',Fernet.generate_key().decode())
    with factory() as db:
        admin=models.User(name='Test Admin',email='admin@example.com',role='admin',password_hash=security.passwords.hash('TestPassword!2026'))
        viewer=models.User(name='Test Viewer',email='viewer@example.com',role='viewer',password_hash=admin.password_hash)
        accounts=models.User(name='Test Accounts',email='accounts@example.com',role='accounts',password_hash=admin.password_hash)
        db.add_all([admin,viewer,accounts,models.Policy(id=1)])
        db.commit()
        tokens={}
        for user in (admin,viewer,accounts):
            session=security.new_session(db,user,True)
            tokens[user.role]={'Authorization':'Bearer '+session['access_token']}
        admin_id=admin.id
    with TestClient(main.app) as client:
        yield {'client':client,'factory':factory,'headers':tokens,'admin_id':admin_id,'engine':engine}
    models.Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sample(env):
    from datetime import timedelta
    from decimal import Decimal
    with env['factory']() as db:
        company=models.Company(name='Acme Limited',code='ACME',email='billing@example.com',industry='Technology')
        db.add(company);db.flush()
        instant=models.now()-timedelta(days=10)
        invoice=models.Invoice(company_id=company.id,number='INV-001',billing_month=instant.strftime('%Y-%m'),issue_date=instant.date(),due_date=instant.date()+timedelta(days=1),amount=Decimal('10000.00'),created_at=instant)
        db.add(invoice);db.commit()
        return {'company_id':company.id,'invoice_id':invoice.id,'month':invoice.billing_month}
