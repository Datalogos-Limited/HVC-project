import os
os.environ['VAULT'] = 'vehicles'
import hashlib
from contextlib import contextmanager
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app, magic_ok
from app.models import DocumentCreate, Money, Credentials
from app.security import Principal, decode_claims, require_access, scope, principal
from app import main, security

TENANT, CLIENT, OTHER = str(uuid4()), str(uuid4()), str(uuid4())
STAFF = Principal('staff', TENANT, frozenset({'asset_manager'}), None)
USER = Principal('user', TENANT, frozenset({'client'}), CLIENT)

@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()

def claims(**patch):
    return {'uid':'user', 'email_verified':True, 'dcb_tenant':TENANT, 'dcb_roles':['client'], 'dcb_client':CLIENT} | patch

@pytest.mark.parametrize('patch', [
    {'email_verified':False}, {'dcb_tenant':'bad'}, {'dcb_roles':'asset_manager'},
    {'dcb_client':None}, {'dcb_client':'invalid'}, {'dcb_roles':[1]},
])
def test_unapproved_claims_denied(patch):
    with pytest.raises(HTTPException) as e: decode_claims(claims(**patch))
    assert e.value.status_code == 403

def test_claims_are_bound():
    p = decode_claims(claims())
    assert p.tenant == TENANT and p.client_id == CLIENT

@pytest.mark.parametrize('vault', ['vehicles','finance','clients'])
def test_client_cannot_create_business_records(vault):
    with pytest.raises(HTTPException) as e: require_access(USER, vault, write=True)
    assert e.value.status_code == 403

def test_client_cannot_read_other_client():
    with pytest.raises(HTTPException) as e: scope(USER, 'vehicles', OTHER)
    assert e.value.status_code == 404

def test_finance_operator_has_no_compliance_access():
    p = Principal('f', TENANT, frozenset({'finance_operator'}), None)
    with pytest.raises(HTTPException): require_access(p, 'compliance')

def test_client_can_create_own_compliance_case():
    require_access(USER, 'compliance', write=True)
    scope(USER, 'compliance', CLIENT)

def test_no_auth_header_is_401():
    response = TestClient(app).get('/v1/assets')
    assert response.status_code == 401

def test_no_anonymous_upload():
    response = TestClient(app).put(f'/v1/documents/{uuid4()}/content', content=b'fake')
    assert response.status_code == 401

def test_staff_must_use_mfa(monkeypatch):
    monkeypatch.setattr(security.firebase_admin, '_apps', {'test':object()})
    monkeypatch.setattr(security.auth, 'verify_id_token', lambda *a, **k: claims(dcb_roles=['asset_manager']))
    response = TestClient(app).get('/v1/assets', headers={'Authorization':'Bearer token'})
    assert response.status_code == 403 and 'multi-factor' in response.text

def test_token_is_verified_with_revocation_check(monkeypatch):
    monkeypatch.setattr(security.firebase_admin, '_apps', {'test':object()})
    calls = []
    def verify(token, **kwargs):
        calls.append(kwargs)
        raise security.auth.InvalidIdTokenError('invalid')
    monkeypatch.setattr(security.auth, 'verify_id_token', verify)
    assert TestClient(app).get('/v1/assets', headers={'Authorization':'Bearer bad'}).status_code == 401
    assert calls == [{'check_revoked':True}]

def test_wrong_vault_routes_absent():
    client = TestClient(app)
    assert client.post('/v1/clients', json={}).status_code == 404
    assert client.post('/v1/auth/sign-in', json={}).status_code == 404

def test_client_create_asset_denied_before_db():
    app.dependency_overrides[principal] = lambda: USER
    payload = {'client_id':CLIENT,'make':'Ferrari','model':'F40','year':1989,'chassis_number':'123',
        'category':'supercar','purchase':{'minor_units':123,'currency':'GBP'},'valuation':{'minor_units':123,'currency':'GBP'}}
    r = TestClient(app).post('/v1/assets', json=payload, headers={'Idempotency-Key':'test-key-001'})
    assert r.status_code == 403

