# Kubernetes deployment handoff — feedback for the Grid Reliance team

We’ve deployed the Grid Reliance platform to our cluster following your [Kubernetes deployment handoff](https://github.com/ekenheim/grid-reliance/) and the full-platform plan. This document summarizes what’s in place, what we had to change, and what we need from you to get everything stable.

---

## What we deployed

| Component | Location | Status / notes |
|-----------|----------|----------------|
| **API** | `development` namespace | Deployed; was crashing (see below). We added a workaround. |
| **Pipeline** (Dagster code server) | `development` namespace | Deployed; registered as code location in our existing Dagster (datasci). |
| **Dashboard** | `development` namespace | **Scaled to 0 replicas** — image `ghcr.io/ekenheim/grid-resilience-dashboard:v1.0.0` not found (ImagePullBackOff). We’ll set replicas back to 1 when the image is published. |
| **Generator** (one-shot Job) | `development` namespace | **Not deployed** — image `ghcr.io/ekenheim/grid-resilience-generator:v1.0.0` not found. We commented it out of the kustomization until the image is available. |
| **Postgres** | Crunchy (shared cluster) | User/database `gridreliance` created; ExternalSecrets sync credentials. |
| **MinIO / object storage** | Bitwarden → Secret | We use a Bitwarden secret `grid-reliance-minio` (MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY). Single bucket `grid-resilience` for now. |
| **Dagster** | `datasci` namespace | Your pipeline is added as a code location; run launcher passes `grid-reliance-minio`, `grid-reliance-postgres`, and `RAY_ADDRESS` to jobs. |
| **Spark** | `datasci` namespace | Placeholder SparkApplications (ERA5 / ENTSO-E ingest) added under our existing Spark operator; they reference OBC bucket secrets. Image and mainApplicationFile are placeholders until you provide the real data-engineering image and entrypoints. |

All app images we use are tagged **v1.0.0** (e.g. `ghcr.io/ekenheim/grid-resilience-api:v1.0.0`).

---

## Issue 1: API container exits immediately (ModuleNotFoundError)

**What we see**

The API pod starts, then exits with **Exit Code 1** after about a second. Logs show:

```text
File "/app/main.py", line 18, in <module>
  from routers import forecast, correlations
File "/app/routers/forecast.py", line 7, in <module>
  from api.data import get_forecast_for_region
ModuleNotFoundError: No module named 'api'
```

So the process fails at import time before serving any traffic. This is independent of MinIO or other env vars.

**What we did on our side**

We added **`PYTHONPATH=/app`** to the API deployment’s env so that when the app runs, Python can resolve the `api` package (we assume the image has something like `/app/api/` and `/app/main.py`). That may or may not fix it depending on how the image is built and how uvicorn is invoked.

**What we need from you**

1. **Confirm the intended run environment**  
   How is the API meant to be run inside the container (e.g. `uvicorn main:app`, or `uvicorn api.main:app`, working directory, any PYTHONPATH)? If the Dockerfile or entrypoint sets a specific CWD or module path, we should align with that.

2. **Fix the image if the layout doesn’t match**  
   If `api` is not a top-level package under the app root, the image needs to be fixed so that:
   - either the code uses imports that match the actual layout (e.g. relative imports or a package name that exists), or  
   - the container is started with the right working directory and/or PYTHONPATH so that `from api.data import ...` works.

3. **Optional: health check that doesn’t depend on full app load**  
   Right now we use `GET /` for liveness/readiness. If you can add a minimal health endpoint that doesn’t import heavy or optional modules, we can point probes there so the pod isn’t killed during slow or conditional imports.

---

## Issue 2: MinIO endpoint template (resolved on our side)

We initially templated the secret as `MINIO_ENDPOINT: "http://{{ .MINIO_ENDPOINT }}"`. If Bitwarden already stores the full URL (e.g. `http://minio.svc:9000`), that produced `http://http://...` and would break the client. We changed the ExternalSecret to pass **`MINIO_ENDPOINT` through as-is** (no prefix). So in your Bitwarden (or equivalent) entry, store the full URL in `MINIO_ENDPOINT`. No change required in the API code for this.

---

## Issue 3: Dashboard and Generator images not found

We expect:

- **Dashboard:** `ghcr.io/ekenheim/grid-resilience-dashboard:v1.0.0`  
- **Generator:** `ghcr.io/ekenheim/grid-resilience-generator:v1.0.0`

We got **ImagePullBackOff** for both. Your handoff only mentioned API and Pipeline images on GHCR, so we’re not sure if these are published yet or under different names/tags.

**What we did**

- Set the **Dashboard** deployment to **0 replicas** so we don’t keep pulling. We’ll set it back to 1 when the image is available.
- **Generator** is commented out of the kustomization so the Job isn’t applied. We’ll re-enable it when the image exists.

**What we need from you**

- Confirm whether you publish Dashboard and Generator to GHCR (and under which image names and tags), or if we should build them ourselves from the repo and use our own registry.

---

## Issue 4: SparkApplications (placeholders)

We added placeholder SparkApplication manifests for ERA5 and ENTSO-E ingest in `datasci`, using your existing ObjectBucketClaims (bronze/silver/gold). The placeholders use:

- Image: `ghcr.io/ekenheim/grid-reliance-data-engineering:v1.0.0`  
- `mainApplicationFile`: e.g. `local:///app/era5_ingest.py` (and similar for ENTSO-E)

We don’t know if that image or those paths exist. We left a short README in the spark folder asking our team to replace image and mainApplicationFile with the real ones from your `data-engineering/` (or for you to provide example SparkApplication YAMLs).

**What we need from you**

- Either publish a data-engineering image and document the correct image name, tag, and main application file (or main class for JVM), or share example SparkApplication YAMLs we can drop in.

---

## Summary of changes we made (for your reference)

- **Postgres:** Created user/database `gridreliance` (no underscore) so our Crunchy convention matches; ExternalSecrets use key `postgres-pguser-gridreliance`.
- **API deployment:** Added env `PYTHONPATH=/app` to work around `ModuleNotFoundError: No module named 'api'`; increased liveness/readiness initial delays and failure thresholds so slow startup doesn’t kill the pod.
- **ExternalSecret (MinIO):** Stopped prefixing `http://` to `MINIO_ENDPOINT`; we now pass the value from the secret store through unchanged (store full URL in the secret).
- **Dashboard:** Set replicas to 0 until the dashboard image is available.
- **Generator:** Removed from the kustomization until the generator image is available.
- **Dagster:** Your pipeline is registered as a code location; run launcher injects `grid-reliance-minio`, `grid-reliance-postgres`, and `RAY_ADDRESS` for jobs.

If you’d like, we can add a short “K8s deployment” section to your main docs or README that links to this file and to the handoff. We’re happy to adjust anything on our side once we have the correct image names, tags, and run/import expectations for the API.
