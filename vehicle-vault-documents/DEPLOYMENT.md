# GCP deployment and acceptance runbook

This prepares a development/staging environment. No resources were deployed during this handover. Production enablement depends on the HLD readiness matrix. Commands below are examples for an authorised project operator; substitute project/domain values and review the resulting plan. Four regional HA Cloud SQL instances incur fixed charges.

## Prerequisites

- GCP project with billing, an approved region/residency decision, and permissions to manage services, IAM, SQL, networking, KMS, buckets, Secret Manager, Identity Platform, Cloud Run, Artifact Registry and the HTTPS load balancer.
- Terraform 1.9 or later below 2.0, Google Cloud CLI, Python 3.12 for local checks; Docker is optional if using Cloud Build.
- Ownership of the frontend and four API subdomains. Keep development/staging/production in separate projects and separate remote Terraform state.
- A private-network database administration path: a managed admin VM or migration runner inside the VPC. Ordinary Cloud Shell without VPC connectivity cannot reach these private SQL instances.

## 1. Build immutable images

From the repository root, authenticate as an operator and set a task-specific project variable:

```sh
export DCB_PROJECT='your-project-id'
export DCB_REGION='europe-west2'
gcloud config set project "$DCB_PROJECT"
gcloud auth application-default login
gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com
```

For the first bootstrap, create the artifact repository through the CLI, then import it into Terraform after building images and preparing variables:

```sh
gcloud artifacts repositories create dcb-vaults --repository-format=docker --location="$DCB_REGION"
gcloud builds submit --config=cloudbuild.yaml --substitutions="_REGION=$DCB_REGION" .
```

Use the build's actual image digests, not a mutable tag, in `infra/terraform.tfvars` copied from the example. Replace both digest placeholders. The scanner refreshes definitions before every scan and refuses stale definitions. Its job must have outbound access to definition mirrors; an egress-restricted deployment needs an approved internal mirror instead.

The Cloud Build execution identity needs permission to write to this repository and to write build logs; grant these narrowly under your organisation's build policy. No runtime service account needs build/deployment permissions. For production, use CI workload identity federation, review pinned base images/dependencies, scan images and enforce an approved release policy.

## 2. Initialise and bootstrap identity configuration

```sh
terraform -chdir=infra init
terraform -chdir=infra import google_artifact_registry_repository.images "projects/$DCB_PROJECT/locations/$DCB_REGION/repositories/dcb-vaults"
terraform -chdir=infra apply -target=google_secret_manager_secret.identity_key -target=google_identity_platform_config.identity
```

This limited targeted apply is a first-time bootstrap to create the identity configuration and secret container before a service references its secret version. It is not the normal ongoing deployment flow. If Identity Platform already exists, inspect and import its config instead of creating a second configuration. The default Terraform backend is local; configure a protected remote GCS backend with restricted access/versioning before shared or production use. Keep state and `.tfvars` out of source control.

Create or select the project's Identity Platform web API key, restrict it to the Identity Toolkit API and set quotas. Supply it to the secret using Secret Manager's console or a secure stdin workflow; do not commit the key or place it in Terraform variables/state. The secret resource is `dcb-identity-web-api-key`; add its first enabled secret version before the next step.

Register the frontend with the appropriate Identity Platform/Firebase web configuration. Configure verified-email templates, recovery links, authorised domains, MFA enrollment and SMS regional/abuse controls. Terraform enables password auth and MFA availability; it does not build the user-facing enrollment/recovery pages. Staff role access fails until an MFA-backed token is presented. Consider federated workforce sign-in for staff as a subsequent policy decision.

## 3. Review and provision infrastructure

```sh
terraform -chdir=infra plan -out=deployment.plan
terraform -chdir=infra apply deployment.plan
terraform -chdir=infra output
```

This provisions four Cloud Run APIs, four SQL instances/databases/IAM users, eight private versioned document buckets, four GCS CMEK keys, scanner jobs, private network and SQL peering, Secret Manager permissions, the load balancer, certificate and Cloud Armor throttle policy. The first API revisions can exist before schema migration, but data endpoints will fail until migrations are complete. Do not direct real users to them yet.

Create DNS A records for all four `api_hosts` pointing to `api_ip`. Wait until the managed certificate becomes active. HTTPS uses port 443 only; no HTTP listener is configured. Set the frontend origin exactly (HTTPS origin with no path or trailing slash) in Terraform, and confirm CORS accepts that origin only. Enable required GCP Data Access audit logs and a separate immutable security log sink according to the HLD; they are not provisioned by this baseline.

