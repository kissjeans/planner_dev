"""FastAPI dependencies: repo access and JWT-cookie auth (spec section 9)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from planner.app.ports import RepoPort
from planner.web.auth import COOKIE_NAME, decode_jwt


def get_repo(request: Request) -> RepoPort:
    repo: RepoPort = request.app.state.repo
    return repo


def get_jwt_secret(request: Request) -> str:
    secret: str = request.app.state.jwt_secret
    return secret


def current_user(
    request: Request, secret: str = Depends(get_jwt_secret)
) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    claims = decode_jwt(token, secret) if token else None
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Не авторизован.")
    return claims


def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Только админ.")
    return user


def actor_id_from(user: dict[str, Any]) -> UUID | None:
    """Return the actor's person UUID from the JWT ``sub``, or None when the
    subject is not a real person id (e.g. the dev-login ``sub='dev'`` or a
    ``tg:<id>`` subject for a user not yet in the team)."""
    try:
        return UUID(str(user.get("sub", "")))
    except (ValueError, TypeError):
        return None
