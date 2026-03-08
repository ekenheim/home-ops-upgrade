"""Dagster Definitions for the bandit pipeline code location."""
from dagster import Definitions, ScheduleDefinition, define_asset_job, job

from .ops import (
    fetch_reward_stats,
    log_to_mlflow,
    run_pymc_refit,
    write_posteriors_to_store,
)


@job(name="bandit_refit_job", description="Bayesian posterior refit for all active bandit experiments")
def bandit_refit_job():
    stats = fetch_reward_stats()
    posteriors = run_pymc_refit(stats)
    write_posteriors_to_store(posteriors)
    log_to_mlflow(posteriors)


bandit_refit_hourly = ScheduleDefinition(
    job=bandit_refit_job,
    cron_schedule="0 * * * *",  # every hour
    name="bandit_refit_hourly",
    description="Hourly Bayesian posterior refit — reads reward log, refits via PyMC on Ray, updates DragonflyDB, logs to MLflow",
)

defs = Definitions(
    jobs=[bandit_refit_job],
    schedules=[bandit_refit_hourly],
)
