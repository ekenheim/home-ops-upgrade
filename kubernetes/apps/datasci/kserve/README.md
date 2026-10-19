# KServe: model serving for `datasci`

KServe is the cluster's platform for serving classic ML models (sklearn,
xgboost, lightgbm, or any MLflow pyfunc model in a container). It replaces
Seldon Core. LLMs are out of scope here: they run on LLMKube on worker4 behind
LiteLLM (`apps/llm`).

Nothing is served yet. With no models, the cost is a single controller pod.

## Layout

| Path | What it is |
|------|------------|
| `ks.yaml` | Two Flux Kustomizations: `kserve-crds` (`wait: true`), and `kserve`, which depends on `kserve-crds` and cert-manager |
| `crds/helmrelease.yaml` | `kserve-crd` chart: the InferenceService, ServingRuntime, ClusterServingRuntime, InferenceGraph, TrainedModel and ClusterStorageContainer CRDs |
| `app/helmrelease.yaml` | `kserve-resources` chart: the controller, its webhooks, and the `inferenceservice-config` ConfigMap (Standard mode, Gateway API on `network/envoy-internal`, MinIO for S3) |
| `app/runtimes-helmrelease.yaml` | `kserve-runtime-configs` chart: the ClusterServingRuntimes. Only sklearn, xgboost and lightgbm are enabled |
| `examples/` | A smoke-test InferenceService (public sklearn iris). Flux does **not** apply it |

The three charts come from `oci://ghcr.io/kserve/charts`
(`flux/repositories/helm/kserve.yaml`). They share one version. The Renovate
group `KServe` moves all three in one PR, and every KServe image takes its tag
from that version.

## Why KServe (decided 2026-10-19)

Seldon Core went to BSL 1.1 in 2024, and it only ever served the shelved
bandit project. The table compares what was available on 2026-10-19:

| | KServe 0.21 (Standard mode) | Ray Serve on KubeRay | BentoML / Yatai | `mlflow models serve` containers | Triton standalone |
|---|---|---|---|---|---|
| Latest release | v0.21.0, 2026-09-25 (v0.20.0 was 2026-08-06) | KubeRay v1.7.1, 2026-09-21; Ray 2.59 | BentoML 1.4.39, 2026-05-07; **Yatai archived** (last release 2023-10) | MLflow 3.17.1, 2026-10-13 | 2.73.0, 2026-09-30 (monthly) |
| Licence | Apache-2.0, CNCF incubating | Apache-2.0 | Apache-2.0 | Apache-2.0 | BSD-3-Clause |
| New footprint | 1 controller pod, 6 CRDs, 7 webhooks (scoped to KServe objects) | None: the operator and the RayService CRD are already installed. But a declarative RayService brings its **own** RayCluster (a head that idles at about 1.3Gi), and Serve apps on the shared `ray-kuberay` cluster are lost on every Recreate | None on the cluster, but no Kubernetes control plane without Yatai | None; you write the Deployment, Service and HTTPRoute for each model | One heavy server pod (the image is many GB), GPU-oriented |
| MLflow registry | Points `storageUri` at the model's artifact dir in MinIO. The sklearn and xgboost flavour files load as they are; pyfunc models need a build-docker image | Arbitrary Python: `mlflow.pyfunc.load_model` inside a deployment | You re-package the model as a Bento | Native, since it is MLflow's own server | Needs ONNX / TorchScript / TensorRT exports |
| Ingress fit | Writes its own HTTPRoute on `envoy-internal` | Hand-written HTTPRoute to the Serve port | Hand-written | Hand-written | Hand-written |
| Autoscaling | HPA per model. Scale-to-zero only with Knative or KEDA, neither of which runs here | Serve replica autoscaling inside the Ray cluster | Yatai only | HPA by hand | HPA by hand |
| A/B and canary | `spec.canary[]` with `trafficPercent`, applied as HTTPRoute weights, with promote and rollback | Incremental upgrade moves traffic old→new only, with no arbitrary weights. Otherwise a router in your own code | Not without Yatai | HTTPRoute weights by hand | HTTPRoute weights by hand |
| GPU | Optional | Optional | Optional | Optional | Wants one |
| Risk here | One small controller and webhooks scoped to KServe objects | worker3 is at about 90% of its memory requested, which cannot spare a head per service | Unmaintained control plane | Every model is boilerplate | Heavy image for small tabular models |

