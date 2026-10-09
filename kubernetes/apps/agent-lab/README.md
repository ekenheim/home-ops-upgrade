# agent-lab

Kubernetes platform for agent-factory (private repo `ekenheim/agent-factory`), a port of
PromtEngineer/software-factory from Upstash Box to Kubernetes Jobs. The orchestrator claims GitHub
issues and runs one sandboxed coding agent per issue, verifies the result in a second sandbox and
records everything in a Postgres ledger.

## Components

| Path | Flux Kustomization | What |
|------|--------------------|------|
| `namespaces.yaml` | `cluster-apps` | `agent-control` (PodSecurity baseline), `agent-work` and `agent-verify` (restricted) |
| `network-policy/` | `agent-lab-network-policy` | Default-deny CiliumNetworkPolicies for all three namespaces, LimitRange and ResourceQuota for the two sandboxes |
| `egress-gateway/` | `agent-lab-egress-gateway` | Gateway `agent-egress` (own ClusterIP EnvoyProxy) routing `/v1` to LiteLLM and injecting the agent-lab key; ReferenceGrant in `llm` |
| `ledger/` | `agent-lab-ledger` | `agentlab-db-secret` (Crunchy user `agentlab`), ObjectBucketClaims `agent-artifacts` / `agent-holdout` (phase 2) |
| `factory/` | `agent-lab-factory` | ServiceAccount `agent-factory`, Roles in `agent-work` / `agent-verify`, `agent-factory-github` in all three namespaces, tokenless `default` ServiceAccounts in the sandboxes, the suspended CronJob |
| `../llm/litellm/keys/agent-lab.yaml` | `litellm-keys` | LiteLLMVirtualKey `agent-lab` ($10/day, 8 parallel, 120 rpm). Lives in `llm` because the operator writes the Secret next to the CR |

Also outside this directory:

- the `agentlab` user in `database/crunchy-postgres/cluster/cluster.yaml`;
- ClusterSecretStore `litellm-key-secrets-agent-lab` in `llm/litellm/clustersecretstore/agent-lab.yaml`,
  which can read only `litellm-key-agent-lab` and serves only `agent-control` (Atlas keeps its own
  store, so neither consumer can read the other's key);
- `agent-work` / `agent-verify` excluded from every evicting descheduler plugin.

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
9. Only the orchestrator reaches Postgres and the API server.
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

## Phase 2 notes

The `agent-artifacts` / `agent-holdout` buckets are not reachable yet. `rook-ceph/rgw-allow-datasci`
admits RGW ingress only from `datasci`, and the agent policies have no RGW egress. When phase 2
lands, extend that policy, or add one, for the `agent-control` and `agent-verify` tools pods, and
add the matching Cilium egress allow.

## Roll back

Delete `kubernetes/apps/agent-lab/` and merge. `cluster-apps` prunes the four Kustomizations, and they
prune everything they created, including the namespaces and the ReferenceGrant in `llm`. To revoke
the LLM key, also remove `../llm/litellm/keys/agent-lab.yaml` from its kustomization; the operator
deletes the key in LiteLLM. Remove `../llm/litellm/clustersecretstore/agent-lab.yaml` along with it.

The ExternalSecret `agentlab-db-secret` uses `deletionPolicy: Retain`. The Postgres user and
database stay until the `agentlab` entry is removed from `cluster.yaml`, and Crunchy does not drop
the database even then.
