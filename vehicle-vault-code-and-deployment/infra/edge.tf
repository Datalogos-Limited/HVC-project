resource "google_compute_security_policy" "edge" {
  name = "dcb-vault-edge"
  rule {
    action   = "throttle"
    priority = 1000
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 100
        interval_sec = 60
      }
    }
  }
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
  }
}
resource "google_compute_region_network_endpoint_group" "vault" {
  for_each              = local.vaults
  name                  = "dcb-${each.key}"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run { service = google_cloud_run_v2_service.vault[each.key].name }
}
resource "google_compute_backend_service" "vault" {
  for_each              = local.vaults
  name                  = "dcb-${each.key}"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTP"
  security_policy       = google_compute_security_policy.edge.id
  backend { group = google_compute_region_network_endpoint_group.vault[each.key].id }
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}
resource "google_compute_url_map" "api" {
  name            = "dcb-vaults"
  default_service = google_compute_backend_service.vault["clients"].id
  dynamic "host_rule" {
    for_each = local.vaults
    content {
      hosts        = ["${host_rule.value}.${var.api_domain}"]
      path_matcher = host_rule.value
    }
  }
  dynamic "path_matcher" {
    for_each = local.vaults
    content {
      name            = path_matcher.value
      default_service = google_compute_backend_service.vault[path_matcher.value].id
    }
  }
}
resource "google_compute_global_address" "edge" { name = "dcb-api-ip" }
resource "google_compute_managed_ssl_certificate" "api" {
  name = "dcb-api-cert"
  managed { domains = [for v in local.vaults : "${v}.${var.api_domain}"] }
}
resource "google_compute_target_https_proxy" "api" {
  name             = "dcb-api-https"
  url_map          = google_compute_url_map.api.id
  ssl_certificates = [google_compute_managed_ssl_certificate.api.id]
}
resource "google_compute_global_forwarding_rule" "api" {
  name                  = "dcb-api-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.edge.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.api.id
}
output "api_ip" { value = google_compute_global_address.edge.address }
output "api_hosts" { value = { for v in local.vaults : v => "https://${v}.${var.api_domain}" } }
output "database_instances" { value = { for v in local.vaults : v => google_sql_database_instance.vault[v].connection_name } }
output "iam_database_users" { value = { for v in local.vaults : v => google_sql_user.runtime[v].name } }
