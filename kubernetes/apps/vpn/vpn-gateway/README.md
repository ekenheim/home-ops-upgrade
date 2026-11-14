# vpn-gateway

Routes **SABnzbd** and **Prowlarr** (namespace `downloads`) out through Mullvad over WireGuard, so Eweka and the indexers see a Mullvad exit IP and DNS lookups never reach the ISP. Everything else in `downloads` (Sonarr, Radarr, Bazarr, VolSync movers, …) goes out directly as before.

Built on [angelnu/pod-gateway](https://github.com/angelnu/pod-gateway) (chart `pod-gateway` 7.x) with a gluetun sidecar.

## How it works

- The gateway pod in `vpn` runs gluetun (WireGuard to Mullvad) plus pod-gateway's dnsmasq/DHCP and a VXLAN endpoint.
- A mutating webhook injects an init and a sidecar container into pods in namespaces labelled `routed-gateway: "true"` **that carry the pod label `setGateway: "true"`**. Those containers replace the pod's default route with a VXLAN to the gateway and point its DNS at the gateway (`172.16.0.1`).
- Cluster, service and LAN CIDRs (`NOT_ROUTED_TO_GATEWAY_CIDRS`) stay on the pod's normal interface, so probes, `*.svc.cluster.local` and envoy-internal keep working.

## Kill switch and leak protection

| Layer | What it does |
|---|---|
| `VPN_BLOCK_OTHER_TRAFFIC: true` | Gateway iptables: FORWARD only out `wg0`; OUTPUT only to UDP 51820 (the tunnel) and cluster/LAN CIDRs. |
| NetworkPolicy on the gateway | Egress only to UDP 51820 and in-cluster pods. |
| Client init/sidecar | Routed pods have no default route outside the VXLAN. If the gateway is down they lose internet, not fall back to the home IP. |
| Webhook `failurePolicy: Fail` + `objectSelector` (postRenderer) | A labelled pod is refused rather than started unrouted while the webhook is down. Unlabelled pods are never sent to the webhook. |
| DNS | Non-`.local` names → gateway dnsmasq → Mullvad's in-tunnel resolver `10.64.0.1`. `.local` → CoreDNS. |

gluetun's own firewall is disabled (`FIREWALL_ENABLED_DISABLING_IT_SHOOTS_YOU_IN_YOUR_FOOT=off`; plain `FIREWALL=off` is ignored since gluetun 3.39) so pod-gateway's rules are the single source of truth.

## Pod security

The injected containers need `NET_ADMIN`/`NET_RAW` and run as root, which `baseline` rejects. `downloads` therefore runs `enforce: privileged` with `warn`/`audit: baseline` so anything else above baseline still shows up.

## Enable checklist

1. Mullvad account → **WireGuard configuration** → generate a key (Linux, any server). From the downloaded `.conf`:
   - `PrivateKey` → `WIREGUARD_PRIVATE_KEY`
   - `Address`, IPv4 part only (e.g. `10.x.y.z/32`) → `WIREGUARD_ADDRESSES`
2. Create a Bitwarden Secrets Manager secret named **`mullvad`** whose value is JSON: `{"WIREGUARD_PRIVATE_KEY": "…", "WIREGUARD_ADDRESSES": "10.x.y.z/32"}`.
3. Merge the PR. Flux deploys the gateway, then SABnzbd and Prowlarr roll and get routed. For the first few minutes they may sit in `Init` or be refused while cert-manager issues the webhook certificate and the tunnel comes up. That is the kill switch working, not a failure, so don't revert.
4. Verify:
   ```sh
   # Exit IP is Mullvad (repeat for deploy/prowlarr)
   kubectl -n downloads exec deploy/sabnzbd -c app -- curl -s https://am.i.mullvad.net/json
   # expect "mullvad_exit_ip": true

   # The gateway forwards DNS to Mullvad, not CoreDNS
   kubectl -n vpn exec deploy/vpn-gateway-pod-gateway-main -c gluetun -- cat /etc/resolv.conf
   # expect nameserver 10.64.0.1

   # Leak test: the TXT answer is the IP of the resolver that reached Google
   kubectl -n downloads exec deploy/sabnzbd -c app -- nslookup -type=txt o-o.myaddr.l.google.com
   # Mullvad-owned address = no leak; your home/ISP IP or 192.168.x = leak
   ```
   Then check an indexer's API/download history shows a Mullvad IP, and run a test download.

If throughput is poor, try `VPN_INTERFACE_MTU: 1420` under `settings`: pod-gateway then sizes the VXLAN to fit inside WireGuard instead of fragmenting.

Exit location is `SERVER_CITIES: Amsterdam` (close to Eweka). Change it in `app/helmrelease.yaml`, e.g. to `Stockholm` or `Malmö`.

## Known gotchas

- Some indexers block or Cloudflare-challenge VPN exit IPs. If one starts failing in Prowlarr, try another exit city or add FlareSolverr.
- DNSSEC is off in the gateway: the v1.13.0 image only ships the 2017 root trust anchor (angelnu/pod-gateway#98).
- `VPN_LOCAL_CIDRS` must not include `10.64.0.0/10`, or DNS to `10.64.0.1` leaves via `eth0`.
- With `VPN_BLOCK_OTHER_TRAFFIC`, `VPN_TRAFFIC_PORT`, `WIREGUARD_ENDPOINT_PORT` and the NetworkPolicy port must all match (51820). Otherwise the handshake is dropped.
