#!/usr/bin/env python3
"""datasci-platform MCP server: the sanctioned service map for this cluster.

WHY THIS EXISTS
---------------
Agents (hermes) can already *reach* every data-science service in this
cluster -- there are no NetworkPolicies in `datasci` or `storage`, and a
curl from the hermes pod to mlflow / minio / ray / marimo all answer. What
they cannot do is *discover* them. So they guess: they invent tracking URIs,
assume `minio.storage:9000` is where MLflow artifacts live (it is not, that
is minio-secondary), and write notebooks against a Ray client that cannot
possibly connect because of a Python version mismatch.

This server answers those questions instead of leaving them to inference.
It is deliberately a *pull* interface: hermes runs on a 131,072-token window
(see hermes-config), so 30 answers pasted into a system prompt would cost
more than they are worth on every unrelated turn. As MCP tools they cost
nothing until asked.

DEPENDENCY-FREE ON PURPOSE
--------------------------
Pure stdlib -- MCP over stdio is newline-delimited JSON-RPC 2.0 and the
whole protocol surface we need is initialize / tools/list / tools/call.
That means no image to build and push, no `pip install` at pod start (which
would need egress and a writable HOME under toolhive's enforced
readOnlyRootFilesystem), and no dependency that Renovate has to chase. The
script is mounted from a ConfigMap onto a stock python image.

STATIC FACTS VS LIVE PROBES
---------------------------
The maps below are *sanctioned* answers, not observed ones -- that is the
point, and it is why they are curated in git rather than scraped from the
API server. Anything genuinely time-varying (is MLflow up, what experiments
exist, how much Ray capacity is free) is a live call instead: see probe(),
mlflow_experiments() and ray_status(). Nothing here talks to the Kubernetes
API, so this server needs no ServiceAccount and can never read a Secret.

Verified against the live cluster on 2026-08-25.
"""

import json
import sys
import urllib.error
import urllib.request

# Protocol revisions we can speak. We echo the client's requested version when
# we know it, else offer our newest -- the spec's negotiation rule.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

DOMAIN = "ekenhome.se"

