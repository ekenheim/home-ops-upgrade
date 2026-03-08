"""PyMC Beta posterior refit — runs on Ray workers (PyMC/nutpie/arviz pre-installed).

Imported by the Dagster op and submitted as a Ray remote function so the heavy
MCMC work executes on Ray workers, not the Dagster run pod.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ArmPosterior:
    arm_id: str
    alpha: float
    beta: float
    mean: float
    hdi_low: float
    hdi_high: float
    n_successes: int
    n_trials: int


def refit_experiment(
    experiment_id: str,
    arm_stats: dict[str, dict],
    prior_alpha: float = 2.0,
    prior_beta: float = 2.0,
    samples: int = 500,
    tune: int = 500,
) -> list[ArmPosterior]:
    """Fit Beta posteriors for all arms via PyMC MCMC.

    arm_stats: {arm_id: {"successes": int, "trials": int}}

    Uses a weakly-informative Beta(prior_alpha, prior_beta) prior.
    Returns posterior summaries to be written back to DragonflyDB.

    This function is decorated with @ray.remote by the caller so it
    executes on Ray workers where PyMC/nutpie/arviz are pre-installed.
    """
    import numpy as np
    import pymc as pm
    import arviz as az

    arm_ids = list(arm_stats.keys())
    n_arms = len(arm_ids)

    successes = np.array([arm_stats[a]["successes"] for a in arm_ids], dtype=int)
    trials = np.array([arm_stats[a]["trials"] for a in arm_ids], dtype=int)

    # Guard: arms with no observations use the prior directly
    if trials.sum() == 0:
        return [
            ArmPosterior(
                arm_id=a,
                alpha=prior_alpha,
                beta=prior_beta,
                mean=prior_alpha / (prior_alpha + prior_beta),
                hdi_low=0.0,
                hdi_high=1.0,
                n_successes=0,
                n_trials=0,
            )
            for a in arm_ids
        ]

    with pm.Model():
        p = pm.Beta("p", alpha=prior_alpha, beta=prior_beta, shape=n_arms)
        pm.Binomial("obs", n=trials, p=p, observed=successes)
        trace = pm.sample(
            samples,
            tune=tune,
            chains=2,
            progressbar=False,
            random_seed=42,
            nuts_sampler="nutpie",  # nutpie is pre-installed on Ray workers
        )

    posterior = trace.posterior["p"]
    hdi = az.hdi(trace, var_names=["p"], hdi_prob=0.94)["p"].values

    results = []
    for i, arm_id in enumerate(arm_ids):
        mean_p = float(posterior[:, :, i].mean())
        # Convert posterior mean back to effective Beta(alpha, beta) params
        # so DragonflyDB stays in the same representation as the hot path
        n_eff = trials[i] + prior_alpha + prior_beta
        results.append(
            ArmPosterior(
                arm_id=arm_id,
                alpha=float(mean_p * n_eff),
                beta=float((1 - mean_p) * n_eff),
                mean=mean_p,
                hdi_low=float(hdi[i, 0]),
                hdi_high=float(hdi[i, 1]),
                n_successes=int(successes[i]),
                n_trials=int(trials[i]),
            )
        )

    return results
