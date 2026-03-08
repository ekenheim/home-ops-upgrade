"""Thompson Sampling selector — pure hot path, no Ray dependency."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from . import store

logger = logging.getLogger(__name__)

_rng = np.random.default_rng()


@dataclass
class SelectionResult:
    arm_id: str
    strapi_content_id: int
    strapi_content_ref: str
    experiment_id: str
    alpha: int
    beta: int


def thompson_select(experiment_id: str, arm_meta: dict[str, dict]) -> SelectionResult:
    """Draw one Thompson sample per arm and return the winning arm.

    arm_meta: {arm_id: {"strapi_id": int, "content_ref": str}}
    """
    arm_ids = list(arm_meta.keys())
    if not arm_ids:
        raise ValueError(f"No active arms for experiment '{experiment_id}'")

    alphas, betas = [], []
    for arm_id in arm_ids:
        a, b = store.get_arm_params(experiment_id, arm_id)
        alphas.append(a)
        betas.append(b)

    samples = _rng.beta(alphas, betas)
    winner_idx = int(np.argmax(samples))
    winner_id = arm_ids[winner_idx]
    meta = arm_meta[winner_id]

    logger.debug(
        "Thompson select: experiment=%s winner=%s alpha=%d beta=%d",
        experiment_id,
        winner_id,
        alphas[winner_idx],
        betas[winner_idx],
    )

    return SelectionResult(
        arm_id=winner_id,
        strapi_content_id=meta["strapi_id"],
        strapi_content_ref=meta["content_ref"],
        experiment_id=experiment_id,
        alpha=alphas[winner_idx],
        beta=betas[winner_idx],
    )
