# Cache static assets at the Cloudflare edge for public-facing sites.
# Reduces origin load and improves latency for digitomara.com, blog, and media.

resource "cloudflare_ruleset" "cache_ekenhome" {
  zone_id = local.zone_ekenhome
  name    = "Cache rules"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules = [
    {
      action = "set_cache_settings"
      action_parameters = {
        browser_ttl = {
          mode    = "override_origin"
          default = 86400 # 1 day
        }
        edge_ttl = {
          mode    = "override_origin"
          default = 604800 # 7 days
        }
        cache = true
      }
      expression  = "(http.host eq \"blog.ekenhome.se\" and http.request.uri.path.extension in {\"js\" \"css\" \"png\" \"jpg\" \"jpeg\" \"gif\" \"webp\" \"svg\" \"ico\" \"woff\" \"woff2\" \"ttf\" \"eot\"})"
      description = "Cache blog static assets"
      enabled     = true
    },
    {
      action = "set_cache_settings"
      action_parameters = {
        browser_ttl = {
          mode    = "override_origin"
          default = 86400
        }
        edge_ttl = {
          mode    = "override_origin"
          default = 604800
        }
        cache = true
      }
      expression  = "(http.host in {\"hempriser.ekenhome.se\" \"comics.ekenhome.se\" \"join.ekenhome.se\"} and http.request.uri.path.extension in {\"js\" \"css\" \"png\" \"jpg\" \"jpeg\" \"gif\" \"webp\" \"svg\" \"ico\" \"woff\" \"woff2\" \"ttf\" \"eot\"})"
      description = "Cache public site static assets"
      enabled     = true
    }
  ]
}

resource "cloudflare_ruleset" "cache_digitomara" {
  zone_id = local.zone_digitomara
  name    = "Cache rules"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules = [
    {
      action = "set_cache_settings"
      action_parameters = {
        browser_ttl = {
          mode    = "override_origin"
          default = 86400
        }
        edge_ttl = {
          mode    = "override_origin"
          default = 604800
        }
        cache = true
      }
      expression  = "(http.request.uri.path.extension in {\"js\" \"css\" \"png\" \"jpg\" \"jpeg\" \"gif\" \"webp\" \"svg\" \"ico\" \"woff\" \"woff2\" \"ttf\" \"eot\" \"json\"})"
      description = "Cache marketing site static assets"
      enabled     = true
    }
  ]
}

# Redirect www.digitomara.com to digitomara.com at the edge (faster than ingress)

resource "cloudflare_ruleset" "redirect_digitomara" {
  zone_id = local.zone_digitomara
  name    = "Redirect rules"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [
    {
      action = "redirect"
      action_parameters = {
        from_value = {
          status_code = 301
          target_url = {
            expression = "concat(\"https://digitomara.com\", http.request.uri.path)"
          }
          preserve_query_string = true
        }
      }
      expression  = "(http.host eq \"www.digitomara.com\")"
      description = "Redirect www to apex"
      enabled     = true
    }
  ]
}
