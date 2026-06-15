"""Admin board routes: Schedule, Calendar, Load — client xlsx vision."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.app.admin_board import AdminBoardUseCase, Board
from planner.app.ports import RepoPort
from planner.web.deps import current_user, get_repo, require_admin

router = APIRouter()


async def _build_board(repo: RepoPort) -> Board:
    tasks = await repo.list_tasks_with_meta()
    people = await repo.list_people()
    return AdminBoardUseCase().build(tasks=tasks, people=people, start=date.today())


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    board = await _build_board(repo)
    people = await repo.list_people()
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "schedule.html", {"board": board, "people": people, "user": user}
    )
    return response


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    board = await _build_board(repo)
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "calendar.html", {"board": board, "user": user}
    )
    return response


@router.get("/load-board", response_class=HTMLResponse)
async def load_board_page(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    board = await _build_board(repo)
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "load.html", {"board": board, "user": user}
    )
    return response


@router.post("/schedule/reassign")
async def reassign(
    task_id: str = Form(...),
    person_id: str = Form(""),
    user: dict[str, Any] = Depends(require_admin),
    repo: RepoPort = Depends(get_repo),
) -> RedirectResponse:
    if not person_id.strip():
        return RedirectResponse("/schedule", status_code=status.HTTP_303_SEE_OTHER)
    tid, pid = UUID(task_id), UUID(person_id)
    moved = await repo.set_task_assignee(tid, pid)
    if moved:
        await repo.add_audit(
            None, "reassign_task", "task", tid, {"person_id": person_id}
        )
    return RedirectResponse("/schedule", status_code=status.HTTP_303_SEE_OTHER)
