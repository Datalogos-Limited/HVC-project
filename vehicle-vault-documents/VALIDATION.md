# Validation record — 15 October 2026

## Passed locally

- Python 3.12: **38 application/security tests passed**. One PostgreSQL integration test was skipped without its database URL.
- Covered invalid/unapproved claims, email verification requirement, roles and client scope, anonymous denial, staff MFA, revoked/invalid-token verification, domain route separation, client mutation denial, input path/size/type/checksum checks, quarantine download blocking, non-echoing validation errors, document category boundaries and positive transaction amounts.
- Four implementation OpenAPI 3.1 contracts generated directly from the application, plus the separately marked target finance contract: **all five passed `openapi-spec-validator`**.
- Terraform **1.9.8** with signed HashiCorp Google provider **6.50.0**: initialization, formatting and **`terraform validate` passed**. Provider selection/checksums are in `infra/.terraform.lock.hcl`.
- Python source compilation and browser ES module syntax check passed.
- HLD HTML opened in the in-app browser, and the architecture SVG was visually inspected. Editable detailed UML is supplied as PlantUML source; Java/PlantUML compilation was not available locally.
- Frontend deployed HTML/JavaScript was fetched and inspected, with per-file SHA-256 hashes recorded. All three non-duplicate DataVaults PDF texts were read through the Drive connector. AppleDouble `._` files and a duplicate requirements PDF were not treated as separate references.

## Attempted but not executable in this environment

A temporary native PostgreSQL installation was prepared under the working directory to run the real RLS/idempotency integration test. PostgreSQL initialization failed because the desktop sandbox denies its `shmget` shared-memory allocation. No database test success is claimed. The disposable test harness is provided in `tests/test_postgres.py`, and the CI workflow supplies PostgreSQL 16 on Linux to run it. CI has been authored but has not been dispatched from this local folder.

## Not validated here

- An authenticated GCP Terraform plan/apply, IAM policy propagation or live private SQL connections.
- Execution of the SQL schema under Cloud SQL; database RLS, connection-pool scope reset, audit grants and concurrency behaviour in a live database.
- Actual image builds, image vulnerability scans, ClamAV signature refresh/scanning or GCS generation-pinned upload/promotion/download against a real bucket.
- Identity Platform live signup/signin/MFA/revocation; email/reset flows; Netlify account migration.
- Browser application migration and end-to-end operation, load testing, recovery/PITR drills or independent penetration testing.
- KYC provider calls, escrow/payment/FX/hedging execution or banking OAuth/webhook integration; these are not implemented.

Terraform validation confirms provider-supported configuration structure, not provisioning success or production readiness. Unit tests using stubs validate application boundaries, not PostgreSQL/GCP enforcement. Run the deployment acceptance steps before handling real client evidence or money.
