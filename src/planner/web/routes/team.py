"""Team routes (spec section 9.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from planner.app.errors import user_message
from planner.app.ports import RepoPort
from planner.app.set_vacation import PersonNotFoundError, SetVacationUseCase
from planner.domain.intent import VacationIntent
from planner.web.deps import actor_id_from, current_user, get_repo, require_admin

router = APIRouter()


@router.get("/team", response_class=HTMLResponse)
async def team_list(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    from planner.app.admin_board import AdminBoardUseCase

    people = await repo.list_people()
    tasks = await repo.list_tasks_with_meta()
    board = AdminBoardUseCase().build(tasks=tasks, people=people, start=date.today())
    load = {r.name: r for r in board.load_rows}
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "team.html", {"people": people, "user": user, "load": load}
    )
    return response


@router.post("/team/vacation")
async def add_vacation(
    person_name: str = Form(...),
    day_from: str = Form(...),
    day_to: str = Form(...),
    capacity_h: int = Form(0),
    user: dict[str, Any] = Depends(require_admin),
    repo: RepoPort = Depends(get_repo),
) -> Response:
    intent = VacationIntent(
        person_name=person_name,
        day_from=date.fromisoformat(day_from),
        day_to=date.fromisoformat(day_to),
        capacity_h=capacity_h,
    )
    try:
        # actor_id is None for a dev-login / admin-without-Person subject; the
        # audit_log.actor_id FK is nullable, so NULL is recorded rather than a
        # forged random uuid that would violate the FK to people.id.
        await SetVacationUseCase(repo).execute(
            intent,
            actor_id_from(user),
            is_admin=bool(user.get("is_admin", False)),
        )
    except PersonNotFoundError as exc:
        return PlainTextResponse(user_message(exc), status_code=404)
    return RedirectResponse("/team", status_code=status.HTTP_303_SEE_OTHER)
