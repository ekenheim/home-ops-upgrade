# Ray Experiment Runbook

This runbook documents how to launch and validate Ray subsystem examples in this repo.

## Profiles

- CPU profile (safe default): `kubernetes/apps/datasci/ray/examples/cpu`
- GPU profile (optional): `kubernetes/apps/datasci/ray/examples/gpu`

The GPU profile is intentionally isolated so non-GPU clusters are not impacted.

## Launch

Run from repo root.

```sh
kubectl apply -k kubernetes/apps/datasci/ray/examples/cpu
```

Optional GPU experiments:

```sh
kubectl apply -k kubernetes/apps/datasci/ray/examples/gpu
```

## Check Job Status

```sh
kubectl -n datasci get jobs -l app.kubernetes.io/name=ray-examples
kubectl -n datasci get pods -l app.kubernetes.io/name=ray-examples
```

## Logs by Subsystem

```sh
kubectl -n datasci logs job/ray-core-smoke
kubectl -n datasci logs job/ray-data-smoke
kubectl -n datasci logs job/ray-train-smoke
kubectl -n datasci logs job/ray-tune-smoke
kubectl -n datasci logs job/ray-serve-smoke
kubectl -n datasci logs job/ray-rllib-smoke
```

## Prometheus Validation Queries

Use Prometheus expression browser or Grafana Explore.

- Core: `sum(ray_scheduler_tasks{ray_io_cluster=~".+"}) by (State)`
- Data: `sum(ray_data_num_tasks_running{ray_io_cluster=~".+"})`
- Train API presence: `count({__name__=~"ray_train_.*",ray_io_cluster=~".+"})`
- Tune presence: `count({__name__=~"ray_tune_.*",ray_io_cluster=~".+"})`
- Serve: `count({__name__=~"ray_serve_.*",ray_io_cluster=~".+"})`
- RLlib presence: `count({__name__=~"ray_rllib_.*|rllib_.*",ray_io_cluster=~".+"})`

## Known Empty Panels (Expected)

These can legitimately show `No Data` when idle or when a feature is unused:

- GPU utilization and GRAM panels when no GPU nodes are present.
- OOM failure panels unless memory pressure events have occurred.
- Ray Data panels until a Data workload has run.
- Tune/Train/RLlib metric-family panels if framework-specific runs have not emitted metrics.
- Some task/actor-by-name panels when there are no active named tasks/actors.

## Cleanup

```sh
kubectl -n datasci delete job -l app.kubernetes.io/name=ray-examples
kubectl -n datasci delete pod -l app.kubernetes.io/name=ray-examples --ignore-not-found=true
```
