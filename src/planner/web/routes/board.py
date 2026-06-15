"""Admin board routes: Schedule, Calendar, Load — client xlsx vision."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from planner.app.admin_board import AdminBoardUseCase, Board
from planner.app.ports import PersonRecord, RepoPort
from planner.web.deps import actor_id_from, current_user, get_repo, require_admin

router = APIRouter()


async def _build_board(repo: RepoPort) -> tuple[Board, list[PersonRecord]]:
    tasks = await repo.list_tasks_with_meta()
    people = await repo.list_people()
    board = AdminBoardUseCase().build(tasks=tasks, people=people, start=date.today())
    return board, people


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    board, people = await _build_board(repo)
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
    board, _ = await _build_board(repo)
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
    board, _ = await _build_board(repo)
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
    try:
        tid, pid = UUID(task_id), UUID(person_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bad id") from exc
    moved = await repo.set_task_assignee(tid, pid)
    if moved:
        await repo.add_audit(
            actor_id_from(user), "reassign_task", "task", tid, {"person_id": person_id}
        )
    return RedirectResponse("/schedule", status_code=status.HTTP_303_SEE_OTHER)
