"""End-to-end web admin tests via FastAPI TestClient + fake repo (spec 9 / 5.8)."""

import hashlib
import hmac
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from planner.app.ports import AuditRecord, PersonRecord, ProjectRecord
from planner.settings import Settings
from planner.web.app import create_app
from planner.web.auth import COOKIE_NAME, create_jwt

BOT = "123456:TEST-TOKEN"
JWT_SECRET = "test-secret"


class WebFakeRepo:
    def __init__(self) -> None:
        self.overrides: list = []
        self.audits: list = []
        self.people = {"Айгуль": PersonRecord(id=uuid4(), name="Айгуль")}

    async def list_projects(self):
        return [ProjectRecord(uuid4(), "Альфа", "planning", None)]

    async def list_people(self):
        return list(self.people.values())

    async def list_audit(self, limit=50, offset=0):
        return [AuditRecord(created_at="2026-06-05", action="confirm_plan",
                            entity_type="plan_version")]

    async def get_person_by_tg_id(self, tg_user_id: int):
        return None

    async def get_person_by_name(self, name: str):
        return self.people.get(name)

    async def upsert_day_override(self, person_id, day, capacity_h, reason):
        self.overrides.append((person_id, day, capacity_h, reason))

    async def add_audit(self, actor_id, action, entity_type, entity_id, payload):
        self.audits.append((action, entity_type))


def _settings() -> Settings:
    return Settings(
        database_url="x",
        redis_url="x",
        bot_token=BOT,
        team_chat_id=1,
        anthropic_api_key="x",
        openai_api_key="x",
        jwt_secret=JWT_SECRET,
        admin_ids="42",
    )


@pytest.fixture
def client():
    repo = WebFakeRepo()
    app = create_app(repo, _settings())
    c = TestClient(app)
    c.repo = repo  # type: ignore[attr-defined]
    return c


def _auth(client, is_admin=True):
    token = create_jwt(
        {"sub": str(uuid4()), "name": "Admin", "is_admin": is_admin}, JWT_SECRET
    )
    client.cookies.set(COOKIE_NAME, token)


def test_plan_requires_auth(client):
    assert client.get("/plan").status_code == 401


def test_plan_lists_projects_when_authed(client):
    _auth(client)
    r = client.get("/plan")
    assert r.status_code == 200
    assert "Альфа" in r.text


def test_audit_page_renders(client):
    _auth(client)
    r = client.get("/audit")
    assert r.status_code == 200
    assert "confirm_plan" in r.text


def test_admin_can_post_vacation(client):
    _auth(client, is_admin=True)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Айгуль", "day_from": "2026-06-10",
              "day_to": "2026-06-11", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(client.repo.overrides) == 2  # type: ignore[attr-defined]


def test_member_cannot_post_vacation(client):
    _auth(client, is_admin=False)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Айгуль", "day_from": "2026-06-10",
              "day_to": "2026-06-11", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_telegram_login_callback_sets_cookie(client):
    data = {"id": "42", "first_name": "Boss", "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(BOT.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    r = client.get("/login/telegram", params=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    assert COOKIE_NAME in r.headers.get("set-cookie", "")
