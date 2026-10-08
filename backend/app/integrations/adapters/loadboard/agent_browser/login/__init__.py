"""Scripted logins per load source.

Each module exposes an async ``login(page, credentials: dict) -> dict``
that drives raw Playwright calls (never the agent tool-loop), inspects
the resulting page for a 2FA / verification prompt, and returns the
Playwright ``storage_state()`` on success.

Standing rule: ``credentials`` is a dict pulled from
:class:`app.identity.credentials.CredentialVault`
(connector ``loadboard_login``, kind ``password``). The LLM never sees
these values — the loop in :mod:`..agent` has no ``type`` or ``click``
verb.
"""

from __future__ import annotations

from typing import Any


class LoginChallenge(RuntimeError):
    """Raised when a login attempt lands on 2FA / verification / captcha.

    The caller logs ``status="login_challenge"`` and stops. A webhook /
    operator notification is explicitly out of v1 — a yellow source chip
    on ``/loads`` is enough (plan amendment #2).
    """


CHALLENGE_SIGNALS = (
    "verify",
    "verification code",
    "code sent",
    "two-factor",
    "2fa",
    "captcha",
    "are you a robot",
)


def looks_like_challenge(text: str) -> bool:
    lo = (text or "").lower()
    return any(sig in lo for sig in CHALLENGE_SIGNALS)


async def default_detect(page: Any) -> None:
    """Common helper: ``raise LoginChallenge`` if the current page smells like one."""
    try:
        content = await page.content()
    except Exception:
        return
    if looks_like_challenge(content):
        raise LoginChallenge("login_challenge_detected")


__all__ = ["LoginChallenge", "default_detect", "looks_like_challenge"]
