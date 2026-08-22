# foreman

LLMKube's optional add-on control plane: it dispatches agentic coder/reviewer
workloads across a fleet of agent pods. Ported from
[joryirving/home-ops `apps/base/llm/foreman`](https://github.com/joryirving/home-ops/tree/main/kubernetes/apps/base/llm/foreman)
into `datasci`.

Chart `oci://ghcr.io/defilantech/charts/foreman`, pinned in `app/ocirepository.yaml`
and kept in lockstep with the `llmkube` chart tag — foreman's operator drives
llmkube's `InferenceService` CRs.

## Layout

| Path      | Flux Kustomization | Contents |
|-----------|--------------------|----------|
| `app/`    | `foreman`          | OCIRepository, HelmRelease (operator + webhook + agent fleet + coder SA), ExternalSecret |
| `agents/` | `foreman-agents`   | The `Agent` CRs the operator schedules against |

They are split because `agents/` applies CRs whose CRD the `app/` HelmRelease
installs, *and* the chart fronts those CRs with a validating admission webhook
running inside the operator pod. In one Kustomization the first reconcile always
fails until Flux retries. Same split as `../toolhive`.

## The pipeline

One issue flows `coder → gate → reviewer`, each step claiming one FleetNode:

| Agent            | Role     | Model (via litellm)  | Execution | Notes |
|------------------|----------|----------------------|-----------|-------|
| `coder`          | coder    | `self-hosted`        | Job       | Resolves a fresh issue |
| `coder-revision` | coder    | `self-hosted`        | Job       | Re-runs after reviewer findings |
| `gate`           | verifier | *none*               | in-process | Deterministic `run_gate_job` — fmt/lint/build/test |
| `reviewer`       | reviewer | `review`             | in-process | Read-only; its summary becomes the PR body |

`agent.replicaCount: 3` is the real cap on concurrency: one task per FleetNode,
and a Job-mode coder holds its node for the whole Job.

Work only arrives if something creates `Workload` CRs. That is
`../dispatch/bridge` — foreman on its own just idles.

## Deltas from upstream

- Namespace `llm` → `datasci`; litellm at `http://litellm.datasci:4000/v1`.
- Models remapped to this cluster's litellm aliases: `nvidia` → `self-hosted`,
  `reviewer` → `review`.
- `stuckLoopDetection` context caps sized to **65536**, the real per-request
  window here (`contextSize: 524288` / `parallelSlots: 8` on the
  `qwen3-35b-a3b` InferenceService), not upstream's 131k model.
- MCP points at the in-cluster toolhive context7
  (`http://mcp-context7-proxy.datasci:8080/mcp`) instead of the public
  `mcp.context7.com`, so no `CONTEXT7_API_KEY` is needed and
  `../toolhive/context7/mcptoolconfig.yaml`'s curated library-ID list applies.
- Dropped: `coder-frontier` and `coder-strix` (pinned to a MiniMax-M3 alias this
  cluster's litellm does not serve), the `foreman-llmkube` second HelmRelease and
  its `*-fork` agents (upstream dogfooding against `joryirving/LLMKube`), and the
  `GrafanaDashboard` CR (this cluster has no grafana-operator — dashboards are
  sidecar-discovered ConfigMaps labelled `grafana_dashboard`).
- Secret store `onepassword` → `bitwarden-secrets-manager`.

## Required secrets

Bitwarden Secrets Manager item **`github-agents`**, field `GITHUB_TOKEN` — the
PAT the coder pushes branches with and the reviewer opens PRs with. Classic PAT
needs `repo` + `workflow`; fine-grained needs Contents and Pull requests
read/write on every repo the loop is pointed at. Shared with `../dispatch`.

`LITELLM_API_KEY` is a **scoped litellm virtual key**, stored as a field of that
name on a `foreman` item of its own. It is deliberately not litellm's
`LITELLM_MASTER_KEY`: that is the proxy's admin credential, so it bypasses every
team and model restriction, and one shared field means rotating it rotates every
consumer at once -- each only noticing at its next container restart.
