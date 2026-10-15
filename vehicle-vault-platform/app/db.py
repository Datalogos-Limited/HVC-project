import os
from contextlib import contextmanager
from functools import lru_cache
from sqlalchemy import create_engine, text
from google.cloud.sql.connector import Connector, IPTypes
from .security import Principal, is_staff

@lru_cache
def engine():
    connector = Connector(refresh_strategy='LAZY')
    def connect():
        return connector.connect(os.environ['INSTANCE_CONNECTION_NAME'], 'pg8000',
            user=os.environ['DB_IAM_USER'], db='vault', enable_iam_auth=True, ip_type=IPTypes.PRIVATE)
    return create_engine('postgresql+pg8000://', creator=connect, pool_size=3, max_overflow=2, pool_pre_ping=True)

@contextmanager
def transaction(p: Principal):
    with engine().begin() as c:
        c.execute(text("SELECT set_config('app.tenant', :tenant, true), set_config('app.client', :client, true), set_config('app.staff', :staff, true), set_config('app.actor', :actor, true)"),
            {'actor': p.uid, 'tenant': p.tenant, 'client': p.client_id or '', 'staff': str(is_staff(p, os.environ['VAULT'])).lower()})
        yield c
