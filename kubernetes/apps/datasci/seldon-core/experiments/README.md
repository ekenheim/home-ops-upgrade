# Seldon Core v2 Experiments

Seldon v2 `Experiment` CRs provide static percentage traffic splitting between Models.
They are **not** the bandit itself — the adaptive Thompson Sampling logic lives in
`bandit-serve` (development namespace, port 8000).

## When to use an Experiment

- Compare two bandit algorithms (Thompson Sampling vs epsilon-greedy) in production
- Run a shadow/mirror test of a new bandit configuration without impacting users
- Use Seldon's built-in observability (request/response logging via the Seldon scheduler)

## Prerequisites

1. Seldon v2 operator deployed (`seldon-core-v2-setup` HelmRelease reconciled)
2. Two `Model` CRs deployed in `datasci` pointing to your bandit variants
3. `SeldonRuntime` running (scheduler + envoy healthy)

## Current experiments

| File | Purpose | Status |
|---|---|---|
| `bandit-ab-experiment.yaml` | Compare Thompson vs epsilon-greedy bandits | Template — not applied |

## Applying an experiment

```bash
kubectl apply -f bandit-ab-experiment.yaml -n datasci
kubectl get experiment bandit-algorithm-comparison -n datasci
```
