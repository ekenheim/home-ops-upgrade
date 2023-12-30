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

    minio = {
      source = "aminueza/minio"
      version = "2.0.1"
    }
  }
}

module "secrets_s3" {
  source = "./modules/get-secret"
  id     = "b01f6ec7-aeda-4c0a-a82e-b0e700c61800"
}