# --------------------------------------------------------------------------
# The service map.
#
# `internal` is what belongs in code and in Kubernetes manifests. `browser`
# is what belongs in prose and Outline docs -- it is behind the `internal`
# IngressClass and an RFC1918 source-range whitelist, so it resolves on the
# LAN only and is NOT reachable from the public internet despite the https.
# --------------------------------------------------------------------------
SERVICES = {
    "mlflow": {
        "status": "running",
        "namespace": "datasci",
        "internal": "http://mlflow.datasci.svc.cluster.local:5000",
        "browser": f"https://mlflow.{DOMAIN}",
        "version": "3.15.1",
        "image": "ghcr.io/ninerealmlabs/mlflow-server",
        "backend_store": {
            "kind": "postgresql",
            "detail": (
                "Crunchy Postgres HA in the `database` namespace, database `mlflow`, "
                "reached through pgbouncer. The URI is assembled by ExternalSecret "
                "`mlflow-db-secret` from ClusterSecretStore `crunchy-pgo-secrets` "
                "(key postgres-pguser-mlflow) -- it is never written in git."
            ),
        },
        "artifact_store": {
            "mode": "proxied",
            "client_sees": "mlflow-artifacts:/",
            "server_writes_to": "s3://mlflow-artifacts/ on minio-secondary",
            "why_it_matters": (
                "The server runs with --serve-artifacts and "
                "--default-artifact-root mlflow-artifacts:/, so CLIENTS NEVER TALK TO S3. "
                "Do not set MLFLOW_S3_ENDPOINT_URL or AWS_* credentials in a notebook or "
                "pipeline just to log an artifact -- MLFLOW_TRACKING_URI is the only "
                "variable required. Artifacts upload through the tracking server, which "
                "holds the only MinIO credential involved."
            ),
        },
        "client_env": {"MLFLOW_TRACKING_URI": "http://mlflow.datasci.svc.cluster.local:5000"},
        "gotchas": [
            "Two experiments predate the proxy switch and still carry a literal "
            "s3://mlflow-artifacts/<id> artifact_location (experiment 35, "
            "hempriser-car-model). New experiments get mlflow-artifacts:/<id>. Create "
            "new experiments rather than reusing 35 unless you want direct-S3 semantics.",
        ],
    },
    "ray": {
        "status": "running",
        "namespace": "datasci",
        "cluster_name": "ray-kuberay",
        "operator": "KubeRay (kuberay-operator), chart ray-cluster 1.7.0",
        "crds": ["rayclusters.ray.io", "rayjobs.ray.io", "rayservices.ray.io", "raycronjobs.ray.io"],
        "internal": {
            "client": "ray://ray-kuberay-head-svc.datasci.svc.cluster.local:10001",
            "dashboard_api": "http://ray-kuberay-head-svc.datasci.svc.cluster.local:8265",
            "gcs": "ray-kuberay-head-svc.datasci.svc.cluster.local:6379",
            "serve": "http://ray-kuberay-head-svc.datasci.svc.cluster.local:8000",
        },
        "browser": f"https://kuberay.{DOMAIN}",
        "lan_loadbalancer": "192.168.50.182 (Service ray-external: 10001/8265/8000/8080/6379)",
        "ray_version": "2.54.0",
        "python_version": "3.10.19",
        "head_image": "rayproject/ray:2.54.0",
        "worker_image": "ghcr.io/ekenheim/grid-resilience-ray-worker:v1.0.18",
        "autoscaling": "enabled in-tree; workergroup min 1 / max 12, 8 CPU + 16G limit each",
        "client_env": {"RAY_ADDRESS": "ray://ray-kuberay-head-svc.datasci.svc.cluster.local:10001"},
        "preferred_access": (
            "Ray Jobs API or a RayJob CR -- NOT Ray Client from marimo. See "
            "runtime_matrix() for the Python-version reason."
        ),
        "gotchas": [
            "Ray enforces an exact Ray-version match between driver and cluster, and is "
            "intolerant of a Python minor-version mismatch. The cluster is Python 3.10 / "
            "Ray 2.54.0; marimo is Python 3.13. A `ray.init(address=...)` from a marimo "
            "notebook CANNOT work -- this is the single most common wrong assumption in "
            "notebooks written against this cluster.",
            "The head image tag is pinned to match the custom worker image, which is "
            "built against Ray 2.54.0. Bump both in lockstep or the workers refuse to "
            "join.",
        ],
    },
    "spark": {
        "status": "installed-but-effectively-unused",
        "namespace": "datasci",
        "operator": "Kubeflow Spark Operator (sparkoperator.k8s.io/v1beta2)",
        "crds": ["sparkapplications", "scheduledsparkapplications", "sparkconnects"],
        "evidence": (
            "Exactly two SparkApplications exist -- grid-resilience-entsoe-ingest and "
            "grid-resilience-era5-ingest -- and BOTH have been in state FAILED since "
            "2026-05-20, i.e. every Spark job on this cluster has been broken for ~96 "
            "days with nobody fixing it. There is no Livy and no Databricks."
        ),
        "recommendation": (
            "Do not build on Spark here and do not teach it as cluster infrastructure. "
            "It is legacy, not strategy. For anything that fits one node use Polars or "
            "DuckDB; for anything that genuinely needs distribution use Ray, which is "
            "healthy and actually exercised."
        ),
    },
    "minio-primary": {
        "status": "running",
        "namespace": "storage",
        "role": "general-purpose object storage and the DATA lake for notebooks",
        "internal": "http://minio.storage.svc.cluster.local:9000",
        "lan": f"http://s3-lan.{DOMAIN}:9000  (LoadBalancer 192.168.50.190)",
        "browser_console": f"https://minio.{DOMAIN}",
        "browser_s3_api": f"https://s3.{DOMAIN}  (external IngressClass)",
        "note": (
            "This is what marimo's AWS_ENDPOINT_URL points at, and what dagster and "
            "alphaos use. It is NOT where MLflow artifacts live."
        ),
    },
    "minio-secondary": {
        "status": "running",
        "namespace": "storage",
        "role": "artifact / backup storage; MLflow's artifact destination",
        "internal": "http://minio-secondary.storage.svc.cluster.local:9000",
        "browser_console": f"https://minio-secondary.{DOMAIN}",
        "browser_s3_api": f"https://s3-secondary.{DOMAIN}",
        "note": (
            "MLflow writes here. Notebooks should not need credentials for it, because "
            "MLflow proxies artifact traffic -- see the mlflow entry."
        ),
    },
    "postgres": {
        "status": "running",
        "namespace": "database",
        "flavour": "Crunchy Postgres Operator (PGO), HA",
        "internal": "postgres-ha.database.svc.cluster.local:5432",
        "pgbouncer_lan": "192.168.50.185:5432 (Service postgres-pgbouncer)",
        "credential_pattern": (
            "One Kubernetes Secret per database user, named postgres-pguser-<app>, "
            "surfaced through ClusterSecretStore `crunchy-pgo-secrets`. Declare the user "
            "in apps/database/crunchy-postgres/cluster/cluster.yaml FIRST -- an "
            "ExternalSecret referencing a user that has not been declared silently never "
            "renders."
        ),
        "gotchas": [
            "The primary is TLS-only: a connection string without an sslmode will fail. ",
            "ESO's Merge policy does not work against these secrets; copy the working "
            "pattern from an existing app rather than inventing one.",
        ],
    },
    "marimo": {
        "status": "running",
        "namespace": "datasci",
        "internal": "http://marimo.datasci.svc.cluster.local:2718",
        "browser": f"https://marimo.{DOMAIN}",
        "version": "0.24.0",
        "python_version": "3.13.15",
        "mcp_endpoint": "http://marimo.datasci:2718/mcp/server (bearer-token authenticated)",
        "notebook_root": "/home/marimo",
        "collections": ["pymc-marketing/", "analytics-engineering/", "scratch/"],
        "dependency_model": (
            "--sandbox: every notebook gets its own uv venv built from its own PEP 723 "
            "`# /// script` header. There is no shared image to add packages to, and "
            "editing a requirements file elsewhere will not affect a notebook."
        ),
        "limits": "8Gi memory limit for the whole pod (all open kernels share it); /tmp is a 60Gi emptyDir",
        "s3_env": {
            "AWS_ENDPOINT_URL": f"http://s3-lan.{DOMAIN}:9000",
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "from Secret marimo-s3-creds",
            "AWS_SECRET_ACCESS_KEY": "from Secret marimo-s3-creds",
        },
    },
    "dagster": {
        "status": "running",
        "namespace": "datasci",
        "internal": "http://dagster-dagster-webserver.datasci.svc.cluster.local:80",
        "browser": f"https://dagster.{DOMAIN}",
        "role": "THE sanctioned scheduler and pipeline runner for this cluster",
        "run_launcher": "K8sRunLauncher -- each run becomes a Kubernetes Job in `datasci`",
        "injected_env": {
            "MLFLOW_TRACKING_URI": "http://mlflow.datasci.svc.cluster.local:5000",
            "RAY_ADDRESS": "ray://ray-kuberay-head-svc.datasci.svc.cluster.local:10001",
            "DAGSTER_HOME": "/tmp/dagster",
        },
        "code_locations": [
            "hempriser-pipeline.development.svc.cluster.local:4000",
            "bandit-pipeline.development.svc.cluster.local:4000",
            "grid-reliance-pipeline.development.svc.cluster.local:4000",
            "alphaos-pipeline.development.svc.cluster.local:4000",
        ],
        "note": (
            "Prefect, Dask and Flyte all exist as directories in the repo but their "
            "Kustomizations are COMMENTED OUT in apps/datasci/kustomization.yaml. Dask "
            "pods are still running purely because Flux no longer manages them -- they "
            "are orphans, not a supported runtime. Do not target any of the three."
        ),
    },
    "litellm": {
        "status": "running",
        "namespace": "datasci",
        "internal": "http://litellm.datasci.svc.cluster.local:4000",
        "browser": f"https://litellm.{DOMAIN}",
        "role": "LLM gateway; the model endpoint hermes and notebooks should use",
    },
}

