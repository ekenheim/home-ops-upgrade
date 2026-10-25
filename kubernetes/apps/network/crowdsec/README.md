# CrowdSec

Reads the `envoy-external` access logs, flags scanners, probes and brute
force against the public routes, and blocks the flagged addresses at Envoy.
Adapted from onedr0p/home-ops (commits 3d8c697, e0f42de).

## Where to look

- **`https://crowdsec.<domain>`** (envoy-internal, Authentik Admins, LAN only):
  [CrowdSec Web UI](https://github.com/TheDuffman85/crowdsec-web-ui) in
  `web-ui/`, read-only. Alerts with the requests behind them, decisions,
  agent and LAPI metrics.
- **Grafana, Network folder, CrowdSec Metrics** (grafana.com 21419): buckets,
  parsers, whitelists, alerts, decisions. Its lines-read panels stay empty
  (no kubernetes-source panel).

```sh
kubectl -n network exec deploy/crowdsec-lapi -- cscli alerts list
kubectl -n network exec deploy/crowdsec-lapi -- cscli decisions list
kubectl -n network exec deploy/crowdsec-lapi -- cscli bouncers list
kubectl -n network exec deploy/crowdsec-agent -c crowdsec-agent -- cscli metrics
```

## Status: enforcing (since 2026-10-20)

A decision in LAPI (default: a 4h ban) now blocks that address on every
`envoy-external` route with a 403. Decisions come from the scenarios below;
nothing is pulled from CrowdSec's community blocklist.

- **Unban someone**: `cscli decisions delete --ip <ip>` on the LAPI pod. The
  bouncer picks it up within 10s.
- **Emergency off**: delete the `crowdsec-bouncer` SecurityPolicy (or comment
  `bouncer/securitypolicy.yaml` out of `bouncer/kustomization.yaml`). Envoy
  stops asking the bouncer; detection keeps running.
- **failOpen**: if the bouncer is down or slow, Envoy lets traffic through
  instead of failing the public routes.

## How it's wired

- **Logs**: the kubernetes acquisition source tails pods labelled
  `gateway.envoyproxy.io/owning-gateway-name=envoy-external` through the API
  (`rbac.yaml`). The chart's schema doesn't know that source, so the
  HelmRelease disables schema validation. Envoy Gateway's default access log
  is JSON; the parser reads `downstream_remote_address`, which Envoy fills
  from the X-Forwarded-For it trusts via the `ClientTrafficPolicy` in
  `envoy-gateway/config/envoy.yaml`, so it is the real client.
- **Collections**: `yanis-kouidri/envoy` (parser + `crowdsecurity/base-http-scenarios`),
  `crowdsecurity/plex` (an allowlist only, so Plex's metadata and transcode
  bursts don't trip the crawl scenarios), the `crowdsecurity/whitelists`
  parser (RFC1918), plus a local Tailscale `100.64.0.0/10` whitelist.
- **Bouncer** (`bouncer/`): kdwils/envoy-proxy-bouncer, attached to the
  `envoy-external` Gateway as an ext_authz SecurityPolicy. Its client IP is
  the rightmost X-Forwarded-For entry outside `${CLUSTER_CIDR}`: the real
  client for Cloudflare traffic, the private address for LAN clients (never
  banned, see whitelists). Its LAPI key is generated once in-cluster
  (`app/externalsecret.yaml`, `CreatedOnce`); LAPI registers it as bouncer
  `ENVOY` from `BOUNCER_KEY_ENVOY`. If the key is ever regenerated (by
  deleting the Secret), restart LAPI so it reads the new value, then run
  `cscli bouncers delete ENVOY` and restart it again, since the start script
  skips a name that already exists. A bouncer stuck on an old key shows as
  403s on `/v1/decisions/stream` in the LAPI log and a NOT_SERVING readiness
  probe.
- **WAF (AppSec)**: the bouncer also sends requests for the hosts in its
  `config.waf.routes` (Strapi, Authentik, Grafana, Home Assistant, Overseerr,
  Wizarr, the marketing site, Hempriser, the blog) to the `crowdsec-appsec`
  pod, which blocks a request matching a virtual-patching (one rule per known
  CVE) or generic rule with a 403, on the first try. Other hosts are not
  inspected: Plex for its streaming volume, S3 for pgBackRest. Headers, URI
  and query only, no bodies. failOpen: a WAF error lets the request through,
  and LAPI bans still apply. To unblock a false positive, find the rule in
  the alert (`cscli alerts inspect <id> -d`) and add it to
  `DISABLE_APPSEC_RULES` in the appsec env.
- **Route-level SecurityPolicies win**: in Envoy Gateway a SecurityPolicy on
  an HTTPRoute replaces the Gateway one for that route unless it sets
  `mergeType: StrategicMerge`. The ones on `envoy-external` today (kavita,
  rybbit-main, strapi-ecom-cms) are private-CIDR allow-lists that 403 every
  internet client anyway. **Any new route-level policy on `envoy-external`
  that lets public traffic through (OIDC, API keys) needs `mergeType`**, or
  CrowdSec silently stops covering that route.
- **Local-only**: `DISABLE_ONLINE_API=true`. No alerts are sent to CrowdSec,
  which also means no community blocklist and no console. The agent still
  downloads hub content from CrowdSec's CDN at startup.
- **LAN clients skip CrowdSec**: split DNS sends them to `envoy-external`
  directly with a private source address. To test from home, go through
  Cloudflare: `curl --resolve <host>:443:<cloudflare edge IP> ...`. The
  built-in test scenario fires on `/crowdsec-test-NtktlJHV4TfBSK3wvlhiOBnl`
  and creates an alert without a ban.
- `envoy-internal` (LiteLLM, OpenClaw, everything LAN/Tailscale-only) is not
  watched.

## Next

- **WAF body inspection** (per route, if ever): the AppSec WAF (see below)
  sees headers, URI and query only. Envoy Gateway 1.9.2's
  `extAuth.bodyToExtAuth` answers 413 to any body over its buffer, failOpen
  or not, so it can only go on routes with small bodies (login endpoints),
  via a route-level SecurityPolicy with `mergeType: StrategicMerge`.
- **App log parsers** (only if failed logins show up): `firix/authentik-logs`,
  `crowdsecurity/home-assistant`, `LePresidente/grafana-logs`,
  `LePresidente/overseerr-logs`. Each needs the app to log the real client
  IP, not Envoy's.
- Not planned: the community blocklist (it means sharing alerts with
  CrowdSec) and the Cloudflare Worker bouncer (scanner load isn't a problem).
