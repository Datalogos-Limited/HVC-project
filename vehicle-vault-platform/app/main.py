import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from uuid import UUID, uuid4
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from google.cloud import storage
from sqlalchemy import text
from . import models
from .db import transaction
from .security import Principal, principal, require_access, scope

VAULT = os.environ.get('VAULT', 'vehicles')
if VAULT not in {'clients', 'vehicles', 'compliance', 'finance'}:
    raise RuntimeError('Unknown vault')
KIND = {'clients': 'clients', 'vehicles': 'assets', 'compliance': 'kyc-cases', 'finance': 'transactions'}[VAULT]
app = FastAPI(title=f'DCB {VAULT} vault', version='0.1.0', docs_url='/docs' if os.getenv('ENABLE_DOCS') == 'true' else None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[os.environ.get('FRONTEND_ORIGIN', 'http://localhost:8081')],
    allow_methods=['GET', 'POST', 'PUT', 'OPTIONS'], allow_headers=['Authorization', 'Content-Type', 'Idempotency-Key'])

@app.middleware('http')
async def secure_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Request-ID'] = str(uuid4())
    return response

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Never echo credentials, identity data or rejected request bodies.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': list(e['loc']), 'type': e['type'], 'msg': e['msg']} for e in exc.errors()]})

@app.get('/healthz')
def health():
    return {'status': 'ok', 'vault': VAULT}

def audit(c, p, action, resource_id):
    c.execute(text('INSERT INTO audit_events (tenant_id, actor_id, action, resource_id) VALUES (:t,:u,:a,:r)'),
              {'t': p.tenant, 'u': p.uid, 'a': action, 'r': resource_id})

def find(c, p, record_id, kind, lock=False):
    q = 'SELECT * FROM records WHERE id=:id AND tenant_id=:t AND kind=:k'
    row = c.execute(text(q + (' FOR UPDATE' if lock else '')), {'id': str(record_id), 't': p.tenant, 'k': kind}).mappings().first()
    if not row:
        raise HTTPException(404, 'Resource not found')
    scope(p, VAULT, str(row['client_id']))
    return row

def envelope(row):
    return {'id': str(row['id']), 'client_id': str(row['client_id']), 'version': row['version'],
            'created_at': row['created_at'].isoformat(), 'data': row['data']}

