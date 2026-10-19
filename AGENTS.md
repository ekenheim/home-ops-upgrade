# Home Operations - AI Assistant Guide

This is a **home Kubernetes cluster monorepo** managed with GitOps (Flux, Renovate, GitHub Actions). A single Talos Linux cluster named `home-kubernetes` hosts infrastructure, media, a data-science / ML stack, a self-hosted LLM platform with a dedicated GPU node, sandboxed coding agents, and self-hosted GitHub Actions runners.

## Repository Structure

```
home-ops-upgrade/
├── kubernetes/              # Cluster configuration (Flux-managed)
│   ├── apps/                # Application configs, grouped by namespace
│   ├── base/                # Gateway API CRDs
│   ├── bootstrap/
│   │   ├── flux/            # Flux install + core kustomization
│   │   └── talos/           # talconfig.yaml, talsecret.sops.yaml
│   ├── flux/                # Flux config, repositories (helm/git), vars
│   └── templates/           # Reusable components (e.g. VolSync)
├── clusterconfig/           # talhelper-generated Talos machine configs
├── terraform/               # OpenTofu for Authentik, Cloudflare, MinIO (host-side, not in-cluster)
├── ansible/                 # Playbooks for NAS and supporting infra
├── build/                   # Custom OCI images (bandit-pipeline, bandit-serve, strapi-bootstrap)
├── .taskfiles/              # Domain-grouped Task commands (Flux, Talos, Rook, VolSync, Bitwarden, …)
├── Taskfile.yaml            # Top-level task entry (run `task -l`)
├── docs/                    # Project notes (primarily the Bandit ML platform)
└── .github/                 # Workflows + Renovate config
```

## Cluster Architecture

Single cluster: **`home-kubernetes`**

- **Control plane**: `master1` (192.168.50.31), `master2` (192.168.50.33), `master3` (192.168.50.34) — Intel NUC12WSHi5
- **Workers**: `worker3` (192.168.50.37, Minisforum MS-A2, general workloads), `worker4` (192.168.50.38, Minisforum MS-S1 MAX, AMD Strix Halo iGPU, 128GB)
- **worker4 is a dedicated LLM node**: tainted `llm-workload=true:NoSchedule`, labelled `node-role.kubernetes.io/rocm-worker`, with its own Talos schematic (`amdgpu` + ROCm kernel args). Only LLM / ROCm workloads and Rook tolerate the taint.
- **Control-plane VIP**: `192.168.50.10:6443`
- **Talos Linux** `v1.13.x`, **Kubernetes** `v1.35.x`, **Flux** `v2.9.x` — the pinned values live in `kubernetes/bootstrap/talos/talconfig.yaml` and `kubernetes/flux/config/flux.yaml`; read those rather than trusting a version written here.
- **Semi-hyper-converged**: compute + Rook/Ceph block storage (one NVMe OSD per node) on cluster nodes; a separate NAS provides NFS/SMB bulk storage and off-cluster backup targets.
- **Hardware state as of 2026-10-12**: `master1` and `master3` are dead and being replaced with ASRock DeskMini B760 nodes; `master2` is the sole etcd member. Check `kubectl get nodes` before assuming three control-plane nodes.

## Key Technologies

