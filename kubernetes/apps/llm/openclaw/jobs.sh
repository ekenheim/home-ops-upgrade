#!/bin/sh
# OpenClaw scheduled jobs. OpenClaw keeps jobs in its state on the PVC
# (volsync-backed), not in openclaw.json, so this script is their source of
# truth. Idempotent via --declaration-key; re-run after a PVC loss:
#
#   kubectl -n llm exec -i deploy/openclaw -c app -- sh -s < jobs.sh
#
# Delivery: DM to the admin user (DISCORD_ADMIN_USER_ID is in the pod env).
# All three run on MiniMax-M3 (owner decision 2026-09-30; it is far better at
# the exact tool calls below than Ornith, which spent whole budgets guessing).
# --thinking adaptive: MiniMax-M3 on the Anthropic route supports no other
# level; low/medium were silently mapped to it with a warning on every run.
#
# Two MiniMax-M3 quirks shape the prompts (2026-10-14, from 14 days of
# transcripts): it wraps any list inside toolhive's free-form `parameters` as
# {"item": [...]}, which the servers reject, so no call below takes a list;
# and web_fetch truncates at 20k chars and spills the rest to a /tmp file the
# sandboxed `read` cannot open, which it then tried to web_fetch as file://.
# The GitHub commits API is 3-4 KB per commit (joryirving: 72 commits, 340 KB
# a day, 56 of them smurf-bot), so upstream-watch reads the per-author Atom
# feeds instead: 20 entries, ~17 KB, no bots.
set -eu
cd /app 2>/dev/null || true
oc() { node dist/index.js "$@"; }
TO="user:${DISCORD_ADMIN_USER_ID}"
COMMON="--tz Europe/Stockholm --session isolated --channel discord --to $TO --announce --best-effort-deliver --timeout-seconds 1800"

# Tools sit behind the toolhive gateway (find_tool/call_tool). Left to
# discover them, Ornith spent the whole 20-minute budget guessing tool names
# and argument keys (2026-09-29), so these prompts name every call exactly.
# Home Assistant is one template evaluation; the forecast needs a service call
# because weather entities no longer carry it as an attribute.
# Tibber's monthly net consumption is sensor.tibber_monthly_net_consumption;
# the old sensor.lidkopingsvagen_10_* id no longer exists, and the brief
# reported "unknown None" for energy until 2026-10-17.
BRIEF=$(cat <<'EOF'
Morning brief for the owner, max 15 lines, skip anything unremarkable.
Do NOT use find_tool or tool_search. Make exactly these three calls in parallel, then write the brief.
Every tool below lives behind the toolhive gateway, so no tool_name is a tool id you can call directly. Each call is OpenClaw's tool_call with id "mcp:toolhive:toolhive__call_tool" and args {"tool_name": <name>, "parameters": {...}}.

A. tool_name "ha-mcp_ha_eval_template", parameters {"template": <the template below, verbatim>}
Weather now: {{ states('weather.forecast_home') }}, {{ state_attr('weather.forecast_home','temperature') }}°C
Indoor: {% for e in ['kitchen','bedroom','tv_room','bathroom','computer'] %}{{ e }} {{ states('sensor.' ~ e ~ '_sensor_temperature') }}, {% endfor %}hallway {{ states('sensor.hallway_temperature') }}
Doors/windows open: {{ ['binary_sensor.door_open','binary_sensor.door_window_door_is_open','binary_sensor.door_window_door_is_open_2','binary_sensor.door_tilted','binary_sensor.door_window_door_is_tilted'] | select('is_state','on') | list or 'none' }}
Unavailable entities: {{ states | selectattr('state','eq','unavailable') | list | count }}
Energy this month: {{ states('sensor.tibber_monthly_net_consumption') }} {{ state_attr('sensor.tibber_monthly_net_consumption','unit_of_measurement') }}

B. tool_name "ha-mcp_ha_call_service", parameters {"domain": "weather", "service": "get_forecasts", "entity_id": "weather.forecast_home", "data": {"type": "daily"}, "return_response": true}
Use only the first forecast entry (today): condition, high/low, precipitation.

C. tool_name "grafana_query_prometheus", parameters {"datasourceUid": "prometheus", "queryType": "instant", "startTime": "now", "endTime": "now", "expr": "max by (alertname, namespace, severity) (max_over_time(ALERTS{alertstate=\"firing\", alertname!=\"Watchdog\"}[10h]))"}
These are the alerts that fired overnight, one line each (alertname, namespace, severity).

The unavailable-entity count is normally around 150 (phones and laptops asleep); mention it only if it is far above that.
End with at most one suggested action.
EOF
)

