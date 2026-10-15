locals {
  vaults   = toset(["clients", "vehicles", "compliance", "finance"])
  services = toset(["run.googleapis.com", "compute.googleapis.com", "sqladmin.googleapis.com", "servicenetworking.googleapis.com", "cloudkms.googleapis.com", "storage.googleapis.com", "identitytoolkit.googleapis.com", "secretmanager.googleapis.com", "artifactregistry.googleapis.com"])
}
resource "google_project_service" "api" {
  for_each           = local.services
  service            = each.key
  disable_on_destroy = false
}
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "dcb-vaults"
  format        = "DOCKER"
  depends_on    = [google_project_service.api]
}
resource "google_compute_network" "vault" {
  name                    = "dcb-vault-network"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.api]
}
resource "google_compute_subnetwork" "run" {
  name                     = "dcb-run"
  region                   = var.region
  network                  = google_compute_network.vault.id
  ip_cidr_range            = "10.20.0.0/24"
  private_ip_google_access = true
}
resource "google_compute_global_address" "sql_range" {
  name          = "dcb-sql-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.vault.id
}
resource "google_service_networking_connection" "private" {
  network                 = google_compute_network.vault.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_range.name]
}
resource "google_kms_key_ring" "vault" {
  name       = "dcb-vaults"
  location   = var.region
  depends_on = [google_project_service.api]
}
resource "google_kms_crypto_key" "vault" {
  for_each        = local.vaults
  name            = each.key
  key_ring        = google_kms_key_ring.vault.id
  rotation_period = "7776000s"
  lifecycle { prevent_destroy = true }
}
resource "google_service_account" "runtime" {
  for_each   = local.vaults
  account_id = "dcb-${each.key}"
}
resource "google_service_account" "scanner" {
  for_each   = local.vaults
  account_id = "dcb-scan-${each.key}"
}
data "google_storage_project_service_account" "storage" {
  project    = var.project_id
  depends_on = [google_project_service.api]
}
resource "google_kms_crypto_key_iam_member" "storage" {
  for_each      = local.vaults
  crypto_key_id = google_kms_crypto_key.vault[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.storage.email_address}"
}
locals {
  buckets = { for pair in setproduct(local.vaults, ["quarantine", "clean"]) : "${pair[0]}-${pair[1]}" => { vault = pair[0], stage = pair[1] } }
}
resource "google_storage_bucket" "documents" {
  for_each                    = local.buckets
  name                        = "${var.project_id}-dcb-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  encryption { default_kms_key_name = google_kms_crypto_key.vault[each.value.vault].id }
  depends_on = [google_kms_crypto_key_iam_member.storage]
}
resource "google_storage_bucket_iam_member" "runtime_quarantine" {
  for_each = local.vaults
  bucket   = google_storage_bucket.documents["${each.key}-quarantine"].name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_storage_bucket_iam_member" "runtime_clean" {
  for_each = local.vaults
  bucket   = google_storage_bucket.documents["${each.key}-clean"].name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_storage_bucket_iam_member" "scan_quarantine" {
  for_each = local.vaults
  bucket   = google_storage_bucket.documents["${each.key}-quarantine"].name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.scanner[each.key].email}"
}
resource "google_storage_bucket_iam_member" "scan_clean" {
  for_each = local.vaults
  bucket   = google_storage_bucket.documents["${each.key}-clean"].name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.scanner[each.key].email}"
}
resource "google_sql_database_instance" "vault" {
  for_each            = local.vaults
  name                = "dcb-${each.key}"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true
  settings {
    tier              = "db-custom-2-7680"
    availability_type = "REGIONAL"
    disk_autoresize   = true
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vault.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }
  }
  depends_on = [google_service_networking_connection.private]
}
resource "google_sql_database" "vault" {
  for_each = local.vaults
  name     = "vault"
  instance = google_sql_database_instance.vault[each.key].name
}
resource "google_sql_user" "runtime" {
  for_each = local.vaults
  name     = trimsuffix(google_service_account.runtime[each.key].email, ".gserviceaccount.com")
  instance = google_sql_database_instance.vault[each.key].name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
resource "google_sql_user" "scanner" {
  for_each = local.vaults
  name     = trimsuffix(google_service_account.scanner[each.key].email, ".gserviceaccount.com")
  instance = google_sql_database_instance.vault[each.key].name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
locals {
  identities = merge({ for v in local.vaults : "api-${v}" => google_service_account.runtime[v].email }, { for v in local.vaults : "scan-${v}" => google_service_account.scanner[v].email })
  sql_roles  = { for x in setproduct(keys(local.identities), ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]) : "${x[0]}-${x[1]}" => { identity = x[0], role = x[1] } }
}
resource "google_project_iam_member" "sql" {
  for_each = local.sql_roles
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${local.identities[each.value.identity]}"
}
resource "google_project_iam_custom_role" "verify_users" {
  role_id     = "dcbVerifyIdentity"
  title       = "DCB verify identity revocation"
  permissions = ["firebaseauth.users.get"]
}
resource "google_project_iam_member" "verify" {
  for_each = local.vaults
  project  = var.project_id
  role     = google_project_iam_custom_role.verify_users.name
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_secret_manager_secret" "identity_key" {
  secret_id = "dcb-identity-web-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.api]
}
resource "google_secret_manager_secret_iam_member" "identity_key" {
  secret_id = google_secret_manager_secret.identity_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["clients"].email}"
}
# Identity Platform is configured on the project; membership is provisioned separately.
resource "google_identity_platform_config" "identity" {
  project = var.project_id
  sign_in {
    allow_duplicate_emails = false
    email {
      enabled           = true
      password_required = true
    }
  }
  mfa {
    state             = "ENABLED"
    enabled_providers = ["PHONE_SMS"]
  }
  authorized_domains = [trimprefix(var.frontend_origin, "https://")]
  depends_on         = [google_project_service.api]
}
resource "google_cloud_run_v2_service" "vault" {
  for_each = local.vaults
  name     = "dcb-${each.key}"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  template {
    service_account                  = google_service_account.runtime[each.key].email
    max_instance_request_concurrency = 4
    scaling { max_instance_count = 5 }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.vault.name
        subnetwork = google_compute_subnetwork.run.name
      }
    }
    containers {
      image = var.image
      resources { limits = { cpu = "1", memory = "1Gi" } }
      dynamic "env" {
        for_each = {
          VAULT                    = each.key, GOOGLE_CLOUD_PROJECT = var.project_id,
          INSTANCE_CONNECTION_NAME = google_sql_database_instance.vault[each.key].connection_name,
          DB_IAM_USER              = google_sql_user.runtime[each.key].name,
          FRONTEND_ORIGIN          = var.frontend_origin,
          QUARANTINE_BUCKET        = google_storage_bucket.documents["${each.key}-quarantine"].name,
          CLEAN_BUCKET             = google_storage_bucket.documents["${each.key}-clean"].name
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.key == "clients" ? [1] : []
        content {
          name = "IDENTITY_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.identity_key.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_project_iam_member.sql, google_project_iam_member.verify, google_secret_manager_secret_iam_member.identity_key]
}
# The load balancer admits requests; the API verifies end-user Identity Platform tokens.
# Identity Platform tokens are NOT Cloud Run IAM invoker tokens.
resource "google_cloud_run_v2_service_iam_member" "edge" {
  for_each = local.vaults
  name     = google_cloud_run_v2_service.vault[each.key].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
resource "google_cloud_run_v2_job" "scan" {
  for_each = local.vaults
  name     = "dcb-scan-${each.key}"
  location = var.region
  template {
    template {
      service_account = google_service_account.scanner[each.key].email
      max_retries     = 2
      timeout         = "600s"
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.vault.name
          subnetwork = google_compute_subnetwork.run.name
        }
      }
      containers {
        image = var.scanner_image
        resources { limits = { cpu = "2", memory = "2Gi" } }
        dynamic "env" {
          for_each = {
            VAULT                    = each.key, GOOGLE_CLOUD_PROJECT = var.project_id,
            INSTANCE_CONNECTION_NAME = google_sql_database_instance.vault[each.key].connection_name,
            DB_IAM_USER              = google_sql_user.scanner[each.key].name,
            QUARANTINE_BUCKET        = google_storage_bucket.documents["${each.key}-quarantine"].name,
            CLEAN_BUCKET             = google_storage_bucket.documents["${each.key}-clean"].name
          }
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
  depends_on = [google_project_iam_member.sql]
}
