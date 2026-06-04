"""FastAPI app factory (spec section 9). Shares the app/ layer with the bot."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from planner.app.ports import RepoPort
from planner.settings import Settings
from planner.web.routes import audit, auth, plan, team

_TEMPLATES = Path(__file__).parent / "templates"


def create_app(repo: RepoPort, settings: Settings) -> FastAPI:
    app = FastAPI(title="planner admin")
    app.state.repo = repo
    app.state.jwt_secret = settings.jwt_secret
    app.state.bot_token = settings.bot_token
    app.state.admin_ids = settings.admin_id_set
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES))

    app.include_router(auth.router)
    app.include_router(plan.router)
    app.include_router(team.router)
    app.include_router(audit.router)
    return app
