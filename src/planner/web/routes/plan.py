"""Plan routes (spec section 9.1)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.app.ports import RepoPort
from planner.web.deps import current_user, get_repo, require_admin

router = APIRouter()


@router.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/plan", response_class=HTMLResponse)
async def plan_list(
    request: Request,
    user: dict = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    projects = await repo.list_projects()
    return request.app.state.templates.TemplateResponse(
        request, "plan.html", {"projects": projects, "user": user}
    )


@router.post("/plan/{project_id}/task/{task_id}/edit")
async def edit_task(
    project_id: UUID,
    task_id: UUID,
    start: str = Form(""),
    end: str = Form(""),
    user: dict = Depends(require_admin),
    repo: RepoPort = Depends(get_repo),
) -> RedirectResponse:
    await repo.update_task_schedule(
        task_id,
        date.fromisoformat(start) if start else None,
        date.fromisoformat(end) if end else None,
        None,
    )
    await repo.add_audit(
        None, "edit_task", "task", task_id, {"start": start, "end": end}
    )
    return RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)
