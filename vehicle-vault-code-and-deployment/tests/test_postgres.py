"""Real RLS/idempotency checks. Local run requires TEST_DATABASE_URL.
A PostgreSQL container is supplied in CI; no SQLite substitution is used.
"""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient
from app import db, main
from app.security import Principal, principal

pytestmark = pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='PostgreSQL integration database not configured')

@pytest.fixture(scope='module')
def database():
    import psycopg
    url = os.environ['TEST_DATABASE_URL']
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("CREATE ROLE dcb_test_runtime LOGIN PASSWORD 'test-only-password'")
        c.execute("CREATE ROLE dcb_test_scanner LOGIN PASSWORD 'test-only-password'")
        sql = Path('scripts/schema.sql').read_text().replace(':"runtime_user"','dcb_test_runtime').replace(':"scanner_user"','dcb_test_scanner')
        c.execute(sql)
    from sqlalchemy.engine import make_url
    u = make_url(url.replace('postgresql://','postgresql+psycopg://')).set(username='dcb_test_runtime',password='test-only-password')
    engine = create_engine(u)
    yield engine
    engine.dispose()

@pytest.fixture
def api(database,monkeypatch):
    monkeypatch.setattr(db,'engine',lambda:database)
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()

def payload(client):
    return {'client_id':client,'make':'Ferrari','model':'F40','year':1989,'chassis_number':str(uuid4()),'category':'supercar',
            'purchase':{'minor_units':10000,'currency':'GBP'},'valuation':{'minor_units':11000,'currency':'GBP'}}

def test_rls_and_idempotency(api,database):
    tenant, client = str(uuid4()),str(uuid4())
    staff = Principal('staff-a',tenant,frozenset({'asset_manager'}),None)
    main.app.dependency_overrides[principal] = lambda:staff
    body=payload(client)
    headers={'Idempotency-Key':'integration-create-001'}
    first=api.post('/v1/assets',json=body,headers=headers)
    assert first.status_code==201,first.text
    rid=first.json()['id']
    again=api.post('/v1/assets',json=body,headers=headers)
    assert again.status_code==201 and again.json()['id']==rid
    changed=api.post('/v1/assets',json=body|{'model':'F50'},headers=headers)
    assert changed.status_code==409
    main.app.dependency_overrides[principal] = lambda:Principal('other-tenant',str(uuid4()),frozenset({'asset_manager'}),None)
    assert api.get('/v1/assets/'+rid).status_code==404
    assert api.get('/v1/assets').json()['items']==[]
    main.app.dependency_overrides[principal] = lambda:Principal('other-client',tenant,frozenset({'client'}),str(uuid4()))
    assert api.get('/v1/assets/'+rid).status_code==404
    assert api.get('/v1/assets').json()['items']==[]
    main.app.dependency_overrides[principal] = lambda:Principal('owner',tenant,frozenset({'client'}),client)
    assert api.get('/v1/assets/'+rid).status_code==200
    # Force RLS also applies to unscoped direct SQL as the runtime identity.
    with database.begin() as c:
        assert c.execute(text('SELECT count(*) FROM records')).scalar()==0
        with pytest.raises(Exception):
            c.execute(text('DELETE FROM records'))