# shellcheck disable=SC2086
oc cron add --declaration-key morning-brief --name morning-brief \
  --display-name "Morning brief" --cron "30 7 * * *" $COMMON \
  --model minimax/MiniMax-M3-messages --thinking adaptive \
  --message "$BRIEF"

# shellcheck disable=SC2086
oc cron add --declaration-key upstream-watch --name upstream-watch \
  --display-name "Upstream homelab watch" --cron "30 8 * * *" $COMMON \
  --model minimax/MiniMax-M3-messages --tools web_fetch \
  --message "Check commits from the last 24 hours in joryirving/home-ops and onedr0p/home-ops.
Make exactly these two web_fetch calls (each repo owner's own commits, newest first; their Renovate bots are left out):
https://github.com/joryirving/home-ops/commits/main.atom?author=joryirving
https://github.com/onedr0p/home-ops/commits/main.atom?author=onedr0p
Keep only entries whose <updated> time is within the last 24 hours.
For a commit you need detail on, web_fetch its <link> URL (https://github.com/<owner>/<repo>/commit/<sha>).
web_fetch only accepts http(s) URLs; never pass it a file path.
Ignore Renovate/dependency bumps and formatting-only changes.
For each notable change give one line: what changed, and why it could matter for a Talos + Flux + Rook-Ceph + Cilium + envoy-gateway cluster running LiteLLM, llmkube, OpenClaw, Hermes and dispatch. Link the commit.
If nothing is notable, reply with one line saying so."

# Flux exports no metrics to Prometheus here, so Flux problems come from the
# read-only flux MCP. Its Warning events, not get_kubernetes_resources: that
# one only stays small with a `fields` list, and M3 sent the list as
# {"item": [...]} on most mornings (83 failed calls in 14 days), then retried
# without it and read the full YAML of every Kustomization. A resource that
# stays broken re-emits its warning every retry interval, so 24 h of events
# covers anything still failing.
HEALTH=$(cat <<'EOF'
Daily health digest for the home-kubernetes cluster.
Do NOT use find_tool or tool_search.
Every tool below lives behind the toolhive gateway, so no tool_name is a tool id you can call directly. Each call is OpenClaw's tool_call with id "mcp:toolhive:toolhive__call_tool" and args {"tool_name": <name>, "parameters": {...}}.

1. In parallel, seven calls with tool_name "grafana_query_prometheus", each with parameters {"datasourceUid": "prometheus", "queryType": "instant", "startTime": "now", "endTime": "now", "expr": <one of these>}:
   - firing alerts: ALERTS{alertstate="firing", alertname!="Watchdog"}
   - Jobs failed in the last 24h: (kube_job_status_failed > 0) and on (namespace, job_name) (kube_job_status_start_time > time() - 86400)
   - Ceph health (0 = OK, 1 = WARN, 2 = ERR): ceph_health_status
   - nodes not Ready: kube_node_status_condition{condition="Ready",status="true"} == 0
   - pods with more than 5 restarts in 24h: sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total[24h])) > 5
   - PVCs more than 85 percent full: max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) > 0.85
   - OSDs down: ceph_osd_up == 0
2. Then two calls with tool_name "flux-mcp_get_kubernetes_events":
   - parameters {"kind": "Kustomization", "type": "Warning", "since": "24h", "limit": 40}
   - parameters {"kind": "HelmRelease", "type": "Warning", "since": "24h", "limit": 40}
   Every parameter is a plain string or number; send them exactly like this.
   Group the events by object (Kind/namespace/name) and report each object once with its latest message, shortened to the cause.
   An object whose newest event is more than 2 hours old has probably recovered; mention it only if it failed repeatedly.

An empty query result means no problem for that check.
Report only problems, each as namespace/name plus a one-line likely cause. If everything is healthy, reply with one line saying so.
EOF
)

# shellcheck disable=SC2086
oc cron add --declaration-key cluster-health --name cluster-health \
  --display-name "Cluster health digest" --cron "0 9 * * *" $COMMON \
  --model minimax/MiniMax-M3-messages --thinking adaptive \
  --message "$HEALTH"

oc cron list
