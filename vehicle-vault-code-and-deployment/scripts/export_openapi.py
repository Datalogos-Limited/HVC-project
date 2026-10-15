"""Run from repository root, one fresh process per vault."""
import json
import os
from pathlib import Path
from app.main import app, VAULT
schema = app.openapi()
schema['servers'] = [{'url': f'https://{VAULT}.api.example.com', 'description':'Replace with the deployed host'}]
schema['info']['description'] = 'Implemented starter endpoints. Finance execution returns 501. See HLD for production gaps.'
for path, operations in schema['paths'].items():
    for verb, op in operations.items():
        if isinstance(op,dict) and verb in {'get','post','put'}:
            op['x-implementation-status'] = 'blocked' if path.endswith('/submit') else 'implemented'
            for code,description in [('401','Missing, expired, revoked or invalid token'),('403','Unapproved membership, role or staff MFA'),('404','Resource not found in permitted scope'),('409','Conflicting retry or state'),('423','Document not cleared'),('503','Dependency unavailable')]:
                op.setdefault('responses',{}).setdefault(code,{'description':description})
Path(f'docs/api-{VAULT}.json').write_text(json.dumps(schema,indent=2)+'\n')
