# `llm` namespace

Split out of `datasci` on 2026-08-29. `datasci` keeps the data-science platform
(mlflow, ray, spark, dagster, marimo, jupyter-lab, qdrant, falkordb, seldon,
kubeflow, label-studio); everything that serves, proxies, tools or drives an LLM
lives here.

| App | Role |
| --- | --- |
| `litellm-operator` | **New.** Renders LiteLLM proxy config from CRDs. Currently idle — see below. |
| `litellm` | The gateway. Still a bjw-s app-template HelmRelease + hand-written `config.yaml`. |
| `llmkube` | llama.cpp InferenceServices (`ornith-35b`, `qwen38-27b`) on worker4's Strix Halo iGPU. |
| `ollama-igpu` | Ollama on the Intel iGPU. |
| `toolhive` | MCP operator + `ha-mcp`, `context7`, `memory-mcp`, `platform-mcp`. |
| `agentmemory` | Long-term memory service behind litellm. Hermes' plugin talks to this one. |
| `memini` | **New.** Second memory service (REST + MCP), on trial alongside agentmemory. |
| `searxng` | Search backend for open-webui and litellm. |
| `open-webui`, `hermes`, `langflow` | Frontends. |
| `foreman`, `dispatch` | The agentic coding loop. |

## litellm-operator is installed but drives nothing

The chart ships six CRDs (`LiteLLMProxy`, `LiteLLMModel`, `LiteLLMVirtualKey`,
`LiteLLMTeam`, `LiteLLMGuardrail`, `LiteLLMMCPServer`) and the operator reconciles
only what those CRs describe. No CR exists yet, so `litellm` keeps rendering from
`litellm/app/configmap.yaml` exactly as before. Installing the operator first is
deliberate: it puts the CRDs and the LLMKube auto-register watcher in place
without coupling gateway availability to an operator that has nothing to do.

`llmkube.autoRegister: true` is the near-term payoff — it mints a `LiteLLMModel`
whenever an `InferenceService` goes Ready, which removes the hand-mirroring of
`ornith-35b` / `qwen38-27b` into `config.yaml`'s `model_list`. That registration
is inert until a `LiteLLMProxy` exists to adopt it.