# --------------------------------------------------------------------------
# Buckets. Verified live; note which MinIO each lives on -- they are different
# servers with different credentials and only partly overlapping contents.
# --------------------------------------------------------------------------
BUCKETS = {
    "primary": [
        "bandit-models", "deploykf-artifacts", "flyte-metadata", "forex",
        "grid-resilience", "harbor", "hempriser", "kubeflow-pipelines",
        "medusa-uploads", "mlflow-artifacts", "postgresql", "pymc-teaching",
        "spark", "splink", "stocks-us", "supplydemand", "volsync",
    ],
    "secondary": [
        "bandit", "deploykf-artifacts", "flyte-metadata", "grid-resilience",
        "harbor", "hempriser", "kubeflow-pipelines", "medusa-uploads",
        "mlflow-artifacts", "postgresql", "spark", "splink", "volsync",
    ],
}

TEACHING_BUCKET = {
    "name": "pymc-teaching",
    "server": "minio-primary",
    "endpoint": f"http://s3-lan.{DOMAIN}:9000",
    "state": "EXISTS AND IS EMPTY (0 objects as of 2026-08-25)",
    "verdict": (
        "This is the dedicated teaching/demo bucket and it is already reachable from "
        "marimo with the credentials marimo already has. Use it. There is no need to "
        "create a new bucket, and no need to ask for one."
    ),
}