The choice is KServe in Standard mode, for four reasons:
- It is declarative: one InferenceService per model, in Git or applied by hand.
- It creates the Gateway API routes itself.
- Canary with weights is built in. That covers the weighted split that the bandit Seldon `Experiment` did.
- With no models, the cost is one small pod.

Ray Serve is still available for Python-heavy model composition: see
`../ray/EXAMPLES.md`.

One Seldon piece survives inside KServe. `modelFormat: mlflow` maps to the
`kserve-mlserver` runtime, which is `seldonio/mlserver`. That server's last
release was June 2025, and it only claims MLflow model format versions 1 and 2.
That runtime is **disabled** here (`app/runtimes-helmrelease.yaml`). MLflow
models go through KServe's own runtimes or a build-docker image instead, as
the next section describes.

## Serving an MLflow-registered model

MLflow (3.17) stores artifacts through its proxy in
`s3://mlflow-artifacts/` on minio-secondary. A registered model version's
source is a `models:/m-<id>` URI, so first resolve it to the S3 directory.
Run this from Jupyter, or from the mlflow pod:

```python
from mlflow import MlflowClient
c = MlflowClient("http://mlflow.datasci.svc.cluster.local:5000")
c.get_model_version_download_uri("diabetes-elasticnet", "2")
# 's3://mlflow-artifacts/34/models/m-931ce002874e4cdd84c8f706bd479444/artifacts'
```

That directory holds `MLmodel`, `requirements.txt` and the flavour's model
file. Which runtime can load it depends on the flavour:

| MLflow flavour | File it writes | KServe runtime | Caveat |
|---|---|---|---|
| `mlflow.sklearn` (default cloudpickle) | `model.pkl` | `sklearn`, run by `kserve-sklearnserver` | The server has scikit-learn ~1.5.1 (KServe 0.21). Train with a matching sklearn: a pickle from a much older or newer sklearn can fail to load or warn. `serialization_format="skops"` writes `model.skops`, which the server does not read |
| `mlflow.xgboost` | `model.ubj` | `xgboost`, run by `kserve-xgbserver` (xgboost ~2.1.1) | none |
| `mlflow.lightgbm` | `model.lgb` | `lightgbm`, run by `kserve-lgbserver` | The server only loads `*.bst`. Also log `booster.save_model("model.bst")` as an artifact and point `storageUri` at that directory |
| any other flavour, or pyfunc | (anything) | your own container | See [Arbitrary pyfunc models](#arbitrary-pyfunc-models) |

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: diabetes
  namespace: datasci
  annotations:
    # S3 credentials for the storage-initializer. The secret is created by
    # mlflow's ExternalSecret. The endpoint and http settings come from
    # app/helmrelease.yaml (kserve.storage.s3).
    serving.kserve.io/secretName: mlflow-minio-creds
spec:
  predictor:
    minReplicas: 1
    maxReplicas: 2          # HPA on CPU; set maxReplicas == minReplicas to pin
    model:
      modelFormat:
        name: sklearn
      protocolVersion: v2   # optional; v1 is the default
      storageUri: s3://mlflow-artifacts/34/models/m-931ce002874e4cdd84c8f706bd479444/artifacts
      resources:
        requests: {cpu: 50m, memory: 256Mi}
        limits: {memory: 1Gi}
```

Put the manifest in Git under `kubernetes/apps/datasci/<model>/` with its own
`ks.yaml` (dependsOn `kserve`) when the model should stay deployed. Otherwise
`kubectl apply` it while you experiment. Always set `resources`: otherwise
the model gets the defaults from `app/helmrelease.yaml`
(`kserve.inferenceservice.resources`: 100m / 256Mi requested).

Nothing schedules on worker4: it is tainted, and InferenceServices carry no
toleration. Keep it that way.

### Arbitrary pyfunc models

MLflow serves any model with its own FastAPI server (`/invocations`, port
8080 in the built image):

```sh
mlflow models build-docker -m "models:/<name>/<version>" -n harbor.<domain>/datasci/<name>:<version>
docker push harbor.<domain>/datasci/<name>:<version>
```

Then use a custom-container predictor:

```yaml
spec:
  predictor:
    containers:
      - name: kserve-container
        image: harbor.<domain>/datasci/<name>:<version>
        ports: [{containerPort: 8080, protocol: TCP}]
        resources: {requests: {cpu: 100m, memory: 512Mi}, limits: {memory: 2Gi}}
```

The routing and canary sections below work the same way. Clients call
`/invocations` instead of the KServe protocol paths.

## Routing

The controller writes HTTPRoutes on `network/envoy-internal`. They attach to
the `https` listener, under the `*.${SECRET_DOMAIN}` certificate.
external-dns-unifi publishes the hostnames. Cloudflare only publishes
`envoy-external` routes, so nothing here becomes public.

| Route (in `datasci`) | Hostname | Backend |
|---|---|---|
| `<isvc>` | `<isvc>-datasci.${SECRET_DOMAIN}`, the one in `status.url` | the predictor, or the transformer if there is one |
| `<isvc>-predictor` | `<isvc>-predictor-datasci.${SECRET_DOMAIN}` | the predictor |

In-cluster callers skip the gateway and use
`http://<isvc>-predictor.datasci.svc.cluster.local` (port 80). The request
paths are:
- protocol v1: `POST /v1/models/<isvc>:predict`
- protocol v2: `POST /v2/models/<isvc>/infer` and `GET /v2/models/<isvc>/ready`

To keep a model off the gateway entirely, label it
`networking.kserve.io/visibility: cluster-local`.

There is no UI, so no Authentik is needed. To restrict an endpoint to private
CIDRs, the way `mlflow-allow-private` does, point a SecurityPolicy's
`targetRefs` at the generated route by name (`<isvc>`). The controller copies
the InferenceService's annotations onto its routes, so `gethomepage.dev/*`
annotations work too.

## Canary and A/B

Both Standard mode and the HTTPRoutes support named canaries (KServe 0.20+).
Each canary gets its own Deployment and Service. Its `trafficPercent` becomes
a weighted `backendRef` on the routes, and stable gets the rest.

```yaml
spec:
  predictor:
    minReplicas: 2
    model:
      modelFormat: {name: sklearn}
      storageUri: s3://mlflow-artifacts/.../m-<v1>/artifacts
  canary:
    - trafficPercent: 20
      predictor:
        name: v2                         # required, unique
        model:
          modelFormat: {name: sklearn}
          storageUri: s3://mlflow-artifacts/.../m-<v2>/artifacts
```

The rules, from the validating webhook:
- `trafficPercent` totals at most 100.
- The canary must not set `maxReplicas` or other autoscaling fields.
- A canary's replicas come out of stable's `minReplicas`, and its own `minReplicas`, if set, must be lower than stable's. Give stable at least 2.

To promote, set `spec.predictor.name: v2` and the v2 `storageUri`, then
delete the `canary` entry. The canary Deployment becomes stable without a
restart. To roll back, delete the `canary` entry.

There is no canary example under `examples/`. The kubeconform schema
(datree catalog) predates `spec.canary` and would fail CI.

Two things the Seldon `Experiment` had that KServe canary does not:
- **Shadow / mirror traffic**: label both InferenceServices
  `networking.kserve.io/visibility: cluster-local`, then hand-write one
  HTTPRoute on envoy-internal that sends traffic to the A predictor Service
  and copies it to B with a `RequestMirror` filter (`percent` sets the
  share).
- **Two independent models, e.g. two algorithms, in a 50/50 split**: use the
  same hand-written route with two weighted `backendRefs`, one per
  `<isvc>-predictor` Service.

## Smoke test

```sh
kubectl apply -k kubernetes/apps/datasci/kserve/examples
kubectl -n datasci get isvc sklearn-iris -w
curl -s https://sklearn-iris-datasci.${SECRET_DOMAIN}/v1/models/sklearn-iris:predict \
  -H 'Content-Type: application/json' -d '{"instances": [[6.8, 2.8, 4.8, 1.4]]}'
kubectl delete -k kubernetes/apps/datasci/kserve/examples
```

## Operations

- **Controller down**: InferenceService create, update and delete is rejected,
  because the webhooks are `failurePolicy: Fail`. New pods of existing models
  are rejected too, through the pod mutator on pods labelled
  `serving.kserve.io/inferenceservice`. Running models keep serving. No
  other workload is affected.
- **Upgrades**: Renovate's `KServe` group bumps all three charts. The CRDs
  are templates, so they upgrade with the chart. Read the release notes for
  InferenceService API changes before merging.
- **Turning on a runtime** (Triton, MLServer, ...): set `disabled: false` in
  `app/runtimes-helmrelease.yaml`.