def create_record(p, kind, data, client_id, key):
    require_access(p, VAULT, write=True)
    scope(p, VAULT, client_id)
    body = json.dumps(data, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(body.encode()).hexdigest()
    with transaction(p) as c:
        # Serialises same tenant/actor/kind/key retries across Cloud Run instances.
        lock_key = f'{p.tenant}:{p.uid}:{kind}:{key}'
        c.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'), {'k': lock_key})
        old = c.execute(text('SELECT * FROM idempotency WHERE tenant_id=:t AND actor_id=:u AND kind=:k AND key=:i'),
            {'t': p.tenant, 'u': p.uid, 'k': kind, 'i': key}).mappings().first()
        if old:
            if old['request_hash'] != digest:
                raise HTTPException(409, 'Idempotency key reused with different content')
            return envelope(find(c, p, old['record_id'], kind))
        rid = str(uuid4())
        if kind == 'clients':
            client_id = rid
        c.execute(text('INSERT INTO records(id,tenant_id,client_id,kind,data) VALUES (:id,:t,:c,:k,CAST(:d AS jsonb))'),
            {'id': rid, 't': p.tenant, 'c': client_id, 'k': kind, 'd': body})
        c.execute(text('INSERT INTO idempotency(tenant_id,actor_id,kind,key,request_hash,record_id) VALUES (:t,:u,:k,:i,:h,:r)'),
            {'t': p.tenant, 'u': p.uid, 'k': kind, 'i': key, 'h': digest, 'r': rid})
        audit(c, p, f'{kind}.created', rid)
        return envelope(find(c, p, rid, kind))

IdempotencyKey = Header(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')

@app.get(f'/v1/{KIND}', response_model=models.RecordPage)
def list_records(p: Principal = Depends(principal), limit: int = Query(50, ge=1, le=100), after: UUID | None = None):
    require_access(p, VAULT)
    with transaction(p) as c:
        rows = c.execute(text('SELECT * FROM records WHERE tenant_id=:t AND kind=:k AND (CAST(:a AS uuid) IS NULL OR id > CAST(:a AS uuid)) ORDER BY id LIMIT :n'),
            {'t': p.tenant, 'k': KIND, 'a': str(after) if after else None, 'n': limit + 1}).mappings().all()
        audit(c, p, f'{KIND}.listed', None)
        return {'items': [envelope(r) for r in rows[:limit]], 'next_cursor': str(rows[limit-1]['id']) if len(rows) > limit else None}

@app.get(f'/v1/{KIND}/{{record_id}}', response_model=models.RecordEnvelope)
def get_record(record_id: UUID, p: Principal = Depends(principal)):
    require_access(p, VAULT)
    with transaction(p) as c:
        row = find(c, p, record_id, KIND)
        audit(c, p, f'{KIND}.read', str(record_id))
        return envelope(row)

if VAULT == 'clients':
    @app.post('/v1/clients', status_code=201, response_model=models.RecordEnvelope)
    def create_client(body: models.Client, p: Principal = Depends(principal), idempotency_key: str = IdempotencyKey):
        return create_record(p, KIND, body.model_dump(mode='json'), str(uuid4()), idempotency_key)

    def identity_call(method, body):
        api_key = os.environ.get('IDENTITY_API_KEY')
        if not api_key:
            raise HTTPException(503, 'Identity Platform not configured')
        try:
            r = httpx.post(f'https://identitytoolkit.googleapis.com/v1/accounts:{method}', params={'key': api_key},
                json={'email': body.email, 'password': body.password.get_secret_value(), 'returnSecureToken': True}, timeout=15)
        except httpx.HTTPError:
            raise HTTPException(503, 'Identity provider unavailable')
        if r.status_code != 200:
            raise HTTPException(400, 'Authentication request could not be completed')
        d = r.json()
        return {'id_token': d['idToken'], 'refresh_token': d['refreshToken'], 'expires_in': int(d['expiresIn']), 'user_id': d['localId']}

    @app.post('/v1/auth/sign-up', response_model=models.TokenResponse)
    def sign_up(body: models.Credentials):
        return identity_call('signUp', body)

    @app.post('/v1/auth/sign-in', response_model=models.TokenResponse)
    def sign_in(body: models.SignIn):
        return identity_call('signInWithPassword', body)

    @app.get('/v1/me', response_model=models.IdentityResponse)
    def me(p: Principal = Depends(principal)):
        return {'user_id': p.uid, 'tenant_id': p.tenant, 'roles': sorted(p.roles), 'client_id': p.client_id}

if VAULT == 'vehicles':
    @app.post('/v1/assets', status_code=201, response_model=models.RecordEnvelope)
    def create_asset(body: models.Asset, p: Principal = Depends(principal), idempotency_key: str = IdempotencyKey):
        return create_record(p, KIND, body.model_dump(mode='json'), str(body.client_id), idempotency_key)

if VAULT == 'compliance':
    @app.post('/v1/kyc-cases', status_code=201, response_model=models.RecordEnvelope)
    def create_case(body: models.KycCase, p: Principal = Depends(principal), idempotency_key: str = IdempotencyKey):
        data = body.model_dump(mode='json') | {'status': 'draft'}
        return create_record(p, KIND, data, str(body.client_id), idempotency_key)

if VAULT == 'finance':
    @app.post('/v1/transactions', status_code=201, response_model=models.RecordEnvelope)
    def create_transaction(body: models.TransactionDraft, p: Principal = Depends(principal), idempotency_key: str = IdempotencyKey):
        return create_record(p, KIND, body.model_dump(mode='json') | {'status': 'draft'}, str(body.client_id), idempotency_key)

    @app.post('/v1/transactions/{record_id}/submit')
    def submit_transaction(record_id: UUID, p: Principal = Depends(principal)):
        require_access(p, VAULT, write=True)
        with transaction(p) as c:
            find(c, p, record_id, KIND)
        raise HTTPException(501, 'Settlement adapter and approval workflow are not implemented; no funds moved')

@app.post('/v1/documents', status_code=201, response_model=models.RecordEnvelope)
def begin_document(body: models.DocumentCreate, p: Principal = Depends(principal), idempotency_key: str = IdempotencyKey):
    allowed = {
        'compliance': {'identity','address','source_of_funds','other'},
        'clients': {'mandate','contract','other'},
        'vehicles': {'title','insurance','valuation','service','inspection','other'},
        'finance': {'contract','bank_statement','other'},
    }
    if body.category not in allowed[VAULT]:
        raise HTTPException(422, 'Document category belongs in another vault')
    # Clients may upload their own evidence/documents but cannot create vehicle or finance records.
    require_access(p, VAULT)
    with transaction(p) as c:
        parent = find(c, p, body.parent_id, KIND)
    # create_record uses stricter write guard; grant no extra privileges: document operation handled separately.
    return create_document_record(p, body, str(parent['client_id']), idempotency_key)

def create_document_record(p, body, client_id, key):
    data = body.model_dump(mode='json') | {'status': 'awaiting_upload', 'generation': None}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction(p) as c:
        c.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'), {'k': f'{p.tenant}:{p.uid}:documents:{key}'})
        old = c.execute(text("SELECT * FROM idempotency WHERE tenant_id=:t AND actor_id=:u AND kind='documents' AND key=:i"),
            {'t': p.tenant, 'u': p.uid, 'i': key}).mappings().first()
        if old:
            if old['request_hash'] != digest:
                raise HTTPException(409, 'Idempotency key reused with different content')
            return envelope(find(c, p, old['record_id'], 'documents'))
        rid = str(uuid4())
        c.execute(text("INSERT INTO records(id,tenant_id,client_id,kind,data) VALUES (:id,:t,:c,'documents',CAST(:d AS jsonb))"),
            {'id': rid, 't': p.tenant, 'c': client_id, 'd': json.dumps(data)})
        c.execute(text("INSERT INTO idempotency(tenant_id,actor_id,kind,key,request_hash,record_id) VALUES (:t,:u,'documents',:i,:h,:r)"),
            {'t': p.tenant, 'u': p.uid, 'i': key, 'h': digest, 'r': rid})
        audit(c, p, 'document.created', rid)
        return envelope(find(c, p, rid, 'documents'))

@app.get('/v1/documents', response_model=models.RecordPage)
def list_documents(parent_id: UUID, p: Principal = Depends(principal), limit: int = Query(50, ge=1, le=100), after: UUID | None = None):
    require_access(p, VAULT)
    with transaction(p) as c:
        find(c, p, parent_id, KIND)
        rows = c.execute(text("SELECT * FROM records WHERE tenant_id=:t AND kind='documents' AND data->>'parent_id'=:p AND (CAST(:a AS uuid) IS NULL OR id>CAST(:a AS uuid)) ORDER BY id LIMIT :n"),
            {'t': p.tenant, 'p': str(parent_id), 'a': str(after) if after else None, 'n': limit+1}).mappings().all()
        audit(c, p, 'documents.listed', str(parent_id))
        return {'items': [envelope(r) for r in rows[:limit]], 'next_cursor': str(rows[limit-1]['id']) if len(rows)>limit else None}

@app.get('/v1/documents/{document_id}', response_model=models.RecordEnvelope)
def get_document(document_id: UUID, p: Principal = Depends(principal)):
    require_access(p, VAULT)
    with transaction(p) as c:
        row = find(c, p, document_id, 'documents')
        audit(c, p, 'document.read', str(document_id))
        return envelope(row)

def magic_ok(content_type, prefix):
    return ((content_type == 'application/pdf' and prefix.startswith(b'%PDF-')) or
        (content_type == 'image/png' and prefix.startswith(b'\x89PNG\r\n\x1a\n')) or
        (content_type == 'image/jpeg' and prefix.startswith(b'\xff\xd8\xff')))

@app.put('/v1/documents/{document_id}/content', status_code=202, response_model=models.UploadResponse, openapi_extra={'requestBody': {'required': True, 'content': {t: {'schema': {'type': 'string', 'format': 'binary'}} for t in ['application/pdf', 'image/jpeg', 'image/png']}}})
async def upload_document(document_id: UUID, request: Request, p: Principal = Depends(principal)):
    require_access(p, VAULT)
    with transaction(p) as c:
        row = find(c, p, document_id, 'documents')
        data = row['data']
    if data['status'] != 'awaiting_upload':
        raise HTTPException(409, 'Upload already committed')
    if request.headers.get('content-type', '').split(';')[0] != data['content_type']:
        raise HTTPException(415, 'Content type does not match document')
    h = hashlib.sha256()
    size = 0
    with tempfile.TemporaryFile() as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > data['size_bytes']:
                raise HTTPException(413, 'Upload exceeds declared length')
            h.update(chunk)
            f.write(chunk)
        if size != data['size_bytes'] or h.hexdigest() != data['sha256']:
            raise HTTPException(422, 'Length or checksum mismatch')
        f.seek(0)
        if not magic_ok(data['content_type'], f.read(16)):
            raise HTTPException(415, 'File signature does not match content type')
        f.seek(0)
        # Lock spans the bounded storage write to prevent concurrent replacement.
        with transaction(p) as c:
            current = find(c, p, document_id, 'documents', lock=True)['data']
            if current['status'] != 'awaiting_upload':
                raise HTTPException(409, 'Upload already committed')
            blob = storage.Client().bucket(os.environ['QUARANTINE_BUCKET']).blob(f'{p.tenant}/{document_id}')
            # Deterministic object, create-only. If DB commit failed after upload, retry verifies existing object.
            from google.api_core.exceptions import PreconditionFailed
            try:
                blob.upload_from_file(f, size=size, content_type=data['content_type'], if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                existing = blob.download_as_bytes(if_generation_match=int(blob.generation))
                if len(existing) != size or hashlib.sha256(existing).hexdigest() != data['sha256']:
                    raise HTTPException(409, 'Stored upload differs; operator reconciliation required')
            data = current | {'status': 'quarantined', 'generation': str(blob.generation)}
            c.execute(text('UPDATE records SET data=CAST(:d AS jsonb),version=version+1 WHERE id=:id AND tenant_id=:t'),
                {'d': json.dumps(data), 'id': str(document_id), 't': p.tenant})
            audit(c, p, 'document.quarantined', str(document_id))
    return {'document_id': str(document_id), 'status': 'quarantined'}

@app.get('/v1/documents/{document_id}/content', response_class=Response, responses={200:{'description':'Authorised attachment bytes', 'content': {t:{'schema':{'type':'string','format':'binary'}} for t in ['application/pdf','image/jpeg','image/png']}}})
def download_document(document_id: UUID, p: Principal = Depends(principal)):
    require_access(p, VAULT)
    with transaction(p) as c:
        row = find(c, p, document_id, 'documents')
        data = row['data']
        if data['status'] != 'clean':
            raise HTTPException(423, 'Document is not cleared for download')
        blob = storage.Client().bucket(os.environ['CLEAN_BUCKET']).blob(f'{p.tenant}/{document_id}')
        content = blob.download_as_bytes(if_generation_match=int(data['clean_generation']))
        audit(c, p, 'document.downloaded', str(document_id))
    return Response(content, media_type=data['content_type'], headers={'Content-Disposition': 'attachment; filename="document"'})
