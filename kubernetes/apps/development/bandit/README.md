# Bandit – Kubernetes manifests (copy into k8s repo)

Deployment layout aligned with the hempriser pattern for the same cluster.

## Copy into your k8s repo

1. Copy the entire `bandit-k8s` contents into your Kubernetes repo, e.g.:
   - `ks.yaml` → where your Flux root Kustomizations live (e.g. `flux/` or repo root).
   - `app/` → e.g. `kubernetes/apps/development/bandit/app/`.

2. In `ks.yaml` set:
   - **path** to the path of `app/` in your repo (e.g. `./kubernetes/apps/development/bandit/app`).
   - **sourceRef.name** to your GitRepository name (e.g. `home-kubernetes` if the k8s repo is the source).

3. Ensure **cluster-settings** ConfigMap and **cluster-secrets** Secret exist (for `postBuild.substituteFrom` so `${SECRET_DOMAIN}` etc. are substituted).

4. **Secrets**: In your secrets backend (e.g. Bitwarden), create:
   - **bandit-minio**: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` (for OBP dataset bucket).
   - Crunchy PGO: ensure secret **postgres-pguser-bandit** exists (user, password, pgbouncer-host, dbname) if you use Crunchy for Postgres.

5. **Postgres**: If you use **Crunchy PGO** only (no MoveToKube), remove `./db` from `app/kustomization.yaml` and ensure the `postgres-pguser-bandit` secret is created by your Crunchy setup. If you use **MoveToKube**, keep `app/db` and ensure `dependsOn` in `ks.yaml` matches your Postgres operator.

6. **Dagster**: After deploy, add the bandit code location in the Dagster HelmRelease workspace:
   - host: `bandit-pipeline.development.svc.cluster.local`
   - port: `4000`
   - name: `bandit-pipeline`

## Layout

- **ks.yaml** – Flux Kustomization (targetNamespace: development).
- **app/** – Kustomize app: externalsecret, pipeline (Dagster), service (FastAPI), db (optional Postgres), seldon (SeldonDeployment).

## Image tags

Update image tags in `app/service/deployment.yaml`, `app/pipeline/deployment.yaml`, and `app/seldon/seldondeployment.yaml` when you release new versions (or use a tag like `latest` / image updater).

## Image pull (ImagePullBackOff)

Pods use `imagePullSecrets: ghcr-pull` and images `ghcr.io/ekenheim/bandit-service:v0.1.0` and `ghcr.io/ekenheim/bandit-pipeline:v0.1.0`. If you see **ImagePullBackOff**:

1. **Secret** – Ensure the `ghcr-pull` secret exists in the `development` namespace (same as hempriser). It must be a `kubernetes.io/dockerconfigjson` secret for `ghcr.io` (e.g. from a GitHub PAT with `read:packages`).
2. **Images** – Build and push the bandit images to GHCR so those tags exist and are readable by the PAT used in `ghcr-pull`.

To see the exact error: `kubectl describe pod -n development -l app.kubernetes.io/name=bandit` and check the Events (e.g. 404 = image/tag missing, 401 = auth failed).