Conversion reference (jory's, same chart):
<https://github.com/joryirving/home-ops/tree/main/kubernetes/apps/base/llm/litellm>

## Cutover runbook

**A namespace move is destroy-then-recreate.** Flux keeps the same Kustomization
objects, sees a new `targetNamespace`, prunes the old resources and creates new
ones. `ceph-block` and `ceph-filesystem` both reclaim `Delete`, so **pruning the
old PVCs destroys their data**. Do steps 1–2 *before* pushing.

### 1. Retain every volume, then push

`./migrate.sh retain` flips all seven PVs backing this namespace to
`persistentVolumeReclaimPolicy: Retain` and records the PVC→PV map to
`.migrate-pvmap`. Run it **before** pushing — once Flux prunes the PVCs there is
nothing left to look the volume names up from.

```sh
./migrate.sh retain
./migrate.sh status
```

Four of them rebind directly in step 4:

| PVC | Size | Why it cannot be re-created cheaply |
| --- | --- | --- |
| `llmkube-model-cache` | 300Gi | GGUF weights; hours to re-download |
| `ollama-igpu` | 100Gi | Ollama model blobs |
| `litellm-chatgpt-auth` | 1Gi | Codex OAuth grant — see step 5 |
| `memory-mcp` | 1Gi | MCP memory graph, 136 days of it |

The other three — `hermes`, `agentmemory`, `open-webui` — **cannot** be rebound.
`templates/volsync/claim.yaml` gives them a `dataSourceRef` on
`ReplicationDestination <app>-bootstrap`, and a PVC carrying a `dataSourceRef` is
handled by the volume-populator controller: it provisions a fresh volume from the
restic snapshot and will not adopt a pre-existing PV. They restore from restic
(step 6). `retain` still flips their PVs, so if a restore comes back empty or
stale the old volume is still there to mount from a debug pod.

### 2. Force a fresh backup of the volsync apps

The restore replays the last snapshot, so anything written after it is lost:

```sh
for a in hermes agentmemory open-webui; do
  kubectl -n datasci patch replicationsource "$a" --type=merge \
    -p '{"spec":{"trigger":{"manual":"pre-llm-move"}}}'
done
kubectl -n datasci get replicationsource -w   # lastManualSync == pre-llm-move
```

### 3. Push, and let Flux prune `datasci`

Note this repo auto-merges: opening the PR *is* the deploy.

```sh
flux reconcile ks cluster-apps --with-source
kubectl get pods -n datasci -w
```

### 4. Re-bind the retained PVs into `llm`

Once the old PVCs are gone their PVs sit `Released`, holding a stale `claimRef`
that stops them binding to anything. `rebind` re-points each at the new claim —
setting `claimRef` rather than clearing it reserves the volume for exactly that
PVC, so no other pending claim can take it:

```sh
./migrate.sh rebind
./migrate.sh status     # expect Available/Bound, never Released
```

### 5. ChatGPT OAuth grant

The `litellm-chatgpt-auth` volume holds a Codex refresh token that is
**single-use and rotates on every use**. If the re-bind in step 4 fails, a
restored copy is an already-spent token — the only recovery is a fresh device
login. litellm blocks on `:4000` for up to 15 minutes waiting for it, which the
120×10s startup probe is sized for; read the code out of the pod log and
authorize it:

```sh
kubectl -n llm logs deploy/litellm -c app -f
```

### 6. Restore the volsync apps

Their `ks.yaml` still carries the volsync component, and `ReplicationDestination`
`<app>-bootstrap` restores from the same restic repository (keyed on `${APP}`,
not on namespace), so the new PVC hydrates from the backup taken in step 2.

Do **not** let the old and new `ReplicationSource` run against one restic
repository at once — a dead lock holder makes volsync re-read the whole PVC every
3 minutes indefinitely. Confirm `datasci` is fully pruned before `llm` starts
syncing.

### 7. Verify

```sh
kubectl -n llm get pods,pvc
kubectl -n llm get helmrelease
kubectl -n llm get litellmproxy,litellmmodel      # expect: no resources (operator idle)
kubectl -n llm exec deploy/hermes -- curl -s http://litellm.llm:4000/health/liveliness
kubectl -n llm exec deploy/litellm -c app -- curl -s http://ornith-35b.llm:8080/v1/models
```

## memini runs alongside agentmemory, not instead of it

They share no data and nothing is wired from one to the other. `agentmemory` is
unchanged and is still what `hermes/app/agentmemory-plugin.yaml` calls; `memini`
is there to be evaluated on its own. Worth knowing while you compare them:
agentmemory's image is `ghcr.io/joryirving/agentmemory`, and that author's own
cluster no longer runs it — they moved to memini. So this is the trial before
deciding whether to follow, not a hedge.

Two upstream pieces were deliberately **not** ported, because they cannot
schedule on this cluster as written:

- **`memini-embed`** — upstream serves Qwen3-Embedding-0.6B (1024-dim) from a
  dedicated InferenceService requesting an Intel GPU through DRA
  (`resourceClaimTemplateName`). The only DeviceClass here is `gpu.amd.com`;
  Intel iGPUs are exposed through the `gpu.intel.com/i915` device plugin, which
  is a different mechanism. memini instead embeds via litellm's existing
  `all-minilm` model (384-dim, served by `ollama-igpu` on the control-plane
  iGPUs). Lower recall quality, but zero new GPU load.
- **`memini-rerank`** — would be a third pod sharing worker4's single
  `llama-strix-gpu` ResourceClaim with ornith-35b and qwen38-27b. Left off;
  recall falls back to plain vector similarity.

`MEMINI_REEMBED_ON_MODEL_CHANGE: true` is set, so if a dedicated embedding model
is added later the store re-embeds rather than silently mixing 384- and
1024-dimension vectors.

### Before this can start

memini needs a **new `memini` Bitwarden Secrets Manager item** with three fields:

| Field | Used for |
| --- | --- |
| `MEMINI_API_KEY` | bearer for memini's own `/v1` and `/mcp`, and the fsck CronJob |
| `LITELLM_API_KEY` | litellm **virtual** key for embeddings + assessment — never the master key |
| `MEMINI_HERMES_KEY` | named key in `api-keys.yaml`, pinned to the `hermes` namespace |

BWS items are undiscoverable from inside the cluster, and a **missing field
renders empty while the ExternalSecret still reports `SecretSynced`** — so a
typo here looks perfectly healthy and only fails later as a 401. Confirm with:

```sh
kubectl -n llm get secret memini -o jsonpath='{.data}' | tr ',' '\n'
```

## Two couplings that span namespaces

Both are easy to half-change and get a silently empty dashboard rather than an error.

**foreman CRD metrics.** `foreman.crs.enabled` renders a CustomResourceState
ConfigMap into `observability` (`crs.namespace`) because kube-state-metrics can
only mount ConfigMaps from its own namespace. The consuming half —
`kube-state-metrics.customResourceState` with `create: false`, plus
`rbac.extraRules` granting list/watch on `foreman.llmkube.dev` — lives in
`apps/observability/kube-prometheus-stack/app/helmrelease.yaml`. Enable one
without the other and you get either a ConfigMap nothing reads or KSM pointing at
a ConfigMap that does not exist. The chart's Grafana dashboard ConfigMap already
carries `grafana_dashboard: "true"` and our sidecar searches all namespaces, so
that part needs no wiring.

**litellm metrics.** The ServiceMonitor scrapes `/metrics` on the proxy port with
no Authorization header, which only works because
`litellm_settings.require_auth_for_metrics_endpoint: false` is set in
`litellm/app/configmap.yaml`. Drop that and the target 401s while still looking
correctly configured. The series themselves only exist because
`success_callback`/`failure_callback` name `prometheus` — the alerts in
`prometheusrule.yaml` read counters written on the failure path, so
`failure_callback` is the load-bearing one.

## What stayed in `datasci` on purpose

`marimo` — hermes reaches it at `marimo.datasci:2718`, and `platform-mcp`'s
service map still points every MLflow/Ray/Dagster/marimo entry at `.datasci`.
`qdrant` and `falkordb` have no consumers in this namespace. If marimo later
moves, `hermes/app/configmap.yaml` and `platform-mcp/server/platform_mcp.py`
both need updating.

## Pre-existing issue, not introduced by the move

`open-webui/app/externalsecret.yaml` still points OIDC at `sso.jory.dev` and
`chat.jory.dev`. Copied from upstream; unrelated to the namespace split.