| Category     | Tool                              | Purpose                                       |
|--------------|-----------------------------------|-----------------------------------------------|
| OS           | Talos Linux                       | Immutable, declarative Kubernetes OS          |
| OS upgrades  | tuppr                             | Declarative Talos / Kubernetes upgrades       |
| GitOps       | Flux                              | Reconciles `kubernetes/` → cluster            |
| CI           | Renovate + GitHub Actions         | Dependency updates, lint, validation          |
| CI           | renovate-operator                 | Self-hosted Renovate running in-cluster       |
| CI runners   | actions-runner-controller         | 5 self-hosted scale sets inside the cluster   |
| Networking   | Cilium (eBPF, kube-proxy replacement) | CNI, L2 announcements, network policy     |
| Ingress      | Envoy Gateway + Gateway API       | Primary L7 ingress (`envoy-internal` / `envoy-external` Gateways), OIDC |
| Ingress      | Istio                             | Service mesh; legacy gateway for Ray / Dask   |
| DNS          | external-dns                      | Syncs HTTPRoute/service records to DNS        |
| Secrets      | external-secrets + Bitwarden      | Secret sync from Bitwarden ClusterSecretStore |
| Secrets@rest | SOPS (age)                        | Encrypts `*.sops.yaml` in Git                 |
| Storage      | Rook/Ceph                         | In-cluster distributed block storage          |
| Storage      | OpenEBS                           | Local hostpath PVs (`/var/mnt/local-hostpath`) |
| Storage      | MinIO                             | S3-compatible object storage (two instances)  |
| Backups      | VolSync                           | PVC snapshot + replication                    |
| Registry     | Spegel                            | In-cluster peer-to-peer OCI mirror            |
| Tunnels      | Cloudflared                       | Cloudflare Tunnel for public ingress          |
| VPN          | Tailscale                         | Mesh VPN into the cluster network             |
| LLM gateway  | LiteLLM + litellm-operator        | Model pools, per-app virtual keys             |
| Inference    | LLMKube (llama.cpp, ROCm)         | Local models on worker4                       |
| IaC          | OpenTofu (`terraform/`)           | Authentik, Cloudflare, MinIO config           |

## GitOps Flow

```
Git push → Flux GitRepository sync → cluster-apps Kustomization
        → per-namespace kustomization.yaml → per-app ks.yaml (Flux Kustomization)
        → kustomization.yaml / HelmRelease → cluster resources
```

- Root Kustomization: `kubernetes/flux/apps.yaml` points at `./kubernetes/apps` with SOPS decryption and `postBuild` substitution from `cluster-settings` (ConfigMap) and `cluster-secrets` (Secret), patched onto every child Kustomization.
- **`ks.yaml` convention**: each app directory defines a Flux `Kustomization` in `ks.yaml` alongside a Kustomize `kustomization.yaml` and a `HelmRelease`.
- Each namespace's `kustomization.yaml` is the on/off switch: commented-out `ks.yaml` entries are disabled apps. Check it before describing an app as running.
- Helm and Git sources live under `kubernetes/flux/repositories/{helm,git}/`.
- Variable values for substitution live under `kubernetes/flux/vars/`.

## Namespace Tour

Apps are grouped by namespace under `kubernetes/apps/<namespace>/`:

- **`llm`** — LLM platform (split out of `datasci` 2026-08-29): LiteLLM gateway + litellm-operator (virtual keys), LLMKube llama.cpp InferenceServices on worker4, CPU embeddings, ComfyUI, whisper.cpp, Lemonade TTS, ToolHive (MCP servers), memini / hindsight (memory), SearXNG, Hermes, OpenClaw (Discord agent gateway with scheduled jobs; its config and workspace `AGENTS.md` are in `openclaw/app/configmap.yaml`, and its cron jobs live on the PVC, so `openclaw/jobs.sh` is re-run by hand after a change, not applied by Flux), Langflow, SillyTavern, OpenCode, repo-wiki, alert-triage, dispatch / courier (agentic coding loop). See `kubernetes/apps/llm/README.md` and `PLATFORM-PLAN.md`.
- **`agent-lab`** — **off since 2026-10-18 except Trackinizer.** Trackinizer (the analysis ledger, `trackinizer` namespace, `https://trackinizer.<domain>` behind Authentik, Crunchy database `trackinizer`) is ON: the owner keeps using it. Everything else is off: the sandbox namespaces `agent-control` / `agent-work` / `agent-verify`, egress-gateway, ledger, factory, the agent-lab LiteLLM key and store, and the `tetragon-policies` Kustomization. The `agent-lab-network-policy` Kustomization runs with only Trackinizer's policy. The manifests stay for a re-enable (agent-factory sandboxes with default-deny Cilium policies, quotas, an Envoy egress gateway that injects the LiteLLM key, a Postgres ledger); the `agentlab` Crunchy database is kept. See "Status" in `kubernetes/apps/agent-lab/README.md` before describing or re-enabling it.
- **`datasci`** — ML / data platform: Ray, Spark, Dagster, MLflow, Label Studio, Jupyter Lab, Marimo, Qdrant, FalkorDB, Docat. Dask, Prefect, Flyte, Bandit (bandit-demo, banditweb; off since 2026-10-15), grid-reliance-infra (off since 2026-10-18) and Seldon Core v2 (off since 2026-10-19; only bandit used it) are disabled.
- **`database`** — Crunchy Postgres, Dragonfly. MariaDB is disabled.
- **`media`** — Plex, Overseerr, Tautulli, Wizarr, Kavita, Maintainerr, xTeve.
- **`downloads`** — Sonarr, Radarr (+ radarr4k), Prowlarr, Bazarr, Kapowarr, Lingarr, Recyclarr, SABnzbd, arr-mcp.
- **`default`** — Home Assistant, Z-Wave JS UI, Music Assistant, Mosquitto, Wyoming (whisper / piper / kokoro), Immich, Paperless, Syncthing, Actual, Outline, PairDrop, FileBrowser, IT-Tools, Gatus endpoints.
- **`network`** — Cloudflared, Envoy Gateway, external-dns, Tailscale, echo-server. Headscale is disabled.
- **`security`** — Authentik.
- **`storage`** — OpenEBS, MinIO (primary + secondary), snapshot-controller, VolSync.
- **`observability`** — kube-prometheus-stack, Grafana (dashboards as ConfigMaps in Git), Thanos, Karma, Gatus, Kromgo, UnPoller, exporters, alertmanager-silencer.
- **`kube-system`** — Cilium, CoreDNS, Spegel, metrics-server, Kubelet CSR approver, k8s-gpu-dra-driver.
- **`kube-tools`** — descheduler, node-feature-discovery, device plugins (Intel + Nvidia), Reloader, tuppr, renovate-operator, fstrim, nfs-stale-watchdog.
- **`development`** — Harbor, Redpanda, Strapi (×2), Rybbit, Bytewax, Atlas, side projects (alphaos, hempriser, marketing-agency). Metabase, Meilisearch, Airbyte, Umami, GrowthBook and Medusa (off since 2026-10-18) are disabled.
- **`rook-ceph`** — distributed storage operator + cluster.
- **`istio-system`** (Istio base / istiod / gateway, Gateway API CRDs; the Seldon Core v1 operator is disabled), **`cert-manager`**, **`external-secrets`**, **`flux-system`**, **`actions-runner-system`** (ARC controller + 5 scale sets: general, atlas, bandit, banditweb, nordic-housing; ecom and grid-reliance off since 2026-10-18), **`blog`**.
- Disabled namespaces still present in the tree: **`argocd`** (deployKF experiment, off), **`vpn`**.

## Conventions

- Secrets are stored in Bitwarden, pulled by `external-secrets` via the Bitwarden `ClusterSecretStore`.
- SOPS (age) encrypts sensitive values in Git. File naming: `*.sops.yaml`. Encryption scope per `.sops.yaml`:
  - `kubernetes/**/*.sops.yaml` — only `data` / `stringData` keys encrypted.
  - `talos/**/*.sops.yaml` and `ansible/**/*.sops.yaml` — full file encrypted.
- Apps use `HelmRelease` via Flux; raw manifests are the exception.
- Per-app README files live next to the app when needed.
- `Taskfile.yaml` is the operator entry point — `task -l` lists commands, with includes from `.taskfiles/{Ansible,Bitwarden,ExternalSecrets,Flux,Kubernetes,Postgresql,Rook,Sops,Talos,VolSync}/`.
- CI workflows in `.github/workflows/`: `lint`, `kubeconform`, `flux-local`, `schemas`, `sops-guard`, `ai-pr-review`, `renovate-trigger`, `volsync-drill`, `publish-terraform`, `strapi-bootstrap`, `release`, `tag`, `labels`, `pr-title`, `lychee`, `nas-restart`, `update-pr-branches`.
- YAML is linted with `yamllint` (`.yamllint.yaml`); Kubernetes manifests validated with `kubeconform` using `lds-schemas.pages.dev` for Flux / Crunchy / External-Secrets CRDs.