RETENTION = {
    "lifecycle_rules_configured": "NONE, on any bucket, on either MinIO server",
    "implication": (
        "Nothing expires automatically anywhere. Demo artifacts written today will "
        "still be there in a year unless something deletes them. There is no "
        "namespace TTL policy and no ResourceQuota in `datasci` either."
    ),
    "recommendation": (
        "Write demo output under a dated prefix -- s3://pymc-teaching/demo/<yyyy-mm-dd>/ "
        "-- so a lifecycle rule can be added later without having to classify existing "
        "objects. Keep MLflow demo runs inside experiments prefixed `demo-` for the same "
        "reason. Ask before adding an actual expiry rule; that is a platform decision, "
        "not a notebook one."
    ),
}

# --------------------------------------------------------------------------
# Credentials. The rule that matters: nothing is typed into a notebook, and
# nothing secret is committed. Everything arrives as an env var placed there
# by External Secrets Operator.
# --------------------------------------------------------------------------
CREDENTIALS = {
    "principle": (
        "Secrets reach workloads as environment variables injected from Kubernetes "
        "Secrets, and those Secrets are themselves generated by External Secrets "
        "Operator from Bitwarden Secrets Manager. Nothing secret is committed to git, "
        "and nothing is typed into notebook source. There is no IAM-style workload "
        "identity in this cluster."
    ),
    "stores": {
        "bitwarden-secrets-manager": "ClusterSecretStore -- the general-purpose secret backend",
        "crunchy-pgo-secrets": "ClusterSecretStore -- Postgres users only (postgres-pguser-<app>)",
        "rook ObjectBucketClaim": (
            "For a dedicated bucket + scoped keypair, create an OBC. It generates a "
            "ConfigMap (BUCKET_HOST / BUCKET_PORT / BUCKET_NAME) plus a Secret "
            "(AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY). The grid-resilience-{bronze,"
            "silver,gold} OBCs in `datasci` are the reference implementation."
        ),
    },
    "canonical_env_names": {
        "MLFLOW_TRACKING_URI": "http://mlflow.datasci.svc.cluster.local:5000 -- the ONLY var an MLflow client needs",
        "MLFLOW_S3_ENDPOINT_URL": "set on the MLflow SERVER only; a client that sets it is doing it wrong",
        "AWS_ENDPOINT_URL": f"http://s3-lan.{DOMAIN}:9000 (primary MinIO) -- already set in marimo",
        "AWS_REGION": "us-east-1 (MinIO ignores it, but boto3 refuses to start without it)",
        "AWS_ACCESS_KEY_ID": "from a Secret; already in marimo's environment",
        "AWS_SECRET_ACCESS_KEY": "from a Secret; already in marimo's environment",
        "RAY_ADDRESS": "ray://ray-kuberay-head-svc.datasci.svc.cluster.local:10001",
    },
    "existing_secrets": {
        "marimo-s3-creds": "primary MinIO keypair, mounted into marimo as AWS_* (source: bitwarden `hempriser-minio`)",
        "mlflow-minio-creds": "secondary MinIO keypair, used by the MLflow server and the Ray pods (source: bitwarden `minio-secondary-creds`)",
        "mlflow-db-secret": "assembled Postgres URI for MLflow (source: crunchy-pgo-secrets)",
    },
    "for_teaching_notebooks": (
        "A read-only teaching notebook needs NO new credential. It needs "
        "MLFLOW_TRACKING_URI (already resolvable, no auth on the tracking server) and, "
        "for data, the AWS_* variables marimo already injects. Do not mint demo "
        "credentials unless you specifically want to prove out the OBC pattern -- and "
        "note the marimo keypair is NOT read-only, so a notebook can overwrite a bucket. "
        "Confine writes to s3://pymc-teaching/."
    ),
    "bitwarden_gotcha": (
        "The `bws` CLI in this cluster is unauthenticated and `find` is not implemented "
        "on the webhook store, so Bitwarden items are UNDISCOVERABLE from inside the "
        "cluster. Worse, a MISSING FIELD renders as empty string and the ExternalSecret "
        "still reports Ready -- a green sync does not mean the value arrived. Probe a "
        "new item with a template-less ExternalSecret before depending on it."
    ),
}

