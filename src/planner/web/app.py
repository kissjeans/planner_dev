"""FastAPI app factory (spec section 9). Shares the app/ layer with the bot."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates

from planner.app.confirm_plan import PlanNotFoundError, PlanNotProposedError
from planner.app.errors import user_message
from planner.app.ports import RepoPort
from planner.app.set_vacation import PersonNotFoundError
from planner.settings import Settings
from planner.web.routes import audit, auth, board, plan, team

_TEMPLATES = Path(__file__).parent / "templates"


def create_app(repo: RepoPort, settings: Settings) -> FastAPI:
    app = FastAPI(title="planner admin")
    app.state.repo = repo
    app.state.jwt_secret = settings.jwt_secret
    app.state.bot_token = settings.bot_token
    app.state.admin_ids = settings.admin_id_set
    app.state.debug = settings.debug
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES))

    app.include_router(auth.router)
    app.include_router(plan.router)
    app.include_router(board.router)
    app.include_router(team.router)
    app.include_router(audit.router)

    @app.exception_handler(PermissionError)
    async def _forbidden(_request: Request, exc: PermissionError) -> PlainTextResponse:
        return PlainTextResponse(user_message(exc), status_code=403)

    @app.exception_handler(PlanNotFoundError)
    @app.exception_handler(PersonNotFoundError)
    async def _not_found(_request: Request, exc: Exception) -> PlainTextResponse:
        return PlainTextResponse(user_message(exc), status_code=404)

    @app.exception_handler(PlanNotProposedError)
    async def _conflict(_request: Request, exc: Exception) -> PlainTextResponse:
        return PlainTextResponse(user_message(exc), status_code=409)

    @app.exception_handler(ValueError)
    async def _bad_request(_request: Request, _exc: ValueError) -> PlainTextResponse:
        return PlainTextResponse("Некорректные данные в запросе.", status_code=400)

    return app