@pytest.mark.parametrize('filename', ['../secret.pdf', 'folder/file.pdf', 'x\\file.pdf', 'x\x00.pdf'])
def test_document_paths_rejected(filename):
    with pytest.raises(ValidationError):
        DocumentCreate(parent_id=uuid4(),filename=filename,category='title',content_type='application/pdf',size_bytes=5,sha256='a'*64)

def test_document_size_bounded():
    with pytest.raises(ValidationError):
        DocumentCreate(parent_id=uuid4(),filename='a.pdf',category='title',content_type='application/pdf',size_bytes=21*1024*1024,sha256='a'*64)

@pytest.mark.parametrize('content_type,data,valid', [
    ('application/pdf',b'%PDF-1.7',True), ('image/png',b'\x89PNG\r\n\x1a\n',True),
    ('image/jpeg',b'\xff\xd8\xff',True), ('application/pdf',b'<script>',False), ('image/png',b'%PDF-1.7',False)])
def test_mime_spoofing(content_type,data,valid):
    assert magic_ok(content_type,data) is valid

def test_financial_amount_is_not_float():
    with pytest.raises(ValidationError): Money(minor_units=12.3,currency='GBP')
    with pytest.raises(ValidationError): Money(minor_units=-1,currency='GBP')

def test_extra_role_field_rejected():
    with pytest.raises(ValidationError): Credentials(email='x@example.com',password='123456789012',roles=['admin'])

def install_document(monkeypatch, status='awaiting_upload'):
    data = {'status':status,'size_bytes':6,'sha256':hashlib.sha256(b'%PDF-x').hexdigest(),'content_type':'application/pdf'}
    @contextmanager
    def fake_transaction(p): yield None
    monkeypatch.setattr(main, 'transaction', fake_transaction)
    monkeypatch.setattr(main, 'find', lambda *a, **k: {'data':data,'client_id':CLIENT})
    app.dependency_overrides[principal] = lambda: USER

def test_quarantined_document_cannot_be_downloaded(monkeypatch):
    install_document(monkeypatch,'quarantined')
    r = TestClient(app).get(f'/v1/documents/{uuid4()}/content')
    assert r.status_code == 423

def test_upload_over_declared_limit_rejected_before_storage(monkeypatch):
    install_document(monkeypatch)
    r = TestClient(app).put(f'/v1/documents/{uuid4()}/content',content=b'%PDF-too-long',headers={'Content-Type':'application/pdf'})
    assert r.status_code == 413

def test_upload_bad_checksum_rejected(monkeypatch):
    install_document(monkeypatch)
    r = TestClient(app).put(f'/v1/documents/{uuid4()}/content',content=b'%PDF-y',headers={'Content-Type':'application/pdf'})
    assert r.status_code == 422

def test_response_security_headers():
    r = TestClient(app).get('/healthz')
    assert r.headers['cache-control'] == 'no-store'
    assert r.headers['x-content-type-options'] == 'nosniff'

def test_validation_does_not_echo_private_input():
    app.dependency_overrides[principal] = lambda: STAFF
    r = TestClient(app).post('/v1/assets', json={'private':'SHOULD-NOT-BE-ECHOED'}, headers={'Idempotency-Key':'test-private-001'})
    assert r.status_code == 422
    assert 'SHOULD-NOT-BE-ECHOED' not in r.text

def test_identity_evidence_not_accepted_in_vehicle_vault():
    app.dependency_overrides[principal] = lambda: USER
    body={'parent_id':str(uuid4()),'filename':'passport.pdf','category':'identity','content_type':'application/pdf','size_bytes':10,'sha256':'a'*64}
    r = TestClient(app).post('/v1/documents',json=body,headers={'Idempotency-Key':'wrong-vault-001'})
    assert r.status_code == 422 and 'another vault' in r.text

def test_transaction_amount_must_be_positive():
    from app.models import TransactionDraft
    with pytest.raises(ValidationError):
        TransactionDraft(client_id=CLIENT,asset_id=uuid4(),direction='purchase',amount={'minor_units':0,'currency':'GBP'})
