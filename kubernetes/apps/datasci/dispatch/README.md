# dispatch

GitHub issue assignment layer for coding agents — issue sync, LLM grooming,
lanes, and a work queue — plus the CronJob that connects that queue to
`../foreman`. Ported from
[joryirving/home-ops `apps/base/llm/dispatch`](https://github.com/joryirving/home-ops/tree/main/kubernetes/apps/base/llm/dispatch).

## Layout

| Path      | Flux Kustomization        | Contents |
|-----------|---------------------------|----------|
| `app/`    | `dispatch`                | OCIRepository, HelmRelease (web UI + API + prisma migration hook), ExternalSecrets, gatus endpoint |
| `bridge/` | `foreman-dispatch-bridge` | `*/15` CronJob that turns claimed issues into foreman `Workload` CRs |

## The loop

```
GitHub issues
  │  dispatch syncs them, grooms raw issues into agent-ready specs (litellm
  │  `self-hosted`), and parks them in the `local` lane as status/ready
  ▼
dispatch queue  ──(bridge, every 15m)──▶  foreman Workload CR
                                            │
                        coder ─▶ gate ─▶ reviewer ─▶ PR opened on GitHub
                                            │
                     failures come back as feedback-carrying retries;
                     reviewer findings re-dispatch as `coder-revision`
```

The bridge also retries failed `Workload`s and drains the pr-fix queue (review
comments on an open PR come back as a revision task). Both `Workload` and
`FleetNode` CRs are ephemeral runtime state and are not committed.

## Held at chart 0.5.40

`app/ocirepository.yaml` pins 0.5.40 and `.github/renovate/packageRules.json5`
holds it there. **0.5.41 ships a CSP that breaks its own UI**: the middleware
tightened `script-src` from `'self' 'unsafe-inline'` to `'self'` without adding
a nonce, and Next.js App Router streams its RSC payload through inline
`self.__next_f.push(...)` scripts (plus dispatch's own inline theme script).
Every browser blocks them, so the app never hydrates.

The symptom is not an error page. Server-side everything is healthy — pages and
API routes answer in 10–35 ms — but nothing on the client works: `/automation`
renders only its `Loading...` shell forever, client-side navigation falls back to
full page loads, and the UI reads as "slow" rather than broken.

Confirm which side a future version is on without deploying it:

```sh
kubectl run csp --rm -i --restart=Never -n datasci --image=ghcr.io/misospace/dispatch:<tag> \
  --command -- sh -c 'grep -rho "Content-Security-Policy\":\"[^\"]*\"" .next/server/edge/chunks/'
```

Lift both holds together once that prints `'unsafe-inline'` again, or a nonce.

## Deltas from upstream

- Namespace `llm` → `datasci`; `DISPATCH_URL: http://dispatch.datasci:3000`,
  `FOREMAN_NAMESPACE: datasci`, litellm at `http://litellm.datasci:4000/v1`.
- **Auth is `basic`, not `oidc`** — this cluster's Authentik has no dispatch
  provider, and adding one means a tofu-controller change. Basic mode needs only
  `DISPATCH_AUTH_USERNAME` + `DISPATCH_AUTH_PASSWORD`; `NEXTAUTH_SECRET` /
  `NEXTAUTH_URL` are oidc-only and omitted.
- Gateway API `route:` → nginx `ingress:` with `className: internal`, matching
  the rest of this repo, plus homepage annotations and a gatus endpoint.
- Database is Crunchy, not CNPG: `app/externalsecret.yaml` builds the URL from
  `postgres-pguser-dispatch` and writes it as key `uri`. The `dispatch` user must be
  declared in `apps/database/crunchy-postgres/cluster/cluster.yaml` -- it ships
  commented out there and is uncommented together with this app's ks.yaml. Direct
  primary with `sslmode=require`, not pgbouncer — the chart's pre-upgrade
  `prisma migrate deploy` hook takes session-scoped advisory locks that
  pgbouncer's transaction pooling drops (same trap as litellm).
- **One lane, not three.** Upstream's `frontier` lane escalates exhausted issues
  to a cloud model (MiniMax-M3); this cluster serves only `qwen3-35b-a3b`, so
  there is nothing to escalate *to*. `ESCALATION_LANE` is unset and exhausted
  issues tombstone. `laneAliases` maps `frontier`/`escalated`/`cloud` → `local`
  so an issue labelled by hand still lands somewhere claimable. Re-add the lane
  (here and in `bridge/helmrelease.yaml`) if a cloud model is ever enabled in
  `../litellm/app/configmap.yaml`.
- `LANE_CODER_AGENTS` is a single `coder` instead of upstream's round-robin over
  coder model variants; `PR_FIX_LANE_AGENTS` collapses both tiers onto `coder`.
- `MAX_IN_PROGRESS: "2"`, sized to foreman's `agent.replicaCount: 3`.
- `GATEPROFILE_MAP` ships only the `"*"` no-op fallback, so the loop works on any
  repo out of the box and the coder's in-workspace self-gate is what catches
  breakage. **Add a real per-repo profile for anything you actually point this
  at** — see the commented example in `bridge/helmrelease.yaml`.
- Bridge chart: upstream pins its own `app-template` OCIRepository; this uses the
  shared `bjw-s` HelmRepository like every other app-template release in the repo.
- Secret store `onepassword` → `bitwarden-secrets-manager`.

## Required secrets

Bitwarden Secrets Manager item **`dispatch`**:

| Field | Purpose |
|-------|---------|
| `DISPATCH_AGENT_TOKEN` | Bearer token for the agent API. Any long random string. **The bridge reads the same field** — a mismatch means every tick 401s. |
| `DISPATCH_AUTH_USERNAME` | Operator UI Basic Auth username |
| `DISPATCH_AUTH_PASSWORD` | Operator UI Basic Auth password |

Plus `GITHUB_TOKEN` from the shared **`github_token`** item (see
`../foreman/README.md`), and `LITELLM_API_KEY` on this item -- a scoped litellm
virtual key, not litellm's `LITELLM_MASTER_KEY`. The master key is the proxy's
admin credential: it bypasses every team and model restriction, and sharing one
field across apps means a rotation hits all of them at once.

`DISPATCH_AUTH_MODE=disabled` is deliberately not used: it removes auth from the
mutating API too, so anything that reaches the ingress could queue coder Jobs
that push to GitHub with the agent PAT.
