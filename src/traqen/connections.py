"""The OAuth flow for connecting a user's GitHub or Jira account.

Started from a link card ("Connect GitHub") when that provider is configured on
the server; there is no connections page for now (see TODO.md).
"""

import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db as dbm
from . import integrations
from .auth import current_user, get_db, safe_next

router = APIRouter()


def _provider(provider: str) -> str:
    if provider not in integrations.PROVIDERS:
        raise HTTPException(404, "Unknown connection")
    return provider


@router.get("/connections/{provider}/start")
def start(
    request: Request,
    provider: str,
    next: str = "/",
    user=Depends(current_user),
):
    provider = _provider(provider)
    settings = request.app.state.settings
    if integrations.client_for(settings, provider) is None:
        raise HTTPException(404, "This connection isn’t set up on this server")
    state = secrets.token_urlsafe(32)
    request.session["connect"] = {
        "provider": provider,
        "state": state,
        "next": safe_next(next),
    }
    return RedirectResponse(integrations.authorize_url(settings, provider, state), 303)


@router.get("/connections/{provider}/callback")
async def callback(
    request: Request,
    provider: str,
    code: str = "",
    state: str = "",
    error: str = "",
    user=Depends(current_user),
    db=Depends(get_db),
):
    provider = _provider(provider)
    pending = request.session.pop("connect", None)
    label = integrations.PROVIDERS[provider]
    if (
        error
        or not code
        or not pending
        or pending["provider"] != provider
        or not secrets.compare_digest(state, pending["state"])
    ):
        if error == "access_denied":
            raise HTTPException(
                400,
                f"{label} didn’t grant access. If your organization restricts "
                "apps, an admin may need to approve Traqen first.",
            )
        raise HTTPException(400, f"Connecting {label} was cancelled or expired.")

    settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            tokens = await integrations.exchange_code(settings, provider, code, client)
        except (integrations.OAuthError, httpx.HTTPError) as exc:
            raise HTTPException(502, str(exc) or f"Connecting {label} failed.") from exc

    with dbm.transaction(db):
        integrations.save_connection(
            db, request.app.state.cipher, user["id"], provider, tokens
        )
        # Links that asked for this connection get checked again.
        db.execute(
            """
            UPDATE links SET checked_at = NULL
            WHERE live_connect = ?
              AND traq_id IN (SELECT id FROM traqs WHERE user_id = ?)
            """,
            (provider, user["id"]),
        )
    return RedirectResponse(pending["next"], 303)