# --------------------------------------------------------------------------
RUNTIMES = {
    "marimo": {
        "python": "3.13.15",
        "packaging": "PEP 723 per-notebook, built by uv into a per-session sandbox venv",
        "can_reach": "mlflow, minio (primary, credentialed), postgres, ray dashboard HTTP",
        "cannot": "act as a Ray driver -- Python 3.13 against a 3.10 cluster",
    },
    "ray_cluster": {"python": "3.10.19", "ray": "2.54.0"},
    "dagster_pipeline_images": {
        "python": "3.14-slim in build/bandit-pipeline; 3.x varies per pipeline repo",
        "packaging": "pip + requirements.txt inside a Dockerfile under build/",
    },
    "hermes": {"can_reach": "every service above; verified by direct curl on 2026-08-25"},
    "rules": [
        "uv is the standard in this environment, not Conda. marimo drives uv directly and "
        "there is no Conda anywhere in the cluster. Teach uv; translate any Conda "
        "instructions from upstream blog posts rather than copying them.",
        "There is NO shared data-science base image. Nothing in this cluster ships "
        "Graphviz, PyMC build deps, JAX/Numpyro, MLflow and Ray together. The nearest "
        "thing to a blessed image is the per-pipeline Dockerfile pattern under build/.",
        "The marimo image has no C++ compiler, so PyTensor silently falls back to its "
        "Python backend. For PyMC work pass compile_kwargs with mode NUMBA rather than "
        "reducing draws to make sampling finish.",
        "PEP 723 is the right answer for anything that runs IN marimo. An image is the "
        "right answer for anything that runs as a Dagster job or a Ray worker, because "
        "those need the dependency resolved before the process starts.",
    ],
}

PIPELINES = {
    "sanctioned": "Dagster",
    "recipe": [
        "1. Write the pipeline as a Python package with a Dagster Definitions object.",
        "2. Add a Dockerfile under build/<name>-pipeline/ whose CMD is "
        "`dagster code-server start --host 0.0.0.0 --port 4000 --module-name <pkg>.definitions`. "
        "build/bandit-pipeline/Dockerfile is the reference.",
        "3. Deploy it as a Deployment in the `development` namespace exposing port 4000 "
        "(see apps/development/grid-reliance/app/pipeline/deployment.yaml), setting "
        "DAGSTER_CURRENT_IMAGE to its own pinned image.",
        "4. Register it under dagsterWebserver.workspace.servers in "
        "apps/datasci/dagster/app/helmrelease.yaml.",
        "5. Schedules and sensors live in the Definitions; the K8sRunLauncher turns each "
        "run into a Job in `datasci` with MLFLOW_TRACKING_URI and RAY_ADDRESS already set.",
    ],
    "alternatives": {
        "Kubernetes CronJob": "fine for cluster chores; that is what cdi-stale-watchdog and "
                              "foreman-dispatch-bridge are. Not for data pipelines -- no lineage, no UI.",
        "Argo Workflows": "not installed.",
        "Prefect": "directory exists, Kustomization commented out. Not deployed.",
        "hermes cron": "documentation and scouting only. Not a data-pipeline runtime.",
    },
}

