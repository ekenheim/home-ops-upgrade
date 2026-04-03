# Enable the Cloudflare managed WAF ruleset on both zones.
# This applies OWASP and Cloudflare-managed rules to block
# SQLi, XSS, RCE, path traversal, and other common attacks.

resource "cloudflare_ruleset" "waf_ekenhome" {
  zone_id = local.zone_ekenhome
  name    = "WAF managed rules"
  kind    = "zone"
  phase   = "http_request_firewall_managed"

  rules = [
    {
      action = "execute"
      action_parameters = {
        id = "efb7b8c949ac4650a09736fc376e9aee" # Cloudflare Managed Ruleset
      }
      expression  = "true"
      description = "Cloudflare Managed Ruleset"
      enabled     = true
    },
    {
      action = "execute"
      action_parameters = {
        id = "4814384a9e5d4991b9815dcfc25d2f1f" # Cloudflare OWASP Core Ruleset
      }
      expression  = "true"
      description = "Cloudflare OWASP Core Ruleset"
      enabled     = true
    }
  ]
}

resource "cloudflare_ruleset" "waf_digitomara" {
  zone_id = local.zone_digitomara
  name    = "WAF managed rules"
  kind    = "zone"
  phase   = "http_request_firewall_managed"

  rules = [
    {
      action = "execute"
      action_parameters = {
        id = "efb7b8c949ac4650a09736fc376e9aee"
      }
      expression  = "true"
      description = "Cloudflare Managed Ruleset"
      enabled     = true
    },
    {
      action = "execute"
      action_parameters = {
        id = "4814384a9e5d4991b9815dcfc25d2f1f"
      }
      expression  = "true"
      description = "Cloudflare OWASP Core Ruleset"
      enabled     = true
    }
  ]
}
