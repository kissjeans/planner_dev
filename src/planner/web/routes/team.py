"""Team routes (spec section 9.1)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.app.ports import PersonRecord, RepoPort
from planner.app.set_vacation import SetVacationUseCase
from planner.domain.intent import VacationIntent
from planner.web.deps import current_user, get_repo, require_admin

router = APIRouter()


def _actor(user: dict[str, Any]) -> PersonRecord:
    sub = user.get("sub", "")
    try:
        pid = UUID(sub)
    except (ValueError, TypeError):
        pid = uuid4()
    return PersonRecord(
        id=pid, name=user.get("name", "—"), is_admin=bool(user.get("is_admin", False))
    )


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
) -> RedirectResponse:
    intent = VacationIntent(
        person_name=person_name,
        day_from=date.fromisoformat(day_from),
        day_to=date.fromisoformat(day_to),
        capacity_h=capacity_h,
    )
    await SetVacationUseCase(repo).execute(intent, _actor(user))
    return RedirectResponse("/team", status_code=status.HTTP_303_SEE_OTHER)
