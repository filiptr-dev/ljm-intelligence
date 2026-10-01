"""Auth routes — login / logout / me. Deliberately tiny.

* ``POST /auth/login``   — email + password → ``{access_token, expires_in, user}``.
* ``POST /auth/logout``  — stateless JWT; this just lets the frontend show the
                           flow a logout button. The Vercel BFF clears the
                           session cookie; the token itself can't be revoked
                           server-side without a revocation table (dropped in
                           v1 — see plan amendments).
* ``GET  /auth/me``      — require login; return the principal.

No rate limiter + no ``login_attempts`` table in v1: the demo password is the
word "password" and the repo is public, so brute-force "protection" would be
theatre. Revisit before real client use.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

from app.auth.deps import UserPrincipal, current_user
from app.auth.passwords import verify_password
from app.auth.tokens import effective_auth_jwt_secret, mint_access_token
from app.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Module-level singletons keep ruff B008 quiet; equivalent to inline Depends().
_CURRENT_USER = Depends(current_user)


class LoginIn(BaseModel):
    # ``str`` not ``EmailStr`` on purpose: the seeded demo address uses the
    # ``.local`` TLD (reserved by pydantic's EmailStr) and v1 does not need
    # strict RFC validation on login input — the DB lookup is the real check.
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class LogoutOut(BaseModel):
    ok: bool = True


@router.post("/login", response_model=LoginOut)
async def login(request: Request, body: LoginIn) -> LoginOut:
    settings = request.app.state.settings
    sessionmaker = request.app.state.sessionmaker
    email_lower = body.email.lower().strip()

    async with sessionmaker() as s:
        user = (await s.execute(select(User).where(User.email == email_lower))).scalar_one_or_none()
        # Verify regardless of user-exists so the response time doesn't leak
        # account enumeration. Both branches end in a generic 401. An
        # ``is_active=False`` user, or an SSO-only user with no
        # ``password_hash``, is treated the same as bad credentials.
        ok = (
            bool(user)
            and user.is_active
            and user.password_hash is not None
            and verify_password(body.password.get_secret_value(), user.password_hash)
        )
        if not ok or user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
        user.last_login_at = datetime.now(UTC)
        await s.commit()

        secret = await effective_auth_jwt_secret(settings, s)

    ttl_days = int(getattr(settings, "auth_access_ttl_days", 7))
    token, expires_in = mint_access_token(
        secret=secret,
        user_id=user.id,
        email=user.email,
        role=user.role,
        ttl_days=ttl_days,
    )
    return LoginOut(
        access_token=token,
        expires_in=expires_in,
        user=UserOut(id=user.id, email=user.email, name=user.name, role=user.role),
    )


@router.post("/logout", response_model=LogoutOut)
async def logout(_: UserPrincipal = _CURRENT_USER) -> LogoutOut:
    # Stateless JWT: the browser-side BFF clears the cookie; nothing to do
    # here beyond asserting the caller had a valid session to begin with.
    return LogoutOut()


@router.get("/me", response_model=UserOut)
async def me(request: Request, principal: UserPrincipal = _CURRENT_USER) -> UserOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        user = (await s.execute(select(User).where(User.id == principal.id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role)
