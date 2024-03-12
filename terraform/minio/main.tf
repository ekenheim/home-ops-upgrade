locals {
  buckets = [
    "loki",
    "longhorn",
    "postgresql",
    "thanos",
    "volsync"
  ]
}

terraform {
  required_providers {
    bitwarden = {
      source  = "maxlaverse/bitwarden"
      version = ">= 0.6.0"
    }

    sops = {
      source  = "carlpett/sops"
      version = "1.0.0"
    }

    minio = {
      source  = "aminueza/minio"
      version = "2.2.0"
    }
  }
}

data "sops_file" "bw_secrets" {
  source_file = "secret.sops.yaml"
}

module "secrets_s3" {
  source = "./modules/get-secret"
  id     = "b01f6ec7-aeda-4c0a-a82e-b0e700c61800"
}
