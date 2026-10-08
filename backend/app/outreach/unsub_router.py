"""Outreach unsubscribe router — the public /unsubscribe surface.

Split from `app/api/enrichment.py` during the 2026-10-08 onion/SOLID
refactor. Unsubscribe is an outreach concern (CAN-SPAM, RFC 8058), not
enrichment — the fold-in table in the plan explicitly separates them.

The handlers still delegate to `app.integrations.enrichment_service`
because that's where the suppression write + token verify live. Routes
and behaviour are byte-identical to the previous location.
"""
from __future__ import annotations

import html as _htmllib

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.integrations.enrichment_service import (
    InvalidUnsubscribeTokenError,
    NotFoundError,
    UnsubscribeConfigError,
    apply_unsubscribe as svc_apply_unsubscribe,
    verify_token as svc_verify_token,
)


class UnsubscribeOut(BaseModel):
    ok: bool
    email: str | None
    already: bool


unsub_router = APIRouter(tags=["unsubscribe"])


async def _verify_or_400(request: Request, token: str):
    try:
        return await svc_verify_token(
            request.app.state.sessionmaker, request.app.state.settings, token
        )
    except UnsubscribeConfigError as exc:
        raise HTTPException(400, "unsubscribe not configured") from exc
    except InvalidUnsubscribeTokenError as exc:
        raise HTTPException(400, "invalid unsubscribe token") from exc


@unsub_router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm_page(request: Request, t: str = Query(..., min_length=1)) -> HTMLResponse:
    """GET renders a confirm page — NEVER mutates.

    Email security scanners (Microsoft Safe Links, Google, Proofpoint) auto-fetch
    every URL in every outbound email. If GET mutated, one scanned inbox would
    unsubscribe the recipient before they read the message. Confirm-then-POST is
    the CAN-SPAM one-click contract (RFC 8058); we honor it.
    """
    _ = await _verify_or_400(request, t)
    t_safe = _htmllib.escape(t, quote=True)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Unsubscribe — LJM International</title>"
        "<meta name='robots' content='noindex,nofollow'>"
        "</head><body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:1rem'>"
        "<h1>Unsubscribe from LJM International outreach</h1>"
        "<p>Click the button below to stop all future emails to this address.</p>"
        f"<form method='POST' action='/unsubscribe?t={t_safe}'>"
        "<button type='submit' style='padding:0.75rem 1.5rem;font-size:1rem;"
        "background:#0a0a0a;color:#fff;border:0;border-radius:4px'>Confirm unsubscribe</button>"
        "</form>"
        "</body></html>"
    )
    return HTMLResponse(html)


@unsub_router.post("/unsubscribe", response_model=UnsubscribeOut)
async def unsubscribe_post(request: Request, t: str = Query(..., min_length=1)) -> UnsubscribeOut:
    """POST performs the suppression. Same route also accepts RFC 8058
    ``List-Unsubscribe=One-Click`` payloads via ``?t=<token>`` on the query
    string. Invalid / forged token → 400 with zero side effects."""
    target = await _verify_or_400(request, t)
    try:
        result = await svc_apply_unsubscribe(request.app.state.sessionmaker, target)
    except NotFoundError as exc:
        raise HTTPException(404, "contact not found") from exc
    return UnsubscribeOut(ok=result.ok, email=result.email, already=result.already)


# Backward-compat alias in case anything still imports `router` from here.
router = unsub_router
