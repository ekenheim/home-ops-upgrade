# agent-lab

Kubernetes platform for [agent-factory](https://github.com/ekenheim/agent-factory), a port of
PromtEngineer/software-factory from Upstash Box to Kubernetes Jobs. The orchestrator claims GitHub
issues and runs one sandboxed coding agent per issue, verifies the result in a second sandbox and
records everything in a Postgres ledger.

## Components

| Path | Flux Kustomization | What |
|------|--------------------|------|
| `namespaces.yaml` | `cluster-apps` | `agent-control` (PodSecurity baseline), `agent-work` and `agent-verify` (restricted) |
| `network-policy/` | `agent-lab-network-policy` | Default-deny CiliumNetworkPolicies for all three namespaces, LimitRange and ResourceQuota for the two sandboxes |
| `egress-gateway/` | `agent-lab-egress-gateway` | Gateway `agent-egress` (own ClusterIP EnvoyProxy) routing `/v1` to LiteLLM and injecting the agent-lab key; ReferenceGrant in `llm` |
| `ledger/` | `agent-lab-ledger` | `agentlab-db-secret` (Crunchy user `agentlab`), `agent-factory-github` and `ghcr-pull` in all three namespaces, ObjectBucketClaims `agent-artifacts` / `agent-holdout` (phase 2) |
| `factory/` | `agent-lab-factory` | ServiceAccount `agent-factory`, Roles in `agent-work` / `agent-verify`, the suspended CronJob |
| `../llm/litellm/keys/agent-lab.yaml` | `litellm-keys` | LiteLLMVirtualKey `agent-lab` ($10/day, 8 parallel, 120 rpm). Lives in `llm` because the operator writes the Secret next to the CR |

Also outside this directory: the `agentlab` user in `database/crunchy-postgres/cluster/cluster.yaml`,
`agent-control` in the `litellm-key-secrets` ClusterSecretStore, and `agent-work` / `agent-verify`
excluded from the descheduler's LowNodeUtilization.

## Trust model

1. Worker and exec pods run untrusted code: LLM output, its tests, and `npm install` scripts.
2. They get no Secrets and no ServiceAccount token, and run as uid 1000 under PodSecurity `restricted`.
3. Their only egress is DNS, the `agent-egress` gateway and the npm/PyPI/Context7 FQDNs.
4. An explicit `egressDeny` also blocks storage, `llm`, `datasci`, `database`, the nodes, the API server and 192.168.0.0/16.
5. LLM access goes only through `agent-egress`, which swaps the worker's dummy key for the budgeted agent-lab key.
6. Only stage/collect pods (`app.kubernetes.io/component: tools`) mount the GitHub PAT and reach github.com.
7. The verifier runs in `agent-verify`, so a worker cannot see or edit the holdout tests.
8. The orchestrator in `agent-control` is trusted code. Its RBAC covers Jobs, pods/log, PVCs and ConfigMaps in the two sandboxes, nothing cluster-wide, and no exec.
9. Only the orchestrator reaches Postgres and the API server.
10. Known gap in phase 1: stage pods run the install command with the PAT mounted, so a malicious dependency's install script can read it. A GitHub App with per-repo tokens is the planned fix, along with running install in an exec Job.

Pod labels the orchestrator must set: `app.kubernetes.io/component` = `worker`, `exec` or `tools`.
Anything not labelled `tools` is treated as untrusted.

## Run it

The CronJob ships `suspend: true`. Once the `v0.1.0` images exist, trigger one run by hand first:

```sh
kubectl -n agent-control create job --from=cronjob/agent-factory agent-factory-manual-1
kubectl -n agent-control logs -f job/agent-factory-manual-1
```

To schedule it every 6 hours, set `suspend: false` in `factory/app/cronjob.yaml` and merge. Don't
patch the live object, because Flux reverts the change. To stop a run, set `suspend: true` and
delete the running Job.

Check the gateway from a throwaway pod in `agent-work`:

```sh
kubectl -n agent-work run gw-test --rm -it --restart=Never \
  --labels=app.kubernetes.io/component=worker \
  --image=curlimages/curl --overrides='{"spec":{"automountServiceAccountToken":false,"securityContext":{"runAsNonRoot":true,"runAsUser":1000,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"gw-test","image":"curlimages/curl","args":["-s","-H","Authorization: Bearer dummy","http://agent-egress.agent-control.svc/v1/models"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}'
```

## Roll back

Delete `kubernetes/apps/agent-lab/` and merge. `cluster-apps` prunes the four Kustomizations, and they
prune everything they created, including the namespaces and the ReferenceGrant in `llm`. To revoke
the LLM key, also remove `../llm/litellm/keys/agent-lab.yaml` from its kustomization; the operator
deletes the key in LiteLLM.

The ExternalSecret `agentlab-db-secret` uses `deletionPolicy: Retain`. The Postgres user and
database stay until the `agentlab` entry is removed from `cluster.yaml`, and Crunchy does not drop
the database even then.
