"""Bandit service — FastAPI hot path for Bayesian Thompson Sampling.

Endpoints:
  GET  /select   — select an arm for an experiment (Thompson sampling)
  POST /reward   — record a reward signal
  GET  /arms     — list cached arm state for an experiment
  GET  /health   — liveness probe
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from . import db, store
from .bandit import thompson_select
from .strapi import fetch_active_arms

logger = logging.getLogger(__name__)

# In-memory cache: experiment_id → {arm_id: {strapi_id, content_ref}}
_arm_cache: dict[str, dict[str, dict]] = {}


def _sync_arms_from_strapi(experiment_id: str | None = None) -> None:
    """Fetch active arms from Strapi and seed DragonflyDB priors."""
    arms = fetch_active_arms(experiment_id)
    for arm in arms:
        exp = arm.experiment_id
        if exp not in _arm_cache:
            _arm_cache[exp] = {}
        _arm_cache[exp][arm.arm_id] = {
            "strapi_id": arm.id,
            "content_ref": arm.content_ref,
            "name": arm.name,
        }
        store.seed_arm(exp, arm.arm_id)
    logger.info("Synced %d arms across %d experiments", len(arms), len(_arm_cache))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        db.ensure_schema()
    except Exception:
        logger.warning("Could not create DB schema on startup — will retry on first reward")
    _sync_arms_from_strapi()
    yield


app = FastAPI(title="Bandit Service", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RewardRequest(BaseModel):
    experiment_id: str
    arm_id: str
    reward: int  # 1 = success/conversion, 0 = no conversion
    context: dict[str, Any] | None = None


class SelectResponse(BaseModel):
    experiment_id: str
    arm_id: str
    strapi_content_id: int
    strapi_content_ref: str
    alpha: int
    beta: int


class RewardResponse(BaseModel):
    status: str
    experiment_id: str
    arm_id: str
    reward: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/select", response_model=SelectResponse)
def select(
    experiment_id: str = Query(..., description="Experiment identifier"),
    refresh: bool = Query(False, description="Re-sync arms from Strapi before selecting"),
) -> SelectResponse:
    if refresh or experiment_id not in _arm_cache:
        _sync_arms_from_strapi(experiment_id)

    arm_meta = _arm_cache.get(experiment_id, {})
    if not arm_meta:
        raise HTTPException(
            status_code=404,
            detail=f"No active arms found for experiment '{experiment_id}'. "
            "Check Strapi for published Arm entries with matching experiment_id.",
        )

    result = thompson_select(experiment_id, arm_meta)
    return SelectResponse(
        experiment_id=result.experiment_id,
        arm_id=result.arm_id,
        strapi_content_id=result.strapi_content_id,
        strapi_content_ref=result.strapi_content_ref,
        alpha=result.alpha,
        beta=result.beta,
    )


@app.post("/reward", response_model=RewardResponse)
def reward(req: RewardRequest) -> RewardResponse:
    if req.reward not in (0, 1):
        raise HTTPException(status_code=422, detail="reward must be 0 or 1")

    # Fast conjugate update in DragonflyDB
    if req.reward == 1:
        store.increment_alpha(req.experiment_id, req.arm_id)
    else:
        store.increment_beta(req.experiment_id, req.arm_id)

    # Durable reward log in Postgres (best-effort — don't fail the response)
    try:
        db.log_reward(req.experiment_id, req.arm_id, req.reward, req.context)
    except Exception:
        logger.exception("Postgres reward log failed — DragonflyDB update succeeded")

    return RewardResponse(
        status="ok",
        experiment_id=req.experiment_id,
        arm_id=req.arm_id,
        reward=req.reward,
    )


@app.get("/arms")
def list_arms(experiment_id: str = Query(...)) -> dict:
    arm_meta = _arm_cache.get(experiment_id, {})
    result = {}
    for arm_id, meta in arm_meta.items():
        alpha, beta = store.get_arm_params(experiment_id, arm_id)
        result[arm_id] = {**meta, "alpha": alpha, "beta": beta, "mean": alpha / (alpha + beta)}
    return {"experiment_id": experiment_id, "arms": result}


@app.post("/sync")
def sync_arms(experiment_id: str | None = None) -> dict:
    """Force re-sync arms from Strapi. Called by Dagster after content changes."""
    _sync_arms_from_strapi(experiment_id)
    return {"status": "ok", "experiments": list(_arm_cache.keys())}
