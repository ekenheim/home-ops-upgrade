# CrowdSec

Reads the `envoy-external` access logs and flags scanners, probes and brute
force against the public routes. Adapted from onedr0p/home-ops
(commits 3d8c697, e0f42de).

## Where to look

- **`https://crowdsec.<domain>`** (envoy-internal, Authentik Admins, LAN only):
  [CrowdSec Web UI](https://github.com/TheDuffman85/crowdsec-web-ui) in
  `web-ui/`, read-only. Alerts with the requests behind them, decisions,
  agent and LAPI metrics.
- **Grafana, Network folder, CrowdSec Metrics** (grafana.com 21419): buckets,
  parsers, whitelists, alerts, decisions. Its lines-read panels stay empty
  (no kubernetes-source panel).

## Status: stage 1, observe only

The agent and LAPI run, and LAPI records alerts and decisions, but **nothing
enforces them**: there is no bouncer and no SecurityPolicy yet. Leave it a
week and read what it would have banned before going further.

```sh
kubectl -n network exec deploy/crowdsec-lapi -- cscli metrics
kubectl -n network exec deploy/crowdsec-lapi -- cscli alerts list
kubectl -n network exec deploy/crowdsec-lapi -- cscli decisions list
```

What to check:

- **Envoy is logging at all.** `kubectl -n network logs <an envoy-external
  pod> -c envoy --tail=3` should show JSON lines with
  `downstream_remote_address`. `envoy.yaml` sets no `telemetry.accessLog`, so
  this relies on Envoy Gateway's default; if the lines aren't there, add an
  explicit `telemetry.accessLog` block or this stage sees nothing.
- **Lines are read and parsed.** In `cscli metrics`, the acquisition table
  shows lines from `network/envoy-external-*` and the parser table shows
  `yanis-kouidri/envoy-logs` parsing them. Some unparsed lines are expected:
  the source follows every container in the pod, `shutdown-manager` included.
- **The source address is the real client.** An all-quiet result is not proof:
  `crowdsecurity/whitelists` covers `10.0.0.0/8`, so if the parser read the
  cloudflared pod address instead of the client, nearly every line would be
  *whitelisted* and nothing would ever alert. In `cscli metrics`, the
  whitelist counters should be a small share of parsed lines, not almost all
  of them. The definitive check is one real line:
  `cscli explain --log '<access-log line>' --type envoy` and read the
  `source_ip` it reports. (Envoy fills `downstream_remote_address` from the
  X-Forwarded-For it trusts via the `ClientTrafficPolicy` in
  `envoy-gateway/config/envoy.yaml`, so this should be the public address.)
  If a bouncer ever acted on a pod address, every internet visitor would be
  cut off.
- **The decisions look like real abuse**, not Plex clients, family or monitors.

## How it's wired

- **Logs**: the kubernetes acquisition source tails pods labelled
  `gateway.envoyproxy.io/owning-gateway-name=envoy-external` through the API
  (`rbac.yaml`). The chart's schema doesn't know that source, so the
  HelmRelease disables schema validation.
- **Collections**: `yanis-kouidri/envoy` (parser + `crowdsecurity/base-http-scenarios`),
  `crowdsecurity/plex` (an allowlist only, so Plex's metadata and transcode
  bursts don't trip the crawl scenarios), `crowdsecurity/whitelists` (RFC1918),
  plus a local Tailscale `100.64.0.0/10` whitelist.
- **Local-only**: `DISABLE_ONLINE_API=true`. No alerts are sent to CrowdSec,
  which also means no community blocklist and no console. The agent still
  downloads hub content from CrowdSec's CDN at startup.
- `envoy-internal` (LiteLLM, OpenClaw, everything LAN/Tailscale-only) is not
  watched.

## Stage 2: enforce (not done)

1. Add the bouncer: `oci://ghcr.io/kdwils/charts/envoy-proxy-bouncer`,
   `config.trustedProxies: ["10.42.0.0/16"]`, an API key for LAPI from
   Bitwarden via external-secrets (`BOUNCER_KEY_ENVOY` in LAPI's env).
2. Attach it with a `SecurityPolicy` `extAuth` (gRPC, port 8080,
   `failOpen: true`) targeting the `envoy-external` Gateway.
3. **Route-level policies win**: in Envoy Gateway a SecurityPolicy on an
   HTTPRoute replaces the Gateway one for that route. Every route-level
   policy on `envoy-external` (OIDC and private-CIDR ones) needs
   `mergeType: StrategicMerge`, or CrowdSec silently skips those routes. The
   bouncer's own docs say to attach per HTTPRoute instead; check which works
   on our Envoy Gateway version before choosing.
4. Decide on the online API (community blocklist in exchange for sharing
   alerts) separately.
