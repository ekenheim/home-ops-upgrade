terraform {
  required_providers {
    authentik = {
      source  = "goauthentik/authentik"
      version = "2024.10.2"
    }
  }
}

provider "authentik" {
  url   = "https://sso.${var.cluster_domain}"
  token = var.authentik_token
}
