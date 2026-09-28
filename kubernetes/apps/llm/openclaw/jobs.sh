#!/bin/sh
# OpenClaw scheduled jobs. OpenClaw keeps jobs in its state on the PVC
# (volsync-backed), not in openclaw.json, so this script is their source of
# truth. Idempotent via --declaration-key; re-run after a PVC loss:
#
#   kubectl -n llm exec -i deploy/openclaw -c app -- sh -s < jobs.sh
#
# Delivery: DM to the admin user (DISCORD_ADMIN_USER_ID is in the pod env).
# Private-data jobs run on local Ornith; only the public-repo watch uses MiniMax.
set -eu
cd /app 2>/dev/null || true
oc() { node dist/index.js "$@"; }
TO="user:${DISCORD_ADMIN_USER_ID}"
COMMON="--tz Europe/Stockholm --session isolated --channel discord --to $TO --announce --best-effort-deliver --timeout-seconds 1200"

# shellcheck disable=SC2086
oc cron add --declaration-key morning-brief --name morning-brief \
  --display-name "Morning brief" --cron "30 7 * * *" $COMMON \
  --model litellm/self-hosted --thinking low \
  --message "Morning brief for the owner, max 15 lines, skip anything unremarkable.
1. Home Assistant tools: today's weather forecast, indoor temperatures, open doors or windows, unavailable devices, yesterday's energy use if available.
2. Grafana tools (Prometheus datasource): alerts that fired or are still firing since 22:00 yesterday, one line each.
End with at most one suggested action."

# shellcheck disable=SC2086
oc cron add --declaration-key upstream-watch --name upstream-watch \
  --display-name "Upstream homelab watch" --cron "30 8 * * *" $COMMON \
  --model litellm/MiniMax-M3-chat --tools web_fetch \
  --message "Check commits from the last 24 hours in joryirving/home-ops and onedr0p/home-ops.
Use web_fetch on https://api.github.com/repos/<owner>/<repo>/commits?since=<ISO-8601 time 24h ago>, then fetch individual commits you need detail on.
Ignore Renovate/dependency bumps and formatting-only changes.
For each notable change give one line: what changed, and why it could matter for a Talos + Flux + Rook-Ceph + Cilium + envoy-gateway cluster running LiteLLM, llmkube, OpenClaw, Hermes and dispatch. Link the commit.
If nothing is notable, reply with one line saying so."

# shellcheck disable=SC2086
oc cron add --declaration-key cluster-health --name cluster-health \
  --display-name "Cluster health digest" --cron "0 9 * * *" $COMMON \
  --model litellm/self-hosted --thinking medium \
  --message "Daily health digest for the home-kubernetes cluster. Use the Grafana tools with the Prometheus datasource.
Look for problems that fail SILENTLY: firing alerts; Jobs that failed in the last 24h (kube_job_status_failed > 0, including CronJob runs); Flux resources not Ready; Ceph health not OK; nodes not Ready; pods with more than 5 restarts in 24h; PVCs more than 85 percent full.
Report only problems, each as namespace/name plus a one-line likely cause. If everything is healthy, reply with one line saying so."

oc cron list
