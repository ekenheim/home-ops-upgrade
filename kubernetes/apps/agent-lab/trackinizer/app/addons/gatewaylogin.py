"""Browser sign-in behind envoy-internal for this cluster's Trackinizer.

The image mounts no sign-in provider, so a browser never gets the
``trackinizer_session`` cookie that workspaces, chats and presets require
(``require_browser`` refuses API keys). Envoy signs the browser in against
Authentik and injects the gateway's ``browser`` key on one path only:
``/api/addons/gatewaylogin/login``. This route trades that key for a session
cookie and redirects back, so every other request runs on the session.

Only the key named ``browser`` is accepted. A session acts with the user's
role, not the key's, so letting any key in (the factory's writer key, say)
would turn it into an admin session.
"""

from __future__ import annotations

import os
from dataclasses import field
from typing import Annotated

from configgle import Fig
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from trackinizer.addons.addon import AddonManifest
from trackinizer.addons.deployment import Deployment
from trackinizer.server.api._deps import get_store
from trackinizer.server.auth import AuthIdentity, require_role
from trackinizer.server.config import session_max_age_from_env
from trackinizer.server.session import set_session_cookie

KEY_NAME = "browser"

router = APIRouter()


def _safe_next(target: str) -> str:
    # Same-origin paths only: no scheme, no host, no protocol-relative //.
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/app/"


@router.get("/login")
async def login(
    request: Request,
    identity: Annotated[AuthIdentity, Depends(require_role("viewer"))],
    next: str = "/app/",  # noqa: A002 -- the login page's own query name
) -> RedirectResponse:
    target = _safe_next(next)
    if identity.api_key_id is None:
        # Already a session; nothing to trade.
        return RedirectResponse(target, status_code=303)
    secret = os.environ.get("TRACKINIZER_SESSION_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="session login is not configured")
    async with get_store(request).engine.acquire() as conn:
        name = await conn.fetchval(
            "SELECT name FROM api_keys WHERE id = $1 AND revoked_at IS NULL",
            identity.api_key_id,
        )
    if name != KEY_NAME:
        raise HTTPException(status_code=403, detail="only the gateway key may sign in")
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(
        response,
        user_id=str(identity.user_id),
        secret=secret,
        max_age_seconds=session_max_age_from_env(),
    )
    return response


class GatewayLogin:
    class Config(Fig["GatewayLogin"]):
        pass

    def __init__(self, config: Config) -> None:
        del config

    def manifest(self) -> AddonManifest:
        return AddonManifest(
            title="Gateway login",
            description="Trades the gateway's browser key for a session cookie.",
            routers=(router,),
        )


class Site(Deployment.Config):
    gatewaylogin: GatewayLogin.Config = field(default_factory=GatewayLogin.Config)
    """Browser sign-in behind envoy-internal."""


def deployment() -> Site:
    return Site()