COMPUTE = {
    "quotas": "No ResourceQuota and no LimitRange exist in `datasci`. Nothing is enforced; "
              "be a good citizen rather than relying on the platform to stop you.",
    "network_policies": "None in `datasci` or `storage`. Cross-namespace traffic is open. "
                        "(kubeflow and blog have their own; they do not affect you.)",
    "nodes": "3 control-plane + worker3 + worker4. worker4 is the ROCm node.",
    "gpu": {
        "hardware": "worker4, AMD, exposed through DRA (DeviceClass gpu.amd.com), NOT a device plugin.",
        "implication": "`nvidia.com/gpu` and `amd.com/gpu` are absent from node allocatable. You "
                       "request a GPU with a DRA ResourceClaim, not a resource limit.",
        "reliability": "Fragile. CDI registration is lost whenever worker4 reboots and GPU pods then "
                       "hang in CreateContainerError indefinitely. Do NOT put a GPU on the critical "
                       "path of a teaching demo.",
    },
    "sensible_teaching_limits": "requests 500m CPU / 1Gi, limits 2 CPU / 4Gi, and keep a demo under "
                                "10 minutes. marimo's own pod ceiling is 8Gi shared across every open notebook.",
}


# --------------------------------------------------------------------------
# Live probes. Short timeouts on purpose: a tool call that hangs costs the
# agent a turn, and "I could not tell" is a more useful answer than a stall.
# --------------------------------------------------------------------------
PROBE_TARGETS = {
    "mlflow": "http://mlflow.datasci.svc.cluster.local:5000/health",
    "minio-primary": "http://minio.storage.svc.cluster.local:9000/minio/health/live",
    "minio-secondary": "http://minio-secondary.storage.svc.cluster.local:9000/minio/health/live",
    "ray": "http://ray-kuberay-head-svc.datasci.svc.cluster.local:8265/api/version",
    "marimo": "http://marimo.datasci.svc.cluster.local:2718/",
    "dagster": "http://dagster-dagster-webserver.datasci.svc.cluster.local:80/server_info",
    "litellm": "http://litellm.datasci.svc.cluster.local:4000/health/liveliness",
}


