import os
from dataclasses import dataclass
from uuid import UUID
import firebase_admin
from firebase_admin import auth
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)

@dataclass(frozen=True)
class Principal:
    uid: str
    tenant: str
    roles: frozenset[str]
    client_id: str | None

STAFF = {
    'clients': {'relationship_manager'},
    'vehicles': {'asset_manager'},
    'compliance': {'compliance_officer'},
    'finance': {'finance_operator'},
}

def decode_claims(claims: dict) -> Principal:
    try:
        if claims.get('email_verified') is not True:
            raise ValueError('unverified email')
        tenant = str(UUID(claims['dcb_tenant']))
        roles = claims['dcb_roles']
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise ValueError('invalid roles')
        client_id = str(UUID(claims['dcb_client'])) if claims.get('dcb_client') else None
        if 'client' in roles and client_id is None:
            raise ValueError('missing client binding')
        return Principal(claims['uid'], tenant, frozenset(roles), client_id)
    except (KeyError, ValueError, TypeError):
        raise HTTPException(403, 'Verified account and approved organisation membership required')

def principal(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> Principal:
    if not credentials or credentials.scheme.lower() != 'bearer':
        raise HTTPException(401, 'Bearer token required', headers={'WWW-Authenticate': 'Bearer'})
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={'projectId': os.environ['GOOGLE_CLOUD_PROJECT']})
    try:
        claims = auth.verify_id_token(credentials.credentials, check_revoked=True)
    except auth.CertificateFetchError:
        raise HTTPException(503, 'Identity verification temporarily unavailable')
    except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError, auth.UserDisabledError, auth.UserNotFoundError, ValueError):
        raise HTTPException(401, 'Invalid or revoked token')
    p = decode_claims(claims)
    staff_roles = set().union(*STAFF.values())
    if p.roles & staff_roles and not claims.get('firebase', {}).get('sign_in_second_factor'):
        raise HTTPException(403, 'Staff multi-factor authentication required')
    return p

def is_staff(p: Principal, vault: str) -> bool:
    return bool(p.roles & STAFF[vault])

def require_access(p: Principal, vault: str, write: bool = False):
    if is_staff(p, vault):
        return
    if 'client' in p.roles and p.client_id and (not write or vault == 'compliance'):
        return
    raise HTTPException(403, 'Role does not permit this operation')

def scope(p: Principal, vault: str, client_id: str):
    require_access(p, vault)
    if not is_staff(p, vault) and p.client_id != client_id:
        raise HTTPException(404, 'Resource not found')
