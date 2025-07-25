terraform {
  required_providers {
    authentik = {
      source  = "goauthentik/authentik"
      version = "2025.6.0"
    }
  }
}

provider "authentik" {
  url   = "https://sso.${var.cluster_domain}"
  token = var.authentik_token
}
