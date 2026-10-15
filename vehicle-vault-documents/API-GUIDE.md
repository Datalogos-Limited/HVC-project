# API contract and integration guide

## Hosts and trust boundaries

Each domain is a separate API origin: `clients.api.example.com`, `vehicles.api.example.com`, `compliance.api.example.com`, and `finance.api.example.com`. Replace these examples with Terraform outputs. Health checks require no token. Sign-up/sign-in are public only on the clients host. All other supplied routes require an Identity Platform ID token in `Authorization: Bearer <ID_TOKEN>`; OAuth access tokens or arbitrary Google IAM tokens are not substitutes.

Organisation, roles and optional client ownership come from approved token claims. There is no `X-Tenant-ID` override. Every resource is scoped on both tenant and client. Non-owned resources return 404. Do not expose internal compliance findings to clients when adding provider/reviewer response models; the starter holds drafts only.

## Implemented endpoint catalogue

| Origin | Method and path | Behaviour |
|---|---|---|
| Clients | `POST /v1/auth/sign-up` | Basic email/password account registration; no vault membership granted |
| Clients | `POST /v1/auth/sign-in` | Basic password sign-in; MFA challenges require the provider SDK |
| Clients | `GET /v1/me` | Approved tenant/role/client binding |
| Clients | `POST /v1/clients` | Relationship manager creates client entity |
| Clients | `GET /v1/clients`, `GET /v1/clients/{id}` | Paginated/scoped client records |
| Vehicles | `POST /v1/assets` | Asset manager creates vehicle record |
| Vehicles | `GET /v1/assets`, `GET /v1/assets/{id}` | Paginated/scoped vehicle records |
| Compliance | `POST /v1/kyc-cases` | Create own or officer-managed draft case |
| Compliance | `GET /v1/kyc-cases`, `GET /v1/kyc-cases/{id}` | Paginated/scoped draft cases |
| Finance | `POST /v1/transactions` | Finance operator creates a transaction draft |
| Finance | `GET /v1/transactions`, `GET /v1/transactions/{id}` | Paginated/scoped drafts |
| Finance | `POST /v1/transactions/{id}/submit` | Returns 501; no execution is enabled |
| All four | `POST /v1/documents` | Create metadata against a parent in the same vault |
| All four | `PUT /v1/documents/{id}/content` | Upload raw bytes; validate and quarantine |
| All four | `GET /v1/documents?parent_id={id}` | Paginated metadata for an authorised parent |
| All four | `GET /v1/documents/{id}` | Read metadata and scan state |
| All four | `GET /v1/documents/{id}/content` | Download clean bytes; returns 423 while blocked |
| All four | `GET /healthz` | Process health only, not a dependency/readiness assertion |

OpenAPI 3.1 contracts generated from the runnable code are `api-clients.json`, `api-vehicles.json`, `api-compliance.json`, and `api-finance.json`. `api-finance-target.json` is a separate design-only contract for escrow, payments, approvals, FX, hedging, bank OAuth, provider webhooks and reconciliation. Its `x-implementation-status: planned` marker means the route does not exist in the starter. `app/providers.py` defines adapter interfaces without simulating provider success.

## Registration and sign-in

```http
POST /v1/auth/sign-up
Content-Type: application/json

{"email":"user@example.com","password":"a-user-chosen-strong-password"}
```

The response includes `id_token`, `refresh_token`, `expires_in` and `user_id`. Treat both tokens as credentials. The ID token alone does not grant access until email verification and operator membership approval. Use Identity Platform SDK email verification/reset/MFA/renewal; do not build your own refresh-token persistence into localStorage. Password sign-in uses the same shape at `/v1/auth/sign-in`. Passwords are not stored by the vault API. A validation error deliberately omits rejected input values.

## Register a client, then a vehicle

Creation requires a stable `Idempotency-Key` of 8–128 alphanumeric, underscore or hyphen characters. The same actor/tenant/route/key with identical content returns the original resource; changed content returns 409. Keep the key across network retries. A new logical operation needs a new key.

```http
POST /v1/clients
Authorization: Bearer <RELATIONSHIP_MANAGER_ID_TOKEN>
Idempotency-Key: create-client-20261015-001
Content-Type: application/json

{"name":"Example Family Trust","entity_type":"trust","domicile":"GB"}
```

The response's `id` and `client_id` are the same client UUID. On the vehicles host:

```http
POST /v1/assets
Authorization: Bearer <ASSET_MANAGER_ID_TOKEN>
Idempotency-Key: register-asset-20261015-001
Content-Type: application/json

{
  "client_id":"<CLIENT_UUID>",
  "make":"Ferrari",
  "model":"F40",
  "year":1989,
  "chassis_number":"ZFFGJ34B000083620",
  "category":"supercar",
  "purchase":{"minor_units":140000000,"currency":"GBP"},
  "valuation":{"minor_units":205000000,"currency":"GBP"},
  "title_status":"unverified",
  "provenance":"Evidence to be reviewed"
}
```

UUID placeholders must be replaced with real values. Amounts are minor units: GBP/USD/EUR/CHF have two decimal places and JPY has zero. No currency conversion is performed by these create/read APIs. A case/draft with client and asset UUIDs does not prove those cross-vault entities exist or that settlement is eligible.

## Add documents

1. Choose the vault that owns the parent: client, vehicle, KYC case or transaction UUID.
2. Compute file SHA-256 and byte length. Create metadata with the correct MIME type.
3. PUT the exact bytes to the returned document UUID's content path on the same origin.
4. Poll metadata until clean/rejected; an authorised operator runs the scanner job in this starter.

```http
POST /v1/documents
Authorization: Bearer <ID_TOKEN>
Idempotency-Key: title-document-20261015-001
Content-Type: application/json

{
  "parent_id":"<VEHICLE_UUID>",
  "filename":"registration.pdf",
  "category":"title",
  "content_type":"application/pdf",
  "size_bytes":12345,
  "sha256":"<64_LOWERCASE_HEX_CHARACTERS>"
}
```

Upload uses raw bytes, **not multipart or base64**:

```http
PUT /v1/documents/<DOCUMENT_UUID>/content
Authorization: Bearer <ID_TOKEN>
Content-Type: application/pdf

<exact file bytes>
```

A successful upload returns 202 with `status: quarantined`. The API accepts PDF/JPEG/PNG up to 20 MiB. It checks signatures but signature checking alone is not malware detection; the scanner must complete. The backend never accepts a browser-provided bucket path, clean flag or object generation. Downloads stream via the authorised API as an attachment; no public document URL is returned.

## Pagination and response shape

List requests accept `limit` (1–100) and `after` (UUID cursor). Results are ordered by UUID, not creation date. This is a live cursor over immutable IDs, not a snapshot: concurrent inserts that sort before the cursor may require a refresh. Use a snapshot/export workflow for formal statements.

```json
{
  "items":[{
    "id":"00000000-0000-0000-0000-000000000001",
    "client_id":"00000000-0000-0000-0000-000000000002",
    "version":1,
    "created_at":"2026-10-15T12:00:00+00:00",
    "data":{}
  }],
  "next_cursor":null
}
```

Typical errors: 401 invalid/revoked token; 403 unapproved role/email/MFA; 404 outside scope; 409 conflicting retry/state; 413 over declared upload length; 415 unsupported/mismatched content; 422 validation/checksum failure; 423 quarantined/rejected download; 501 disabled financial execution; 503 identity dependency unavailable. Do not automatically retry financial commands with a new key. The starter uses FastAPI's `detail` error envelope; the planned finance contract proposes a structured code/message/request-ID error envelope for those future APIs.
