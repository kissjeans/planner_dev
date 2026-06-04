"""Team routes (spec section 9.1)."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.app.ports import PersonRecord, RepoPort
from planner.app.set_vacation import PersonNotFoundError, SetVacationUseCase
from planner.domain.intent import VacationIntent
from planner.web.deps import current_user, get_repo, require_admin

router = APIRouter()


def _actor(user: dict) -> PersonRecord:
    sub = user.get("sub", "")
    try:
        pid = UUID(sub)
    except (ValueError, TypeError):
        pid = uuid4()
    return PersonRecord(id=pid, name=user.get("name", "—"), is_admin=True)


@router.get("/team", response_class=HTMLResponse)
async def team_list(
    request: Request,
    user: dict = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    people = await repo.list_people()
    return request.app.state.templates.TemplateResponse(
        request, "team.html", {"people": people, "user": user}
    )


@router.post("/team/vacation")
async def add_vacation(
    person_name: str = Form(...),
    day_from: str = Form(...),
    day_to: str = Form(...),
    capacity_h: int = Form(0),
    user: dict = Depends(require_admin),
    repo: RepoPort = Depends(get_repo),
) -> RedirectResponse:
    intent = VacationIntent(
        person_name=person_name,
        day_from=date.fromisoformat(day_from),
        day_to=date.fromisoformat(day_to),
        capacity_h=capacity_h,
    )
    try:
        await SetVacationUseCase(repo).execute(intent, _actor(user))
    except PersonNotFoundError:
        pass  # swallow: person not on team -> nothing to schedule
    return RedirectResponse("/team", status_code=status.HTTP_303_SEE_OTHER)
