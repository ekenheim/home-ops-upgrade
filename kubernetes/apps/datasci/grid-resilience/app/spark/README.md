# Grid Reliance SparkApplications

Placeholder SparkApplication manifests for ERA5 and ENTSO-E ingest. The Spark operator runs in **datasci** (`jobNamespaces: ["datasci"]`).

## Setup

1. **Image and main class:** Replace `spec.image` and `spec.mainApplicationFile` (and `spec.mainClass` for JVM apps) with the actual image and entrypoint from the [grid-reliance data-engineering](https://github.com/ekenheim/grid-reliance/tree/main/data-engineering) build (e.g. from `data-engineering/processing/`).
2. **Credentials:** Driver and executor use `envFrom` with the OBC-created secrets (`grid-resilience-bronze`, `grid-resilience-silver`) in datasci. Ensure those secrets exist (created by the ObjectBucketClaim controller).
3. **Optimization:** Apply the Spark optimization checklist from the deployment plan (section 5.1): driver/executor resources, memory overhead, parallelism, shuffle partitions, S3A config for Ceph RGW.

## Files

- `spark-era5-ingest.yaml` — placeholder for ERA5 weather ingest into Bronze.
- `spark-entsoe-ingest.yaml` — placeholder for ENTSO-E grid data ingest into Bronze.

Apply or commit the manifests and run/schedule via `kubectl apply` or your GitOps pipeline.
