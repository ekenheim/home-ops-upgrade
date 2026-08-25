# platform-mcp

The sanctioned service map for this cluster's data-science stack, served to
agents as an MCP server.

## Why

hermes could already *reach* every data-science service here — there are no
NetworkPolicies in `datasci` or `storage`, and a `curl` from the hermes pod to
mlflow, minio, ray and marimo all answer 200. What it could not do is
*discover* them, so it guessed. The failure mode that prompted this:

- invented MLflow tracking URIs;
- assumed `minio.storage:9000` holds MLflow artifacts (it does not — that is
  `minio-secondary`, and clients should not touch S3 at all because the
  tracking server proxies artifacts);
- wrote notebooks calling `ray.init()` from marimo, which cannot work: the Ray
  cluster is Python 3.10 / Ray 2.54.0 and marimo is Python 3.13.

Facts are exposed as **tools** rather than pasted into hermes' system prompt
deliberately. hermes runs on a 131,072-token window; a thirty-item platform
briefing would be charged on every unrelated turn. As tools they cost nothing
until something asks.

## Tools

| Tool | Answers |
| --- | --- |
| `service_map` | internal + browser URLs, versions, backing stores, per-service gotchas |
| `credentials` | ESO/Bitwarden pattern, canonical env-var names, what a teaching notebook actually needs |
| `storage_map` | buckets per MinIO server, naming convention, the empty `pymc-teaching` bucket, retention (there is none) |
| `runtime_matrix` | Python versions per execution context, uv-vs-Conda, PEP 723 vs image deps |
| `pipeline_patterns` | the Dagster recipe for scheduled/containerized work, and what the non-answers are |
| `compute_limits` | quotas, network policies, nodes, GPU-via-DRA and why not to demo on it |
| `probe` | live reachability of one named service |
| `mlflow_experiments` | live experiment list with artifact locations |
| `ray_status` | live Ray version, nodes and capacity |

The first six are curated static facts — *sanctioned* answers, which is the
point, and why they live in git rather than being scraped from the API server.
The last three are live calls.

## Design notes

**Pure stdlib, stock image.** MCP over stdio is newline-delimited JSON-RPC 2.0,
and the surface needed is `initialize` / `tools/list` / `tools/call`. So the
server is one dependency-free `.py` file mounted from a ConfigMap onto
`python:3.13-slim`. No image to build, publish or Renovate; no `pip install` at
pod start, which would otherwise need egress and a writable `HOME` under
toolhive's enforced `readOnlyRootFilesystem`.

**No Kubernetes API access.** The server has no ServiceAccount beyond the one
toolhive creates and makes no API calls, so it cannot read a Secret. That is
why it is safe to expose unauthenticated inside the namespace, like `context7`.

**`command` override.** toolhive leaves `command`/`args` empty and relies on the
image entrypoint — which for `python:3.13-slim` is an interactive REPL. The
`podTemplateSpec` override on the `mcp` container is what makes a stock image
usable as a backend.

## Maintenance

`disableNameSuffixHash: true` is required because the MCPServer references the
ConfigMap by fixed name. The consequence is that **editing `server/platform_mcp.py`
does not roll the backend StatefulSet by itself** — Flux updates the ConfigMap,
but the mounted file only changes after the pod restarts:

```sh
kubectl -n datasci rollout restart statefulset platform-mcp
```

Verify a change without deploying it — the server speaks plain JSON-RPC on
stdin:

```sh
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"service_map","arguments":{"service":"mlflow"}}}' \
  | python3 server/platform_mcp.py
```

The live-probe tools (`probe`, `mlflow_experiments`, `ray_status`) need cluster
DNS, so test those from inside a pod rather than from a workstation.

## Keeping it honest

The static maps were verified against the live cluster on 2026-08-25. They will
drift. The tools most likely to go stale first are `service_map` versions and
`storage_map` bucket lists; `probe` and `ray_status` are live and will not.
When a service moves, this file is the thing to update — an agent that trusts
it is precisely the thing that will not notice it is wrong.
