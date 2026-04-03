# Enforce Full (Strict) SSL mode — validates the origin certificate from cert-manager.
# Prevents Cloudflare from accepting self-signed or expired certs on the tunnel origin.

resource "cloudflare_zone_setting" "ssl_ekenhome" {
  zone_id    = local.zone_ekenhome
  setting_id = "ssl"
  value      = "strict"
}

resource "cloudflare_zone_setting" "ssl_digitomara" {
  zone_id    = local.zone_digitomara
  setting_id = "ssl"
  value      = "strict"
}

# Always redirect HTTP to HTTPS

resource "cloudflare_zone_setting" "https_ekenhome" {
  zone_id    = local.zone_ekenhome
  setting_id = "always_use_https"
  value      = "on"
}

resource "cloudflare_zone_setting" "https_digitomara" {
  zone_id    = local.zone_digitomara
  setting_id = "always_use_https"
  value      = "on"
}

# Enable HSTS

resource "cloudflare_zone_setting" "hsts_ekenhome" {
  zone_id    = local.zone_ekenhome
  setting_id = "security_header"
  value = jsonencode({
    strict_transport_security = {
      enabled            = true
      max_age            = 31536000
      include_subdomains = true
      preload            = true
      nosniff            = true
    }
  })
}

resource "cloudflare_zone_setting" "hsts_digitomara" {
  zone_id    = local.zone_digitomara
  setting_id = "security_header"
  value = jsonencode({
    strict_transport_security = {
      enabled            = true
      max_age            = 31536000
      include_subdomains = true
      preload            = true
      nosniff            = true
    }
  })
}
