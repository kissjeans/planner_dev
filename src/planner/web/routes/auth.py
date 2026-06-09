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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "login.html", {}
    )
    return response


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
    resp.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax", max_age=JWT_TTL_HOURS * 3600
    )
    return resp


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE_NAME)
    return resp