Cloud Run public invoker permission is combined with restricted ingress and application token verification. An organisation policy that forbids `allUsers` requires an alternative edge identity arrangement before applying; do not weaken token checks to work around it.

## 4. Bootstrap each PostgreSQL schema

Terraform creates each IAM login but does not execute SQL. Use a separately authorised migration identity with schema ownership from inside the VPC; never give table ownership or `cloudsqlsuperuser` to runtime/scanner identities. Bootstrap the migration login and database privileges through your controlled database administration procedure.

Using the Cloud SQL Auth Proxy with private IP and IAM automatic authentication is one supported path. The proxy's Google identity must be the approved migration identity, and it must be able to reach the VPC. For each instance, run `scripts/schema.sql` **once** against database `vault`, with the exact runtime and scanner PostgreSQL usernames supplied to psql:

```sh
psql "$DCB_MIGRATION_CONNECTION" -v ON_ERROR_STOP=1 \
  -v runtime_user='dcb-vehicles@YOUR_PROJECT_ID.iam' \
  -v scanner_user='dcb-scan-vehicles@YOUR_PROJECT_ID.iam' \
  -f scripts/schema.sql
```

Use the actual Terraform-generated user names. Repeat for clients, compliance and finance with their corresponding users and instances. The migration is a version-1 bootstrap, not a rerunnable migration engine. Future upgrades need versioned migrations and a rollback strategy; do not re-run CREATE TABLE statements on a live schema. Cloud SQL IAM service-account usernames omit `.gserviceaccount.com`, as the Terraform output does.

## 5. Verify identity and core data flows

Set `GOOGLE_CLOUD_PROJECT` for the privileged operator script, create/verify a test login, and provision approved membership. A relationship manager creates a client; an asset manager creates its vehicle. Bind a client login to that UUID only after relationship approval:

```sh
python scripts/provision_user.py USER_UID TENANT_UUID client CLIENT_UUID
```

The operator must have Identity Platform user administration permission. Runtime services have only user-read permission for token revocation/disabled-user checks. The script replaces claims and revokes existing sessions, so the test user must sign in again. Log and approve these operator changes through your administration process. Never share an operator identity with the application.

Test 401 without a token; 403 for unapproved membership and staff without MFA; 404 for another client's resource; repeated create with the same key; 409 with changed content under that key. Test pagination, exact CORS origin and rejection of oversized/spoofed/checksum-mismatched uploads.

## 6. Scan uploaded documents

Upload a small benign PDF and confirm the record is quarantined and download returns 423. The baseline uses manual scanner dispatch:

```sh
gcloud run jobs execute dcb-scan-vehicles --region="$DCB_REGION" --args="TENANT_UUID,DOCUMENT_UUID" --wait
```

The operator needs job execution permission; the job uses its own scanner identity. Its entrypoint refreshes AV definitions then runs `python -m app.scan` with the two UUIDs. Confirm a clean generation is recorded and authorised download succeeds. Test malware and unavailable/stale definitions: no download must succeed. Validate the actual image's memory, definition mirror access and exit codes in staging. Automatic dispatch, pending scan alerts and dead-letter/retry management are not yet implemented.

## 7. Acceptance and release gates

The package's `VALIDATION.md` records local checks. Before production, run authenticated Terraform plan/apply, private SQL IAM connection tests, GCS/KMS access tests, scanner image integration, direct-run.app ingress rejection, revoked-token checks, restore drills and browser end-to-end testing. Verify a domain identity cannot log in to another vault's SQL instance or read its buckets. RLS is not a substitute for that IAM isolation.

Finance submit must continue returning 501 until real provider sandbox certification, ledger invariants, webhook signature/replay tests, reconciliation, eligibility checks and maker/checker approvals are implemented and reviewed. No production funds should depend on prototype state. Add CI image builds/scans and a deployment promotion workflow after choosing the hosting project and release policy; the supplied CI verifies application/database behaviour and Terraform configuration only.

## Rollback and recovery

Keep prior image digests and route traffic back to the previous revision for reversible API regressions. Database migrations must be backward-compatible; revision rollback does not reverse schema/data changes. Use SQL PITR into a separate recovery instance and verify reconciliation before switching. Document object generations support recovery but do not replace independent backup and deletion policy. CMEK keys have Terraform `prevent_destroy`; database deletion protection and `force_destroy=false` protect core stores. Never disable these protections to clear a failed deployment without a separate recovery/deletion decision.
