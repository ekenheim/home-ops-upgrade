# agent-lab

Kubernetes platform for agent-factory (private repo `ekenheim/agent-factory`), a port of
PromtEngineer/software-factory from Upstash Box to Kubernetes Jobs. The orchestrator claims GitHub
issues and runs one sandboxed coding agent per issue, verifies the result in a second sandbox and
records everything in a Postgres ledger.

## Status

**Off since 2026-10-18 (owner), except Trackinizer.** The factory went first (#7740), then the rest
the same day (#7747). The owner will try other agent concepts later, so everything is commented out,
not deleted. The rest of this README describes the platform as it ran.

**Trackinizer is ON** (re-enabled 2026-10-18, a few hours after #7747): the owner keeps using it on
its own, through the web UI. What runs for it:

- `namespace-trackinizer.yaml` (split out of `namespaces.yaml`, which keeps the three sandbox
  namespaces, off);
- `network-policy/ks.yaml`, with only `trackinizer.yaml` in `network-policy/app/kustomization.yaml`.
  The Kustomization stays separate from Trackinizer's so `agent-lab-trackinizer` can keep its
  `dependsOn` and the pod never starts before its default-deny policy exists;
- `trackinizer/ks.yaml`, without `externalsecret-token.yaml` (the orchestrator's key lives in
  `agent-control`, which is off);
- `trackinizer-oidc.yaml` in `../security/authentik/app/kustomization.yaml` and
  `authentik-trackinizer-oidc` under `blueprints.secrets` in `../security/authentik/app/helmrelease.yaml`.

What is still off, and what to uncomment to bring it back, in this order:

1. `kustomization.yaml` here: `namespaces.yaml`; the three sandbox policies and `limits.yaml` in
   `network-policy/app/kustomization.yaml`; then `ledger/ks.yaml`.
2. `../llm/litellm/keys/kustomization.yaml` and `../llm/litellm/clustersecretstore/kustomization.yaml`:
   `agent-lab.yaml` in both. The operator mints a new key; the old one was revoked at the prune.
3. `egress-gateway/ks.yaml` here.
4. The `tetragon-policies` Kustomization in `../kube-system/tetragon/ks.yaml`.
5. `factory/ks.yaml`, if the factory itself comes back, with `externalsecret-token.yaml` in
   `trackinizer/app/kustomization.yaml`.

What stayed, untouched: the `agentlab` and `trackinizer` users and databases in the Crunchy cluster
(`trackinizer` held 251 change-log rows, 150 sessions and 57 inquiries, still there on re-enable;
`agentlab` was empty), the `coding` team's `factory-*` models and their pools in LiteLLM, Tetragon
(with the dormant `AgentSandboxNetworkToolExec` alert and the agent-lab `exportAllowList`, which
exports Trackinizer's exec events again), the descheduler excludes, and the Bitwarden items
`agent_factory_github` and `agent_factory_trackinizer`. The Trackinizer application, provider and
binding also stayed in Authentik through the pause (a blueprint that disappears is forgotten, not
reversed); the restored blueprint adopted them.

What did not survive and regenerates on re-enable: the in-cluster generated Secrets (Trackinizer's
session and browser keys, the OIDC client secret; `register-web-key` registers the new browser key on
start and revokes the old one) and the two buckets, which were empty when they went.

## Components

| Path | Flux Kustomization | What |
|------|--------------------|------|
| `namespaces.yaml`, `namespace-trackinizer.yaml` | `cluster-apps` | `agent-control` (PodSecurity baseline), `agent-work`, `agent-verify` (restricted); `trackinizer` (restricted) in its own file |
| `network-policy/` | `agent-lab-network-policy` | Default-deny CiliumNetworkPolicies for all four namespaces, LimitRange and ResourceQuota for the two sandboxes |
| `egress-gateway/` | `agent-lab-egress-gateway` | Gateway `agent-egress` (own ClusterIP EnvoyProxy) routing `/v1` to LiteLLM and injecting the agent-lab key; ReferenceGrant in `llm` |
| `ledger/` | `agent-lab-ledger` | `agentlab-db-secret` (Crunchy user `agentlab`), ObjectBucketClaims `agent-artifacts` / `agent-holdout` (phase 2) |
| `trackinizer/` | `agent-lab-trackinizer` | Trackinizer, the analysis track's ledger (Deployment + ClusterIP Service in `trackinizer`, Crunchy user `trackinizer`), and the orchestrator's Trackinizer key `agent-factory-trackinizer` in `agent-control`. See [Analysis ledger (Trackinizer)](#analysis-ledger-trackinizer) |
| `factory/` | `agent-lab-factory` | ServiceAccount `agent-factory`, Roles in `agent-work` / `agent-verify`, `agent-factory-github` in all three namespaces, tokenless `default` ServiceAccounts in the sandboxes, the suspended CronJob |
| `../llm/litellm/keys/agent-lab.yaml` | `litellm-keys` | LiteLLMVirtualKey `agent-lab` ($10/day, 8 parallel, 120 rpm). Lives in `llm` because the operator writes the Secret next to the CR |

Also outside this directory:

- the `agentlab` and `trackinizer` users in `database/crunchy-postgres/cluster/cluster.yaml`;
- ClusterSecretStore `litellm-key-secrets-agent-lab` in `llm/litellm/clustersecretstore/agent-lab.yaml`,
  which can read only `litellm-key-agent-lab` and serves only `agent-control` (Atlas keeps its own
  store, so neither consumer can read the other's key);
- `agent-work` / `agent-verify` excluded from every evicting descheduler plugin;
- Tetragon (`kube-system/tetragon`): observe-only TracingPolicies in `agent-work` / `agent-verify`
  (writes to system and credential files, every TCP connect, reads of ServiceAccount tokens and
  SSH / git credentials, credential changes, mount attempts, exec from `/tmp`), exec events from all
  four namespaces on the export container's stdout, and the `AgentSandboxNetworkToolExec` alert.

## Trust model

1. Worker and exec pods run untrusted code: LLM output, its tests, and `npm install` scripts.
2. They get no Secrets and no ServiceAccount token, and run as uid 1000 under PodSecurity `restricted`.
   The sandboxes' `default` ServiceAccount has token automount off, so a Job that forgets
   `automountServiceAccountToken: false` still gets no token.
3. Their only egress is DNS, the `agent-egress` gateway and the npm/PyPI/Context7 FQDNs. DNS itself
   only answers cluster names and those FQDNs, so it is not a tunnel out.
4. An explicit `egressDeny` also blocks storage, `llm`, `datasci`, `database`, the nodes, the API
   server, 192.168.0.0/16, 172.16.0.0/12 and the tailnet's 100.64.0.0/10.
5. LLM access goes only through `agent-egress`, which swaps the worker's dummy key for the budgeted agent-lab key.
6. Only stage/collect pods (`app.kubernetes.io/component: tools`) mount a GitHub token and reach github.com.
7. The verifier runs in `agent-verify`, so a worker cannot see or edit the holdout tests.
8. The orchestrator in `agent-control` is trusted code. Its RBAC covers Jobs, pods/log, PVCs and ConfigMaps in the two sandboxes, nothing cluster-wide, and no exec.
9. Only the orchestrator reaches the API server. Postgres is reached by the orchestrator and by
   Trackinizer, each with its own login, and Trackinizer is reached only by the orchestrator and,
   for the web UI, envoy-internal. No sandbox pod reaches either.
10. GitHub tokens: agent-lab has its own Bitwarden item, `agent_factory_github`, never the shared
    `github_token` PAT (that one is the human account's and can write to every repo dispatch and
    courier use, this one included). `GITHUB_TOKEN` is a fine-grained PAT for the target repo only
    (Contents, Issues, Pull requests read/write) and goes to `agent-control` and `agent-work`.
    `GITHUB_TOKEN_READONLY` (Contents read, same repo) is the only token in `agent-verify`, where
    everything downstream of the worker's branch runs. Protect the target repo's default branch so
    neither can push to it.
11. Known gap until the orchestrator meets the contract below: if stage runs the install command,
    or collect runs `git` inside the worker-written `.git`, worker-controlled code (an install
    script, a hook, `core.fsmonitor`, a filter driver) runs next to the mounted token with
    github.com reachable. That is a token theft and push path, which is why the CronJob stays
    suspended until the contract holds. A GitHub App with per-run tokens comes later.
12. Known gap: registry egress is by FQDN over TLS, so `npm publish` with a token an agent was handed
    would get out. A read-only in-cluster registry proxy as the only allowed registry fixes it later.

## Orchestrator contract

The manifests here enforce what they can. The rest is up to the Jobs the orchestrator creates:

- Label every pod `app.kubernetes.io/component` = `worker`, `exec` or `tools`. Anything not
  labelled `tools` is treated as untrusted.
- Set `automountServiceAccountToken: false`, uid 1000, `RuntimeDefault` seccomp, no privilege
  escalation and all capabilities dropped, as `restricted` requires.
- Worker pods set `OPENCODE_DISABLE_MODELS_FETCH` and `OPENCODE_DISABLE_AUTOUPDATE`; models.dev is not
  on the allow list.
- Stage (`tools`) only clones. The install command runs in an `exec` Job with no Secrets.
- Collect (`tools`) does a fresh clone into its own emptyDir, copies the worker's tree from the PVC
  over it (excluding `.git`, symlinks copied as links and never followed), then commits and pushes
  from the fresh clone. It never runs `git` inside the worker's `.git`. If it ever has to, use
  `git -c core.hooksPath=/dev/null -c core.fsmonitor=false` with `GIT_CONFIG_NOSYSTEM=1` and the
  repo config ignored.
- Pass the token by environment, through `GIT_ASKPASS` or `http.extraHeader` set with `-c`.
  Never write it to `.git/config` or a remote URL, since both persist on the PVC the worker reads.
- In `agent-verify`, stage clones the run branch with the read-only token. Install and tests run
  in `exec`.
- The pods print their JSON report between `===FACTORY_REPORT_BEGIN===` and
  `===FACTORY_REPORT_END===`.

## Before the first run

The CronJob ships `suspend: true`. Before the first manual run:

1. Create the target repo `ekenheim/tinylinks` (a copy of agent-factory's `demo-repo/tinylinks`) or
   change `FACTORY_REPO`. Protect its default branch.
2. Create the Bitwarden Secrets Manager item `agent_factory_github`, with JSON fields
   `GITHUB_TOKEN` and `GITHUB_TOKEN_READONLY` scoped as in trust model item 10. Until it exists,
   `agent-lab-factory` stays not-Ready. Nothing else depends on it.
3. Build and push `ghcr.io/ekenheim/agent-factory`, `agent-factory-base` and `agent-factory-tools`
   at `v0.1.0`, then make the three packages **public**. There is no pull secret: GHCR does not
   accept fine-grained PATs, and a classic `read:packages` token would mean one more GitHub token in
   all three namespaces. Check an anonymous pull works, for example
   `docker logout ghcr.io && docker pull ghcr.io/ekenheim/agent-factory:v0.1.0`. Pin the three
   images by digest once they exist.
4. Confirm the orchestrator meets the contract above. Stage must not install, collect must not run
   git in the worker's `.git`, and tokens must stay out of `.git/config`.
5. Confirm the gateway: `kubectl -n agent-control get gateway,svc agent-egress`. The Gateway should be
   `Programmed`, and the Service named `agent-egress`. If not, check the proxy's xDS egress rule
   to `network/envoy-gateway:18000` and the proxy's readiness first.
6. Confirm the network policies are enforced and not just accepted. Run the checks below with
   `hubble observe -n agent-work --verdict DROPPED -f` open alongside.

Smoke pod for the checks. It is labelled `worker`, so it gets the untrusted policy set. Leaving
the label off would give it the same treatment, because anything not `tools` is untrusted:

```sh
kubectl -n agent-work run np-test --rm -it --restart=Never \
  --labels=app.kubernetes.io/component=worker \
  --image=quay.io/curl/curl:8.22.0 --command \
  --overrides='{"spec":{"automountServiceAccountToken":false,"securityContext":{"runAsNonRoot":true,"runAsUser":1000,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"np-test","image":"quay.io/curl/curl:8.22.0","command":["sleep","600"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}' \
  -- sleep 600
# in a second shell:
x() { kubectl -n agent-work exec np-test -- "$@"; }
x curl -s -m5 -H 'Authorization: Bearer dummy' http://agent-egress.agent-control.svc/v1/models  # expect: model list
x curl -s -m5 -o /dev/null -w '%{http_code}\n' https://registry.npmjs.org/                       # expect: 200
x curl -s -m5 http://litellm.llm.svc:4000/health                                                 # expect: timeout (denied)
x curl -s -m5 https://github.com                                                                 # expect: DNS failure (not tools)
x nslookup example.com                                                                           # expect: REFUSED
x curl -s -m5 -k https://kubernetes.default.svc                                                  # expect: timeout (denied)
x curl -s -m5 http://192.168.50.1                                                                # expect: timeout (denied)
```

Then start a second smoke pod and curl the first pod's IP from it. Expect a timeout, because
pod-to-pod traffic inside `agent-work` is denied. Repeat the GitHub check from a pod labelled
`app.kubernetes.io/component=tools`, where `github.com` should resolve and connect.

## Run it

Once the checklist is done, trigger one run by hand:

```sh
kubectl -n agent-control create job --from=cronjob/agent-factory agent-factory-manual-1
kubectl -n agent-control logs -f job/agent-factory-manual-1
```

To schedule it every 6 hours, set `suspend: false` in `factory/app/cronjob.yaml` and merge. Don't
patch the live object, because Flux reverts the change. To stop a run, set `suspend: true` and
delete the running Job.

## Analysis ledger (Trackinizer)

The analysis track writes its Intents, Experiments and Findings to Trackinizer (agent-factory README,
"The analysis ledger"), set by `FACTORY_TRACKINIZER_URL=http://trackinizer.trackinizer.svc:8765` on the
CronJob. It runs in its own namespace so the factory's component-keyed policies never select it. Its
policy admits only the orchestrator's pods and the envoy-internal proxies on 8765, and lets the pod reach
only DNS and the Postgres primary.

The web UI is at `https://trackinizer.${SECRET_DOMAIN}` (LAN only, via `envoy-internal`). The image mounts
no browser sign-in provider (its Google button stays off), so Envoy signs the browser in instead:

- `trackinizer/app/securitypolicy.yaml` sends every request on the route, `/api` included, through
  Authentik OIDC (application `trackinizer`, **authentik Admins only**).
- Workspaces, chats and presets refuse API keys (`require_browser`), so the web app needs Trackinizer's
  own session cookie. `trackinizer/app/addons/gatewaylogin.py`, an addon mounted from a ConfigMap
  (`TRACKINIZER_ADDONS`), issues it. When signed out, Trackinizer sends the browser to
  `/auth/login_page`. The HTTPRoute redirects that to `/api/addons/gatewaylogin/login`, the only
  path where `httproutefilter.yaml` injects the gateway's `browser` key. The addon accepts only that
  key, trades it for a session signed with `TRACKINIZER_SESSION_SECRET`, and redirects back. A
  session acts with the user's role (admin), which is why no other key may sign in.
- The browser key and the session secret are generated in-cluster (nothing in Bitwarden). The
  Deployment's `register-web-key` init container registers the key as `browser` (role writer) on
  every start.
- The Authentik provider, application and group binding are a blueprint in
  `security/authentik/app/trackinizer-oidc.yaml`. Its client secret is generated in-cluster and shared
  with Envoy through a ReferenceGrant.

Sign out at `/oauth2/logout`. Scripts and tests cannot use the URL, since it now needs a browser
session; they go through the Service or a port-forward with their own key.
The orchestrator's key, `FACTORY_TRACKINIZER_TOKEN`, comes from the Bitwarden item
`agent_factory_trackinizer` and is read with `optional: true`. Every ledger failure is logged and
ignored, so a missing or broken Trackinizer never stops a run.

One-time bootstrap, in order. Steps 1–7 were done on 2026-10-11; they are kept here for a rebuild:

1. Make the GHCR package `agent-factory-trackinizer` **public**, like the other agent-factory images,
   and check that an anonymous pull of the pinned digest works.
2. Create the Bitwarden Secrets Manager item `agent_factory_trackinizer` with one field,
   `FACTORY_TRACKINIZER_TOKEN`. A placeholder value is fine until step 5.
3. Nothing to do by hand for the database. After Flux has created the `trackinizer` user and
   database, the Deployment's `db-init` container connects as the postgres superuser (through
   `trackinizer-super-secret`, immich's pattern) and runs `CREATE EXTENSION IF NOT EXISTS vector` and
   `GRANT USAGE, CREATE ON SCHEMA public TO trackinizer` before every start. Both are no-ops once done.

4. On that first good start `TRACKINIZER_BOOTSTRAP_ADMIN` (add it back to the `trackinizer` container in
   `trackinizer/app/deployment.yaml` as `value: "${SECRET_ACME_EMAIL}"`, the ACME email from cluster-secrets)
   seeds the admin and writes its key to `/tmp`, which is an emptyDir. Read the key **before the pod
   restarts**, because the server never mints a second one. Keep it somewhere only you hold:

   ```sh
   kubectl -n trackinizer exec deploy/trackinizer -- cat /tmp/home/.local/share/rekursiv-ai/trackinizer/bootstrap_token
   ```

5. Mint the factory's writer key with the admin key, and put the `secret` from the response in the
   Bitwarden item as `FACTORY_TRACKINIZER_TOKEN`. Never store the admin key there:

   ```sh
   kubectl -n trackinizer port-forward svc/trackinizer 8765:8765 &
   curl -s -X POST http://127.0.0.1:8765/api/me/tokens \
     -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' \
     -d '{"name":"agent-factory","role":"writer"}'
   ```

   The port-forward goes through the kubelet, not the pod network, so the ingress policy does not block
   it. Set `ADMIN_KEY` in the same shell
   first: an empty one gets a 401 `invalid bearer token` and mints nothing. Check the result with
   `GET /api/me/tokens`, which should list an `agent-factory` key with role `writer`.
6. Delete the `TRACKINIZER_BOOTSTRAP_ADMIN` env entry from `deployment.yaml` and merge. The pod restarts
   and the token file goes with the old `/tmp`.
7. From an agent-factory checkout, run the live contract suite against the instance through the same port-forward,
   with the writer key:
   `FACTORY_TEST_TRACKINIZER_URL=http://127.0.0.1:8765 FACTORY_TEST_TRACKINIZER_TOKEN=<writer key> npx tsx --test --import ./test/setup.ts test/analysis-trackinizer.test.ts`.
8. Nothing to do for the web UI. On a brand-new instance `register-web-key` runs before the schema and
   the admin exist and skips; restart the pod once after step 4 so it registers the browser key.

If the admin key is lost before anything has been written, reset the empty instance as superuser and
let the pod bootstrap again (not tested):
`DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION pg_database_owner; CREATE EXTENSION vector;`
in database `trackinizer`, with `TRACKINIZER_BOOTSTRAP_ADMIN` added back (step 4).

## Phase 2 notes

The `agent-artifacts` / `agent-holdout` buckets are not reachable yet. `rook-ceph/rgw-allow-datasci`
admits RGW ingress only from `datasci`, and the agent policies have no RGW egress. When phase 2
lands, extend that policy, or add one, for the `agent-control` and `agent-verify` tools pods, and
add the matching Cilium egress allow.

## Roll back

To pause it, see [Status](#status): that is how it is off now. To remove it for good, delete
`kubernetes/apps/agent-lab/` and merge. `cluster-apps` prunes the five Kustomizations, and they
prune everything they created, including the namespaces and the ReferenceGrant in `llm`. To revoke
the LLM key, also remove `../llm/litellm/keys/agent-lab.yaml` from its kustomization; the operator
deletes the key in LiteLLM. Remove `../llm/litellm/clustersecretstore/agent-lab.yaml` along with it.

The ExternalSecrets `agentlab-db-secret` and `trackinizer-db-secret` use `deletionPolicy: Retain`.
The Postgres users and databases stay until the `agentlab` and `trackinizer` entries are removed from
`cluster.yaml`, and Crunchy does not drop the databases even then.
