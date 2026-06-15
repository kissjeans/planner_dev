"""Login routes: Telegram widget callback -> JWT cookie (spec section 9.2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.web.auth import (
    COOKIE_NAME,
    JWT_TTL_HOURS,
    create_jwt,
    verify_telegram_login,
)

router = APIRouter()

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _set_session_cookie(resp: RedirectResponse, token: str) -> None:
    resp.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax", max_age=JWT_TTL_HOURS * 3600
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "login.html", {}
    )
    return response


@router.get("/dev-login")
async def dev_login(request: Request) -> RedirectResponse:
    """Local-only admin login bypass (Telegram widget needs a public domain).

    Enabled only when ``DEBUG=true`` AND the request comes from loopback;
    returns 404 otherwise so it can never grant admin over the network.
    """
    client_host = request.client.host if request.client else ""
    if not request.app.state.debug or client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    claims = {"sub": "dev", "name": "Dev Admin", "tg_id": 0, "is_admin": True}
    token = create_jwt(claims, request.app.state.jwt_secret)
    resp = RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(resp, token)
    return resp


@router.get("/login/telegram")
async def login_callback(request: Request) -> RedirectResponse:
    data = dict(request.query_params)
    if not verify_telegram_login(data, request.app.state.bot_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Подпись недействительна.")

    tg_id = int(data["id"])
    repo = request.app.state.repo
    person = await repo.get_person_by_tg_id(tg_id)
    is_admin = (person.is_admin if person else False) or (
        tg_id in request.app.state.admin_ids
    )

    claims = {
        "sub": str(person.id) if person else f"tg:{tg_id}",
        "name": person.name if person else data.get("first_name", "—"),
        "tg_id": tg_id,
        "is_admin": is_admin,
    }
    token = create_jwt(claims, request.app.state.jwt_secret)

    resp = RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(resp, token)
    return resp


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE_NAME)
    return resp
