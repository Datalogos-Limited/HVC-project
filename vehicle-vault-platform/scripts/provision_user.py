"""Operator-only: grant approved claims using an authorised workstation identity.
python scripts/provision_user.py UID TENANT_UUID ROLE [CLIENT_UUID]
Never expose this script through an HTTP endpoint.
"""
import os
import sys
from uuid import UUID
import firebase_admin
from firebase_admin import auth

uid, tenant, role, *client = sys.argv[1:]
assert role in {'client','relationship_manager','asset_manager','compliance_officer','finance_operator'}
claims = {'dcb_tenant': str(UUID(tenant)), 'dcb_roles': [role]}
if role == 'client':
    claims['dcb_client'] = str(UUID(client[0]))
firebase_admin.initialize_app(options={'projectId': os.environ['GOOGLE_CLOUD_PROJECT']})
assert auth.get_user(uid).email_verified, 'Verify email before granting membership'
auth.set_custom_user_claims(uid, claims)
auth.revoke_refresh_tokens(uid)
print('Claims replaced and existing sessions revoked. User must sign in again.')
