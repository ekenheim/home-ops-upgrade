"""Strapi CMS client — fetches active Arm entries."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get(
    "STRAPI_URL", "http://strapi-cms.development.svc.cluster.local:1337"
)
_API_TOKEN = os.environ.get("STRAPI_API_TOKEN", "")


@dataclass
class Arm:
    id: int
    arm_id: str
    experiment_id: str
    content_ref: str
    content_type: str | None
    name: str
    is_active: bool


def _headers() -> dict[str, str]:
    if _API_TOKEN:
        return {"Authorization": f"Bearer {_API_TOKEN}"}
    return {}


def fetch_active_arms(experiment_id: str | None = None) -> list[Arm]:
    """Return all published, active Arm entries from Strapi, optionally filtered by experiment."""
    params: dict[str, str] = {
        "filters[is_active][$eq]": "true",
        "publicationState": "live",
        "pagination[pageSize]": "100",
    }
    if experiment_id:
        params["filters[experiment_id][$eq]"] = experiment_id

    try:
        resp = httpx.get(
            f"{_BASE_URL}/api/arms",
            params=params,
            headers=_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch arms from Strapi: %s", exc)
        return []

    arms: list[Arm] = []
    for entry in resp.json().get("data", []):
        attrs = entry.get("attributes", entry)
        arms.append(
            Arm(
                id=entry["id"],
                arm_id=str(entry["id"]),
                experiment_id=attrs.get("experiment_id", "default"),
                content_ref=attrs.get("content_ref", ""),
                content_type=attrs.get("content_type"),
                name=attrs.get("name", f"arm-{entry['id']}"),
                is_active=attrs.get("is_active", True),
            )
        )
    logger.info("Fetched %d active arms from Strapi", len(arms))
    return arms
