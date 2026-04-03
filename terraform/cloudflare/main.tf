terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = ">= 5.0.0"
    }
  }
}

data "cloudflare_zones" "ekenhome" {
  filter = {
    name = "ekenhome.se"
  }
}

data "cloudflare_zones" "digitomara" {
  filter = {
    name = "digitomara.com"
  }
}

locals {
  zone_ekenhome   = data.cloudflare_zones.ekenhome.result[0].id
  zone_digitomara = data.cloudflare_zones.digitomara.result[0].id
}
