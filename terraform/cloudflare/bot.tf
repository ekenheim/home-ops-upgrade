# Enable Bot Fight Mode to challenge automated/malicious bot traffic.

resource "cloudflare_bot_management" "ekenhome" {
  zone_id    = local.zone_ekenhome
  fight_mode = true
}

resource "cloudflare_bot_management" "digitomara" {
  zone_id    = local.zone_digitomara
  fight_mode = true
}
