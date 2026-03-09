"""Dagster ops for the bandit refit pipeline."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import redis
import ray
import mlflow
from dagster import OpExecutionContext, op, Out, Output

from .inference import refit_experiment

logger = logging.getLogger(__name__)


def _pg_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["BANDIT_PG_DSN"])


def _redis_client() -> redis.Redis:
    return redis.Redis(
        host=os.environ.get("DRAGONFLY_HOST", "dragonfly-master.database.svc.cluster.local"),
        port=int(os.environ.get("DRAGONFLY_PORT", "6379")),
        db=int(os.environ.get("DRAGONFLY_DB", "2")),
        decode_responses=True,
    )


@op(out={"arm_stats": Out(dict)})
def fetch_reward_stats(context: OpExecutionContext) -> Output:
    """Read reward log from Postgres, aggregate by experiment and arm.

    Returns:
        {experiment_id: {arm_id: {"successes": int, "trials": int}}}
    """
    lookback_hours = int(os.environ.get("BANDIT_LOOKBACK_HOURS", "168"))  # 7 days default
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    sql = """
        SELECT
            experiment_id,
            arm_id,
            SUM(reward)::int       AS successes,
            COUNT(*)::int          AS trials
        FROM bandit_rewards
        WHERE created_at >= %s
        GROUP BY experiment_id, arm_id
        ORDER BY experiment_id, arm_id
    """

    stats: dict[str, dict[str, dict]] = {}
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (since,))
            for exp_id, arm_id, successes, trials in cur.fetchall():
                stats.setdefault(exp_id, {})[arm_id] = {
                    "successes": successes,
                    "trials": trials,
                }
    finally:
        conn.close()

    total_trials = sum(
        v["trials"] for exp in stats.values() for v in exp.values()
    )
    context.log.info(
        "Fetched reward stats: %d experiments, %d total trials since %s",
        len(stats),
        total_trials,
        since.isoformat(),
    )
    return Output(stats, output_name="arm_stats")


@op(out={"posteriors": Out(dict)})
def run_pymc_refit(context: OpExecutionContext, arm_stats: dict) -> Output:
    """Submit PyMC inference to Ray workers and collect posterior summaries."""
    ray_address = os.environ.get("RAY_ADDRESS", "auto")
    if not ray.is_initialized():
        ray.init(address=ray_address, ignore_reinit_error=True)

    # Decorate inference function at runtime so Ray workers run it
    remote_refit = ray.remote(refit_experiment)

    futures = {
        exp_id: remote_refit.remote(exp_id, exp_arm_stats)
        for exp_id, exp_arm_stats in arm_stats.items()
    }

    posteriors: dict[str, list] = {}
    for exp_id, future in futures.items():
        result = ray.get(future)
        posteriors[exp_id] = result
        context.log.info(
            "PyMC refit complete for experiment '%s': %d arms",
            exp_id,
            len(result),
        )

    return Output(posteriors, output_name="posteriors")


@op
def write_posteriors_to_store(context: OpExecutionContext, posteriors: dict) -> None:
    """Write updated Beta(alpha, beta) params to DragonflyDB."""
    r = _redis_client()
    pipe = r.pipeline()
    for exp_id, arm_results in posteriors.items():
        for arm in arm_results:
            alpha_key = f"bandit:{exp_id}:{arm.arm_id}:alpha"
            beta_key = f"bandit:{exp_id}:{arm.arm_id}:beta"
            pipe.set(alpha_key, max(1, int(arm.alpha)))
            pipe.set(beta_key, max(1, int(arm.beta)))
    pipe.execute()

    total = sum(len(v) for v in posteriors.values())
    context.log.info("Wrote updated priors for %d arms across %d experiments", total, len(posteriors))


@op
def log_to_mlflow(context: OpExecutionContext, posteriors: dict) -> None:
    """Log per-arm posterior metrics to MLflow under experiment 'bandit-refit'."""
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow.datasci.svc.cluster.local:5000"))
    mlflow.set_experiment("bandit-refit")

    for exp_id, arm_results in posteriors.items():
        with mlflow.start_run(run_name=f"{exp_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M')}"):
            mlflow.set_tag("experiment_id", exp_id)
            mlflow.set_tag("n_arms", str(len(arm_results)))

            for arm in arm_results:
                prefix = f"arm_{arm.arm_id}"
                mlflow.log_metrics({
                    f"{prefix}_alpha": arm.alpha,
                    f"{prefix}_beta": arm.beta,
                    f"{prefix}_mean": arm.mean,
                    f"{prefix}_hdi_low": arm.hdi_low,
                    f"{prefix}_hdi_high": arm.hdi_high,
                    f"{prefix}_n_successes": float(arm.n_successes),
                    f"{prefix}_n_trials": float(arm.n_trials),
                })

            context.log.info(
                "Logged MLflow run for experiment '%s' with %d arms",
                exp_id,
                len(arm_results),
            )