def _get(url, timeout=6):
    """GET a URL, returning (status, body). Never raises."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # A 4xx still proves the service is listening, which is what a probe asks.
        return exc.code, exc.read(4_000).decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
def tool_service_map(service=None):
    if service:
        key = service.strip().lower()
        if key not in SERVICES:
            return {
                "error": f"unknown service {service!r}",
                "known": sorted(SERVICES),
            }
        return {key: SERVICES[key]}
    return {
        "url_convention": (
            "Use the `internal` cluster-DNS URL in code and manifests. Use the `browser` "
            "URL in prose and Outline docs. The browser URLs sit behind the `internal` "
            "IngressClass with an RFC1918 source-range whitelist, so despite the https "
            "they resolve on the LAN only."
        ),
        "services": SERVICES,
    }


def tool_credentials(topic=None):
    if topic:
        key = topic.strip().lower()
        for candidate in (key, key.replace("-", "_")):
            if candidate in CREDENTIALS:
                return {candidate: CREDENTIALS[candidate]}
        return {"error": f"unknown topic {topic!r}", "known": sorted(CREDENTIALS)}
    return CREDENTIALS


def tool_storage_map(bucket=None):
    if bucket:
        name = bucket.strip()
        return {
            "bucket": name,
            "on_primary": name in BUCKETS["primary"],
            "on_secondary": name in BUCKETS["secondary"],
            "lifecycle_rules": "none",
            "teaching_bucket": TEACHING_BUCKET if name == TEACHING_BUCKET["name"] else None,
        }
    return {
        "servers": {
            "primary": SERVICES["minio-primary"],
            "secondary": SERVICES["minio-secondary"],
        },
        "buckets": BUCKETS,
        "teaching_bucket": TEACHING_BUCKET,
        "retention": RETENTION,
        "naming_convention": (
            "Flat, lowercase, hyphenated, named for the consumer or the dataset "
            "(mlflow-artifacts, grid-resilience, pymc-teaching). Medallion layering is "
            "expressed with separate OBC-managed buckets per layer "
            "(grid-resilience-bronze/silver/gold), not with prefixes inside one bucket."
        ),
    }


def tool_runtime_matrix():
    return RUNTIMES


def tool_pipeline_patterns():
    return PIPELINES


def tool_compute_limits():
    return COMPUTE


def tool_probe(target):
    key = target.strip().lower()
    if key not in PROBE_TARGETS:
        return {"error": f"unknown target {target!r}", "known": sorted(PROBE_TARGETS)}
    url = PROBE_TARGETS[key]
    status, body = _get(url)
    return {
        "target": key,
        "url": url,
        "reachable": status is not None,
        "http_status": status,
        "body_excerpt": body[:400],
    }


def tool_mlflow_experiments():
    status, body = _get(
        SERVICES["mlflow"]["internal"] + "/api/2.0/mlflow/experiments/search?max_results=200",
        timeout=10,
    )
    if status != 200:
        return {"error": "MLflow did not answer", "http_status": status, "body_excerpt": body[:400]}
    try:
        payload = json.loads(body)
    except ValueError:
        return {"error": "MLflow returned non-JSON", "body_excerpt": body[:400]}
    experiments = [
        {
            "experiment_id": e.get("experiment_id"),
            "name": e.get("name"),
            "artifact_location": e.get("artifact_location"),
            "lifecycle_stage": e.get("lifecycle_stage"),
        }
        for e in payload.get("experiments", [])
    ]
    return {
        "count": len(experiments),
        "experiments": experiments,
        "note": (
            "An artifact_location of mlflow-artifacts:/<id> means the proxy path (correct "
            "for anything new). A literal s3://... means the experiment predates the proxy "
            "switch and its clients need MinIO credentials."
        ),
    }


def tool_ray_status():
    version_status, version_body = _get(SERVICES["ray"]["internal"]["dashboard_api"] + "/api/version")
    nodes_status, nodes_body = _get(SERVICES["ray"]["internal"]["dashboard_api"] + "/nodes?view=summary", timeout=10)
    result = {
        "dashboard_reachable": version_status == 200,
        "version": version_body[:300] if version_status == 200 else None,
        "python_version_on_cluster": SERVICES["ray"]["python_version"],
        "driver_compatibility_warning": SERVICES["ray"]["gotchas"][0],
    }
    if nodes_status == 200:
        try:
            summary = json.loads(nodes_body).get("data", {}).get("summary", [])
            # Ray keeps tombstones for every autoscaled-away worker, so the raw
            # list is mostly DEAD entries with null hostnames. Report the live
            # ones and just count the rest.
            alive = [n for n in summary if n.get("raylet", {}).get("state") == "ALIVE"]
            result["nodes"] = [
                {"hostname": n.get("hostname"), "cpus": n.get("cpus")} for n in alive
            ]
            result["retired_node_records"] = len(summary) - len(alive)
        except ValueError:
            result["nodes"] = "unparseable"
    return result


TOOLS = [
    {
        "name": "service_map",
        "description": (
            "The sanctioned internal (cluster-DNS) and browser URLs for every data-science "
            "service in this cluster: MLflow, Ray, Spark, both MinIO servers, Postgres, "
            "marimo, Dagster and LiteLLM. Includes each service's backing store, version, "
            "the env vars a client should set, and the known traps. CALL THIS BEFORE "
            "WRITING ANY SERVICE URL OR TRACKING URI -- do not guess one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional. One of: " + ", ".join(sorted(SERVICES)),
                }
            },
        },
    },
    {
        "name": "credentials",
        "description": (
            "How workloads receive credentials here: External Secrets Operator over "
            "Bitwarden, the Crunchy per-user Postgres secrets, and Rook ObjectBucketClaims. "
            "Gives the canonical env-var names (MLFLOW_TRACKING_URI, AWS_ENDPOINT_URL, ...), "
            "the Secrets that already exist, and what a read-only teaching notebook actually "
            "needs (which is less than you think). Call before inventing a secret or asking "
            "for a new credential."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Optional. One of: " + ", ".join(sorted(CREDENTIALS)),
                }
            },
        },
    },
    {
        "name": "storage_map",
        "description": (
            "MinIO layout: which buckets exist on the primary vs the secondary server, the "
            "bucket naming convention, the dedicated (and currently empty) pymc-teaching "
            "bucket, and the retention situation -- which is that no lifecycle rule exists "
            "anywhere, so nothing ever expires on its own."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string", "description": "Optional bucket name to look up."}
            },
        },
    },
    {
        "name": "runtime_matrix",
        "description": (
            "Python and library versions for each place code can run (marimo, the Ray "
            "cluster, Dagster pipeline images, hermes) and the compatibility rules between "
            "them -- most importantly that marimo's Python 3.13 cannot drive the Ray "
            "cluster's Python 3.10. Also covers uv vs Conda and PEP 723 vs image-level deps."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pipeline_patterns",
        "description": (
            "The sanctioned way to run scheduled or containerized Python pipelines here: "
            "Dagster, with a concrete five-step recipe from Dockerfile to registered code "
            "location. Also says what the non-answers are (Prefect, Dask and Flyte are "
            "present in the repo but their Kustomizations are commented out)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "compute_limits",
        "description": (
            "Quotas (there are none), network policies (none in datasci or storage), node "
            "inventory, GPU availability via DRA and why a GPU should not be on a demo's "
            "critical path, plus sensible self-imposed limits for teaching workloads."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "probe",
        "description": (
            "Live reachability check against one named service, using its sanctioned "
            "internal URL. Use this to confirm a service is actually up before writing a "
            "notebook that depends on it, instead of discovering it in a failed cell."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "One of: " + ", ".join(sorted(PROBE_TARGETS)),
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "mlflow_experiments",
        "description": (
            "Live list of MLflow experiments with their artifact locations, so a demo can "
            "reuse or avoid an existing experiment rather than colliding with one."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ray_status",
        "description": (
            "Live Ray cluster state from the dashboard API -- version, nodes, CPU capacity -- "
            "together with the driver/cluster version-compatibility warning."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

HANDLERS = {
    "service_map": tool_service_map,
    "credentials": tool_credentials,
    "storage_map": tool_storage_map,
    "runtime_matrix": tool_runtime_matrix,
    "pipeline_patterns": tool_pipeline_patterns,
    "compute_limits": tool_compute_limits,
    "probe": tool_probe,
    "mlflow_experiments": tool_mlflow_experiments,
    "ray_status": tool_ray_status,
}

INSTRUCTIONS = (
    "Authoritative platform facts for this Kubernetes cluster's data-science stack. "
    "When you need a service URL, a tracking URI, a bucket name, a secret name, an env "
    "var name, a Python version, or the supported way to schedule a pipeline, call the "
    "matching tool here rather than inferring it from a blog post or from a similar "
    "cluster. Answers marked as gotchas describe mistakes that have already been made "
    "against this cluster."
)


# --------------------------------------------------------------------------
# JSON-RPC 2.0 over stdio. stdout carries protocol frames ONLY -- every
# diagnostic goes to stderr, because one stray print corrupts the stream and
# the client's failure mode is an unhelpful parse error.
# --------------------------------------------------------------------------
def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle(message):
    """Return a response dict, or None for a notification."""
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "datasci-platform", "version": "1.0.0"},
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        try:
            payload = handler(**arguments)
            is_error = isinstance(payload, dict) and "error" in payload
        except TypeError as exc:
            payload, is_error = {"error": f"bad arguments for {name}: {exc}"}, True
        except Exception as exc:  # never kill the loop over one bad call
            payload, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
            "isError": is_error,
        })

    if method is not None and method.startswith("notifications/"):
        return None

    if request_id is None:
        return None  # unknown notification: the spec says stay silent
    return _error(request_id, -32601, f"method not found: {method}")


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            print(f"[platform-mcp] unparseable frame: {exc}", file=sys.stderr, flush=True)
            continue
        try:
            response = _handle(message)
        except Exception as exc:
            print(f"[platform-mcp] handler crashed: {exc!r}", file=sys.stderr, flush=True)
            response = _error(message.get("id"), -32603, "internal error")
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
