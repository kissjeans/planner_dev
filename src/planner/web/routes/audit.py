"""Audit route (spec section 9.1 / 17)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from planner.app.ports import RepoPort
from planner.web.deps import current_user, get_repo

router = APIRouter()


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
    entries = await repo.list_audit(limit=limit, offset=offset)
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "audit.html", {"entries": entries, "user": user}
    )
    return response
