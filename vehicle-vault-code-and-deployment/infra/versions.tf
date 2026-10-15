terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}
provider "google" {
  project = var.project_id
  region  = var.region
}
variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "europe-west2"
}
variable "frontend_origin" {
  type = string
  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+$", var.frontend_origin))
    error_message = "Use an HTTPS origin without a path or trailing slash."
  }
}
variable "api_domain" {
  type        = string
  description = "Base domain; API hosts become clients.<domain>, vehicles.<domain>, compliance.<domain>, finance.<domain>."
}
variable "image" {
  type        = string
  description = "Built API image URI pinned by sha256 digest."
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Pin a container digest."
  }
}
variable "scanner_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.scanner_image))
    error_message = "Pin a scanner image digest."
  }
}
