"""Manual Cloud Run job: python -m app.scan TENANT_UUID DOCUMENT_UUID.
No public clean-status API. Run with dedicated scanner service account.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID
from google.cloud import storage
from sqlalchemy import text
from .db import transaction
from .security import Principal
from .main import audit, find, magic_ok


def scan(tenant, document_id):
    tenant, document_id = str(UUID(tenant)), str(UUID(document_id))
    role = {'clients':'relationship_manager','vehicles':'asset_manager','compliance':'compliance_officer','finance':'finance_operator'}[os.environ['VAULT']]
    p = Principal('scanner', tenant, frozenset({role}), None)
    with transaction(p) as c:
        data = find(c, p, document_id, 'documents')['data']
    if data['status'] == 'clean':
        return
    if data['status'] != 'quarantined':
        raise RuntimeError('Document must be quarantined')
    store = storage.Client()
    name = f'{tenant}/{document_id}'
    source = store.bucket(os.environ['QUARANTINE_BUCKET']).blob(name, generation=int(data['generation']))
    source.reload()
    if source.size != data['size_bytes'] or source.size > 20*1024*1024:
        raise RuntimeError('Object length mismatch')
    # Fail closed if definitions are absent or older than 48 hours.
    definitions = list(Path('/var/lib/clamav').glob('daily.c[lv]d'))
    import time
    if not definitions or max(x.stat().st_mtime for x in definitions) < time.time()-48*3600:
        raise RuntimeError('Antivirus definitions unavailable or stale')
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)/'document'
        source.download_to_filename(str(path), if_generation_match=int(data['generation']))
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != data['sha256'] or not magic_ok(data['content_type'], content[:16]):
            raise RuntimeError('Object content mismatch')
        result = subprocess.run(['clamscan', '--no-summary', '--alert-exceeds-max=yes', '--max-filesize=21M',
            '--max-scansize=100M', '--max-recursion=10', str(path)], capture_output=True, timeout=120)
        if result.returncode not in (0, 1):
            raise RuntimeError('Scanner failed; document remains quarantined')
        status = 'clean' if result.returncode == 0 else 'rejected'
        generation = None
        if status == 'clean':
            target = store.bucket(os.environ['CLEAN_BUCKET']).blob(name)
            from google.api_core.exceptions import PreconditionFailed
            try:
                target.upload_from_filename(str(path), content_type=data['content_type'], if_generation_match=0)
            except PreconditionFailed:
                target.reload()
                existing = target.download_as_bytes(if_generation_match=int(target.generation))
                if hashlib.sha256(existing).hexdigest() != data['sha256']:
                    raise RuntimeError('Clean object mismatch')
            generation = str(target.generation)
        with transaction(p) as c:
            current = find(c, p, document_id, 'documents', lock=True)['data']
            if current['status'] != 'quarantined' or current['generation'] != data['generation']:
                raise RuntimeError('Document state changed')
            current |= {'status': status, 'clean_generation': generation}
            c.execute(text('UPDATE records SET data=CAST(:d AS jsonb),version=version+1 WHERE tenant_id=:t AND id=:id'),
                {'d': json.dumps(current), 't': tenant, 'id': document_id})
            audit(c, p, f'document.{status}', document_id)

if __name__ == '__main__':
    scan(*sys.argv[1:])
