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
| `memini` | Memory service (REST + MCP). Hermes' provider plugin talks to this one. |
| `hindsight` | **New.** Memory service to consolidate on: banks per consumer, MCP per bank, LLM on the ChatGPT subscription. Nothing wired yet. |
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

## memini is hermes' memory; agentmemory and supermemory are gone

agentmemory was removed on 2026-09-17. Its image (`ghcr.io/joryirving/agentmemory`)
came from a cluster whose author had already moved to memini, and memini had been
running alongside it since 2026-08-29. hermes now uses memini through the
provider plugin in `hermes/app/memini-plugin.yaml` (briefing into the system
prompt, search before each turn, every turn captured as an episodic memory,
`memory_recall` / `memory_save` tools). hermes authenticates with memini's
`hermes` named key, whose default namespace is `hermes`; that key is rendered
into memini's keys file AND into the `hermes-memini` Secret from the same
`MEMINI_HERMES_KEY` field of the `memini` Bitwarden item.

supermemory was trialled for one day (2026-09-17) and dropped: the popular MIT
repo is SDKs and plugins, the server is a closed ~300 MB Bun binary with no
source (supermemoryai/supermemory#1299), and orgs, scoped keys, connectors and
the dashboard are all enterprise-only. The Bitwarden item it asked for was never
created; nothing to clean up.

Two upstream memini pieces are still deliberately **not** ported, because they
cannot schedule on this cluster as written:

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

memini needs a **`memini` Bitwarden Secrets Manager item** with these fields:

| Field | Used for |
| --- | --- |
| `MEMINI_API_KEY` | admin bearer for memini's own `/v1` and `/mcp`, and the fsck CronJob |
| `LITELLM_API_KEY` | litellm **virtual** key for embeddings + assessment — never the master key |
| `MEMINI_HERMES_KEY` | the `hermes` named key: memini's keys file AND hermes' `hermes-memini` Secret |

## hindsight: banks per consumer, LLM on the ChatGPT subscription

Added 2026-09-17 (vectorize-io/hindsight, MIT, chart `oci://ghcr.io/vectorize-io/charts/hindsight`).
Nothing consumes it yet. hermes ships a native provider for it under
`plugins/memory/hindsight` in the image we run, so switching hermes later is a
config change, not a plugin.

How it is put together, and why:

- **Store: the Crunchy Postgres cluster**, database `hindsight`, user declared
  in `database/crunchy-postgres/cluster/cluster.yaml`. Hindsight needs pgvector
  and only supports Postgres (or Oracle); Qdrant is not an option. The URL is
  spelled out in the HelmRelease with `sslmode=require` because the Crunchy
  primary is TLS-only and the chart's helper would omit it. The password comes
  straight from PGO's user Secret through a per-entry `sourceRef` on the
  ExternalSecret, so it is never copied into Bitwarden.
- **LLM: the ChatGPT subscription through Hindsight's own `openai-codex`
  provider**, not litellm. litellm's `chatgpt` alias fails every non-streaming
  `/chat/completions` call (re-verified on v1.101.0, 2026-09-17) and Hindsight
  only makes non-streaming calls. The Codex provider speaks the SSE backend
  natively. It needs its own OAuth grant: a **fourth** independent device login
  (codex CLI, hermes, litellm, now hindsight), because refresh tokens are
  single-use and rotate. It lives in `auth.json` on the `hindsight` PVC,
  mounted at `/codex-auth`.
- **Models come from the Codex backend's list, not OpenAI's API catalogue.**
  Hindsight's default for this provider (`gpt-5.4-mini`) is no longer served.
  On 2026-09-17 the list was gpt-6-astra, gpt-5.6-{sol,terra,luna}, gpt-5.5,
  gpt-reserve, codex-auto-review. luna (light tier) does per-turn extraction,
  5.5 does reflect. Check the list before changing either:
  `GET https://chatgpt.com/backend-api/codex/models?client_version=0.0.0`
  with a Codex access token and `chatgpt-account-id` header.
- **Embeddings: litellm `all-minilm`, 384 dims**, same as memini. Dims are
  baked into the pgvector columns; changing the model means new banks.
- **Reranker: the chart's CPU text-embeddings-inference sidecar** running the
  same MiniLM cross-encoder Hindsight would run locally. The slim image has no
  torch. `tei.reranker.enabled: false` drops to reciprocal-rank fusion.
- **Auth: one bearer**, `HINDSIGHT_API_TENANT_API_KEY`, enforced by the built-in
  tenant extension on `/v1` and `/mcp`. Per-consumer isolation is by **bank**
  (`hermes`, `claude-code`, ...), and each bank has its own MCP endpoint. The
  control plane UI has no login of its own (vectorize-io/hindsight#1148), so it
  is internal-ingress only and talks to the API with the same key.

### Before this can start

1. **`hindsight` Bitwarden Secrets Manager item** with two fields:

   | Field | Used for |
   | --- | --- |
   | `HINDSIGHT_API_TENANT_API_KEY` | bearer for `/v1` and `/mcp`; any long random string |
   | `LITELLM_API_KEY` | litellm **virtual** key allowed `all-minilm` — embeddings only |

2. **`vector` extension**, once, as superuser, after PGO has created the
   database (the app role owns the database but PG16 does not trust `vector`):

   ```sh
   P=$(kubectl get pods -n database -l postgres-operator.crunchydata.com/role=master -o jsonpath='{.items[0].metadata.name}')
   kubectl exec -n database "$P" -c database -- psql -U postgres -d hindsight -c 'CREATE EXTENSION IF NOT EXISTS vector;'
   ```

3. **Codex device login**, on any machine with the codex CLI, into a throwaway
   home so it can never collide with your own grant, then copied onto the PVC.
   The API pod crash-loops until this file exists, so the copy goes through a
   helper pod that mounts the (then unattached) PVC:

   ```sh
   export CODEX_HOME=$(mktemp -d)
   npx @openai/codex login --device-auth          # enter the code at the URL it prints
   kubectl run -n llm codex-auth-copy --restart=Never --image=docker.io/library/busybox \
     --overrides='{"spec":{"securityContext":{"runAsUser":1000,"runAsGroup":1000,"fsGroup":1000,"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"c","image":"docker.io/library/busybox","command":["sleep","600"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}},"volumeMounts":[{"name":"a","mountPath":"/codex-auth"}]}],"volumes":[{"name":"a","persistentVolumeClaim":{"claimName":"hindsight"}}]}}'
   kubectl wait -n llm --for=condition=Ready pod/codex-auth-copy
   kubectl cp "$CODEX_HOME/auth.json" llm/codex-auth-copy:/codex-auth/auth.json
   kubectl delete pod -n llm codex-auth-copy
   rm -rf "$CODEX_HOME"
   ```

   Do not reuse that grant anywhere else afterwards.

4. **First boot may lose the Crunchy ownership race** (alembic runs before the
   userinit controller grants `CREATE` on `public`); it retries on its own. See
   the crunchy onboarding notes if it does not.

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
