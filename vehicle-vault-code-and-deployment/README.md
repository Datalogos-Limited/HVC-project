# Dream Car Brokers — GCP vault platform starter

Prepared from the supplied deployed frontend and DataVaults research PDFs, 15 October 2026.

**Start with [the HLD](docs/HLD.md)** or open `docs/HLD.html` for a readable report. This is an engineering foundation and target design, not a production banking/escrow system. No GCP deployment or live-site change has been performed.

## Contents

- `docs/HLD.md` / `docs/HLD.html` — architecture, four-vault boundaries, identity/IAM, processes, states, operating targets and delivery gaps.
- `docs/uml/` — five editable PlantUML diagrams: components, domain model, sign-up/upload sequence, finance activity, document state.
- `docs/architecture.svg`, `docs/domain-model.svg` — readable overview diagrams.
- `docs/FRONTEND-REVIEW.md` — deployed code findings and integration mapping.
- `docs/API-GUIDE.md`, `docs/api-*.json` — implemented OpenAPI contracts and a separately marked future finance contract.
- `app/` — Python/FastAPI core APIs, authorisation, private SQL connector, quarantine workflow and scanner job.
- `frontend/api-client.js` — asynchronous browser API helper; the live frontend is not patched.
- `scripts/schema.sql` — initial PostgreSQL schema, RLS and least-privilege grants.
- `infra/` — Terraform for four vaults, private SQL, document buckets/KMS, IAM, Cloud Run, scanner jobs and HTTPS edge.
- `docs/DEPLOYMENT.md` — build/bootstrap/migration/deployment/acceptance runbook.
- `tests/`, `.github/workflows/verify.yml` — security tests and PostgreSQL integration/CI.
- `docs/VALIDATION.md` — checks performed and checks still requiring GCP/provider environments.

## Implemented

Password sign-up/sign-in; verified account and approved claim checks; staff MFA; client/vehicle/KYC-draft/transaction-draft creation and scoped reads; stable mutation retry protection; file upload with size/type/checksum checks; quarantine; manually dispatched malware scan; clean-only authorised downloads; audit inserts; GCP infrastructure configuration.

## Planned

KYC screening/decisions; beneficial-owner models; cross-vault existence/status projections; automatic scan dispatch; formal statements; edits/archive/retention/deletion; delegated sharing; escrow, payment, FX/hedge and banking adapters; immutable external audit sink and operating acceptance. Finance execution intentionally returns 501.

## Local checks

Use Python 3.12:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

For real PostgreSQL integration, install `psycopg[binary]==3.2.9`, set `TEST_DATABASE_URL` to a **fresh disposable** PostgreSQL database under a bootstrap superuser, and rerun tests. The integration fixture creates its own runtime roles and schema. It is destructive to an empty test database's namespace and must never point at a real vault. CI supplies PostgreSQL 16 automatically. Tests skip this integration when the URL is unset.

The application defaults to the vehicles vault for OpenAPI/local inspection. Running data endpoints needs IAM-enabled Cloud SQL and configured Identity Platform; there is no development auth bypass or SQLite replacement. Set environment values from the deployment runbook before starting `uvicorn app.main:app`.

Generate one live OpenAPI specification per process with `VAULT=vehicles python -m scripts.export_openapi` (repeat for clients/compliance/finance). For GCP rollout, follow `docs/DEPLOYMENT.md` in order.
