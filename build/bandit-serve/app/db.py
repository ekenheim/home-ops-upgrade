"""Postgres reward event log."""
from __future__ import annotations

import logging
import os

import psycopg2
import psycopg2.pool

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.SimpleConnectionPool | None = None

_DDL = """
CREATE TABLE IF NOT EXISTS bandit_rewards (
    id          BIGSERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    arm_id      TEXT NOT NULL,
    reward      SMALLINT NOT NULL CHECK (reward IN (0, 1)),
    context     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bandit_rewards_experiment
    ON bandit_rewards (experiment_id, arm_id, created_at DESC);
"""


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ["BANDIT_DATABASE_URL"]
        _pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=dsn)
    return _pool


def ensure_schema() -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    finally:
        pool.putconn(conn)


def log_reward(
    experiment_id: str, arm_id: str, reward: int, context: dict | None = None
) -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            import json
            cur.execute(
                """
                INSERT INTO bandit_rewards (experiment_id, arm_id, reward, context)
                VALUES (%s, %s, %s, %s)
                """,
                (experiment_id, arm_id, reward, json.dumps(context) if context else None),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to log reward to Postgres")
    finally:
        pool.putconn(conn)
