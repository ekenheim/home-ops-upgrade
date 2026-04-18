# Home Operations - AI Assistant Guide

This is a **home Kubernetes cluster monorepo** managed with GitOps (Flux, Renovate, GitHub Actions). A single Talos Linux cluster named `home-kubernetes` hosts infrastructure, media, a data-science / ML stack, and self-hosted GitHub Actions runners.

## Repository Structure

```
home-ops-upgrade/
├── kubernetes/              # Cluster configuration (Flux-managed)
│   ├── apps/                # Application configs, grouped by namespace
│   ├── base/                # Gateway API CRDs
│   ├── bootstrap/
│   │   ├── flux/            # Flux install + core kustomization
│   │   └── talos/           # talconfig.yaml, talsecret.sops.yaml, generated clusterconfig
│   ├── charts/              # Custom Helm charts
│   ├── flux/                # Flux config, repositories (helm/git), vars
│   └── templates/           # Reusable components (e.g. VolSync)
├── terraform/               # OpenTofu for Authentik, Cloudflare, MinIO (host-side, not in-cluster)
├── ansible/                 # Playbooks for NAS and supporting infra
├── build/                   # Custom OCI images (bandit-pipeline, bandit-serve, strapi-bootstrap)
├── cilium/                  # Standalone Cilium templates (separate from kubernetes/apps/kube-system/cilium)
├── .taskfiles/              # Domain-grouped Task commands (Flux, Talos, Rook, VolSync, Bitwarden, …)
├── Taskfile.yaml            # Top-level task entry (run `task -l`)
├── docs/                    # Project notes (primarily the Bandit ML platform)
├── scripts/                 # Operational scripts
└── .github/                 # Workflows + Renovate config
```

## Cluster Architecture

Single cluster: **`home-kubernetes`**

- **Control plane**: `master1` (192.168.50.31), `master2` (192.168.50.33), `master3` (192.168.50.34)
- **Workers**: `worker3` (192.168.50.37)
- **Control-plane VIP**: `192.168.50.10:6443`
- **Talos Linux** `v1.12.6`, **Kubernetes** `v1.35.4`, **Flux** `v2.8.5`
- **Semi-hyper-converged**: compute + Rook/Ceph block storage on cluster nodes; a separate NAS provides NFS/SMB bulk storage and off-cluster backup targets.

## Key Technologies

| Category     | Tool                              | Purpose                                       |
|--------------|-----------------------------------|-----------------------------------------------|
| OS           | Talos Linux                       | Immutable, declarative Kubernetes OS          |
| GitOps       | Flux                              | Reconciles `kubernetes/` → cluster            |
| CI           | Renovate + GitHub Actions         | Dependency updates, lint, validation          |
| CI runners   | actions-runner-controller         | 6 self-hosted scale sets inside the cluster   |
| Networking   | Cilium (eBPF, kube-proxy replacement) | CNI, BGP, network policy                  |
| Ingress      | Istio + Gateway API               | Primary L7 / service mesh                     |
| Ingress      | ingress-nginx                     | Secondary controller (legacy / specific apps) |
| DNS          | external-dns                      | Syncs ingress/service records to DNS          |
| Secrets      | external-secrets + Bitwarden      | Secret sync from Bitwarden ClusterSecretStore |
| Secrets@rest | SOPS (age)                        | Encrypts `*.sops.yaml` in Git                 |
| Storage      | Rook/Ceph                         | In-cluster distributed block storage          |
| Storage      | MinIO                             | S3-compatible object storage (two instances)  |
| Backups      | VolSync                           | PVC snapshot + replication                    |
| Registry     | Spegel                            | In-cluster peer-to-peer OCI mirror            |
| Tunnels      | Cloudflared                       | Cloudflare Tunnel for public ingress          |
| VPN          | Headscale                         | Self-hosted Tailscale control plane           |
| IaC          | OpenTofu (`terraform/`)           | Authentik, Cloudflare, MinIO config           |

## GitOps Flow

```
Git push → Flux GitRepository sync → cluster-apps Kustomization
        → per-namespace ks.yaml (Flux Kustomization)
        → kustomization.yaml / HelmRelease → cluster resources
```

- Root Kustomization: `kubernetes/flux/apps.yaml` points at `./kubernetes/apps` with SOPS decryption and `postBuild` substitution from `cluster-settings` (ConfigMap) and `cluster-secrets` (Secret).
- **`ks.yaml` convention**: each app directory defines a Flux `Kustomization` in `ks.yaml` alongside a Kustomize `kustomization.yaml` and a `HelmRelease`.
- Helm and Git sources live under `kubernetes/flux/repositories/{helm,git}/`.
- Variable values for substitution live under `kubernetes/flux/vars/`.

## Namespace Tour

Apps are grouped by namespace under `kubernetes/apps/<namespace>/`:

- **`datasci`** — ML / data platform: Bandit, Dagster, Ray, MLflow, Jupyter, Dask, Flyte, DeployKF, LangFlow. Heaviest resource footprint.
- **`database`** — Crunchy Postgres, Dragonfly, MariaDB.
- **`media`** — Plex, Overseerr, Posterizarr, Tautulli, Kometa, Wizarr, Kavita, Maintainerr, Lingarr, xTeve.
- **`network`** — Cloudflared, external-dns, Headscale, ingress-nginx, Tailscale.
- **`security`** — Authentik.
- **`storage`** — MinIO (primary + secondary).
- **`observability`** — monitoring stack.
- **`kube-system`** — Cilium, CoreDNS, Spegel, metrics-server, Kubelet CSR approver.
- **`kube-tools`** — descheduler, node-feature-discovery, device plugins (Intel + Nvidia), Reloader, system-upgrade-controller, fstrim.
- **`rook-ceph`** — distributed storage operator + cluster.
- **`istio-system`**, **`cert-manager`**, **`external-secrets`**, **`flux-system`**, **`actions-runner-system`** (6 scale sets: general, bandit, banditweb, ecom, controller, nordic-housing), **`argocd`** (installed but Flux is primary), **`blog`**, **`development`**, **`downloads`**, **`vpn`**, **`default`**.

## Conventions

- Secrets are stored in Bitwarden, pulled by `external-secrets` via the Bitwarden `ClusterSecretStore`.
- SOPS (age) encrypts sensitive values in Git. File naming: `*.sops.yaml`. Encryption scope per `.sops.yaml`:
  - `kubernetes/**/*.sops.yaml` — only `data` / `stringData` keys encrypted.
  - `talos/**/*.sops.yaml` and `ansible/**/*.sops.yaml` — full file encrypted.
- Apps use `HelmRelease` via Flux; raw manifests are the exception.
- Per-app README files live next to the app when needed.
- `Taskfile.yaml` is the operator entry point — `task -l` lists commands, with includes from `.taskfiles/{Flux,Talos,Rook,VolSync,Bitwarden,Postgresql,Kubernetes,Sops,Ansible,ExternalSecrets}/`.
- CI workflows in `.github/workflows/`: `lint`, `kubeconform`, `flux-local`, `schemas`, `sops-guard`, `volsync-drill`, `publish-terraform`, `strapi-bootstrap`, `release`, `tag`, `labels`, `pr-title`, `lychee`, `nas-restart`, `update-pr-branches`.
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
