"""Plan routes (spec section 9.1)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.app.add_project import deserialize_plan
from planner.app.ports import RepoPort
from planner.app.render.gantt import render_gantt
from planner.domain.models import Assignment
from planner.web.deps import actor_id_from, current_user, get_repo, require_admin

router = APIRouter()


@router.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/plan", response_class=HTMLResponse)
async def plan_list(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    projects = await repo.list_projects()
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "plan.html", {"projects": projects, "user": user}
    )
    return response


@router.get("/plan/{project_id}", response_class=HTMLResponse)
async def plan_detail(
    project_id: UUID,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    tasks = await repo.list_project_tasks(project_id)
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "plan_detail.html",
        {"project_id": project_id, "tasks": tasks, "user": user},
    )
    return response


@router.get("/plan/{project_id}/gantt.png")
async def plan_gantt_png(
    project_id: UUID,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> Response:
    """Gantt timeline PNG for a project's committed plan (spec 7.4 / 4.5)."""
    pv = await repo.get_committed_plan(project_id)
    assignments = list(deserialize_plan(pv.payload).assignments) if pv else []
    if not assignments:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет утверждённого плана.")

    names = await repo.get_task_name_map()
    origin = min(a.start_date for a in assignments)

    def label_for(a: Assignment) -> str:
        return names.get(a.task_id) or str(a.task_id)[:8]

    png = render_gantt(assignments, origin, label_for=label_for)
    return Response(content=png, media_type="image/png")


@router.post("/plan/{project_id}/task/{task_id}/edit")
async def edit_task(
    project_id: UUID,
    task_id: UUID,
    start: str = Form(""),
    end: str = Form(""),
    user: dict[str, Any] = Depends(require_admin),
    repo: RepoPort = Depends(get_repo),
) -> RedirectResponse:
    await repo.update_task_schedule(
        task_id,
        date.fromisoformat(start) if start else None,
        date.fromisoformat(end) if end else None,
        None,
    )
    await repo.add_audit(
        actor_id_from(user), "edit_task", "task", task_id, {"start": start, "end": end}
    )
    return RedirectResponse("/plan", status_code=status.HTTP_303_SEE_OTHER)