## Common Operations

- **List tasks**: `task -l`
- **Add app**: create `kubernetes/apps/<namespace>/<app>/` with `ks.yaml`, `kustomization.yaml`, and a `HelmRelease` (or raw manifests if unavoidable). Register it in the namespace's parent `kustomization.yaml`.
- **Update app**: merge a Renovate PR, or edit the chart version / image tag and push.
- **Troubleshoot**: `flux get all -A`, `flux get all -n <namespace>`, `kubectl get events -A --sort-by=.lastTimestamp`, `task flux:reconcile`.
- **Talos**: `task talos:<subtask>` (apply config, upgrade, reboot, reset).
- **Secrets**: `task bitwarden:unlock`, then standard `task external-secrets:*` commands.
- **Rook/Ceph**: `task rook:<subtask>` (dashboard, check, cleanup).
- **VolSync**: `task volsync:<subtask>` and the `volsync-drill.yaml` workflow validates restore paths.

## Documentation

- Repo overview: `README.md`
- Project-specific deep dives: `docs/` — currently focused on the Bandit ML platform (`bandit-system.md`, `bandit-deployment-lessons.md`, `bandit-sdk-context.md`, `bandit-insights.md`).
- Component documentation: co-located READMEs next to the component.
- Operational how-tos: `.taskfiles/` tasks and the workflow definitions in `.github/workflows/`.

## PR Review Standards

When reviewing Renovate PRs or manual changes, enforce these criteria.

### HelmRelease Requirements
- Applications use `HelmRelease` via Flux, not raw manifests.
- Pin chart versions with `spec.chart.spec.version` for charts outside `app-template`.
- Set `spec.interval` for reconciliation frequency.
- Resource limits (CPU / memory) SHOULD be specified for production workloads (strongly encouraged for `datasci` and `database` namespaces).
- `valuesFrom` should reference ConfigMaps / Secrets rather than inline sensitive values.

### Secret Management Rules
- **NEVER** commit plain-text secrets or credentials.
- All runtime secrets MUST be sourced via `external-secrets` from the Bitwarden `ClusterSecretStore`.
- Any sensitive value in Git MUST be SOPS-encrypted, using the `*.sops.yaml` naming convention and matching the `.sops.yaml` rules.
- A new secret in a PR requires either external-secrets backing or SOPS encryption — no exceptions.

### Image & Digest Policy
- Prefer `@sha256:` digests over version tags where Renovate supports it.
- For tag-only updates, verify OCI metadata (revision / source / created) and confirm the tag exists on the registry before approving — image tags for related components are not always released in lockstep.
- Preferred registries: `ghcr.io`, `registry.k8s.io`, `quay.io`, `docker.io` (fallback). Avoid Docker Hub for critical infrastructure components where a GHCR mirror exists.
- Reject updates from registries not already represented in `kubernetes/flux/repositories/`.

### Breaking Change Detection
`request_changes` if the PR includes:
- API version changes (e.g., `apps/v1beta1` → `apps/v1`).
- Deprecated field usage.
- Major version bumps without linked release notes.
- CRD changes or modifications to existing custom resources.
- Network policy or security context relaxations.
- Changes to storage classes, Ceph pools, or VolSync replication sources.

### Required Evidence for Approval
Before approving, verify:
1. Release notes / changelog cover the target version.
2. GitHub compare view matches the expected diff.
3. Version aligns with what Renovate reported.
4. No breaking changes identified in release notes.
5. Security advisories don't apply to this version.
6. Affected images actually exist at the target tag on the registry.

_Flux automatically reconciles changes once the PR is merged._
