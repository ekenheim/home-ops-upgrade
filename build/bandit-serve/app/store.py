"""DragonflyDB (Redis-compatible) arm state store.

Key schema:
  bandit:{experiment_id}:arms          → Hash {arm_id: 1, ...}  (registry)
  bandit:{experiment_id}:{arm_id}:alpha → integer count
  bandit:{experiment_id}:{arm_id}:beta  → integer count
"""
from __future__ import annotations

import os
import redis

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=os.environ["DRAGONFLY_HOST"],
            port=int(os.environ.get("DRAGONFLY_PORT", "6379")),
            db=int(os.environ.get("DRAGONFLY_DB", "2")),
            decode_responses=True,
        )
    return _client


def seed_arm(experiment_id: str, arm_id: str) -> None:
    """Register an arm with Beta(1,1) prior if it doesn't already exist."""
    r = get_client()
    alpha_key = f"bandit:{experiment_id}:{arm_id}:alpha"
    beta_key = f"bandit:{experiment_id}:{arm_id}:beta"
    arms_key = f"bandit:{experiment_id}:arms"
    pipe = r.pipeline()
    pipe.hsetnx(arms_key, arm_id, "1")
    pipe.setnx(alpha_key, 1)
    pipe.setnx(beta_key, 1)
    pipe.execute()


def get_arm_params(experiment_id: str, arm_id: str) -> tuple[int, int]:
    """Return (alpha, beta) for an arm."""
    r = get_client()
    alpha = int(r.get(f"bandit:{experiment_id}:{arm_id}:alpha") or 1)
    beta = int(r.get(f"bandit:{experiment_id}:{arm_id}:beta") or 1)
    return alpha, beta


def get_all_arm_ids(experiment_id: str) -> list[str]:
    r = get_client()
    return list(r.hkeys(f"bandit:{experiment_id}:arms"))


def increment_alpha(experiment_id: str, arm_id: str) -> None:
    get_client().incr(f"bandit:{experiment_id}:{arm_id}:alpha")


def increment_beta(experiment_id: str, arm_id: str) -> None:
    get_client().incr(f"bandit:{experiment_id}:{arm_id}:beta")


def set_arm_params(experiment_id: str, arm_id: str, alpha: float, beta: float) -> None:
    """Write updated posterior params from PyMC refit."""
    r = get_client()
    pipe = r.pipeline()
    pipe.set(f"bandit:{experiment_id}:{arm_id}:alpha", max(1, int(alpha)))
    pipe.set(f"bandit:{experiment_id}:{arm_id}:beta", max(1, int(beta)))
    pipe.execute()
