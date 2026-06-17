"""End-to-end web admin tests via FastAPI TestClient + fake repo (spec 9 / 5.8)."""

import hashlib
import hmac
import time
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from planner.app.ports import (
    AuditRecord,
    PersonRecord,
    PlanVersionRecord,
    ProjectRecord,
    TaskMeta,
    TaskRecord,
)
from planner.settings import Settings
from planner.web.app import create_app
from planner.web.auth import COOKIE_NAME, create_jwt

BOT = "123456:TEST-TOKEN"
JWT_SECRET = "test-secret"

_PROJECT_ID = uuid4()
_TASK_ID = uuid4()


class WebFakeRepo:
    def __init__(self) -> None:
        self.overrides: list = []
        self.audits: list = []
        self.task_updates: list = []
        self.reassigns: list = []
        self.people = {"Айгуль": PersonRecord(
            id=uuid4(), name="Айгуль", role_label="Аналитик")}

    async def list_projects(self):
        return [ProjectRecord(_PROJECT_ID, "Альфа", "planning", None,
                              priority="high", template_code="standard",
                              start_date=date(2026, 6, 1))]

    async def list_tasks_with_meta(self):
        person = self.people["Айгуль"]
        return [TaskMeta(
            task_id=_TASK_ID, task_name="Бриф", project_title="Альфа",
            priority="high", status="not_done",
            start_date=date.today(), end_date=date.today(), duration_hours=8,
            assignee_id=person.id, assignee_name="Айгуль", deadline=date.today(),
        )]

    async def set_task_assignee(self, task_id, person_id, hours=8):
        self.reassigns.append((task_id, person_id))
        return True

    async def get_task_name_map(self):
        return {_TASK_ID: "Бриф"}

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
        self.audits.append((actor_id, action, entity_type))

    async def list_project_tasks(self, project_id):
        return [TaskRecord(id=_TASK_ID, name="Бриф", status="open",
                           start_date=date(2026, 6, 8), end_date=date(2026, 6, 8),
                           duration_hours=8)]

    async def update_task_schedule(self, task_id, start, end, person_id):
        self.task_updates.append((task_id, start, end))

    async def get_committed_plan(self, project_id):
        if project_id != _PROJECT_ID:
            return None
        person = self.people["Айгуль"]
        payload = {
            "assignments": [
                {
                    "task_id": str(_TASK_ID),
                    "person_id": str(person.id),
                    "start_date": "2026-06-08",
                    "end_date": "2026-06-09",
                    "allocations": [
                        {"person_id": str(person.id), "day": "2026-06-08", "hours": 8},
                        {"person_id": str(person.id), "day": "2026-06-09", "hours": 8},
                    ],
                }
            ],
            "risks": [],
            "end_date": "2026-06-09",
        }
        return PlanVersionRecord(uuid4(), _PROJECT_ID, "committed", payload)


def _settings() -> Settings:
    return Settings(
        database_url="x",
        redis_url="x",
        bot_token=BOT,
        team_chat_id=1,
        anthropic_api_key="x",
        jwt_secret=JWT_SECRET,
        admin_ids="42",
        debug=False,
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


def test_root_redirects_to_plan(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"


def test_plan_detail_renders_tasks(client):
    _auth(client)
    r = client.get(f"/plan/{_PROJECT_ID}")
    assert r.status_code == 200
    assert "Бриф" in r.text


def test_edit_task_redirects_and_records_update(client):
    _auth(client, is_admin=True)
    r = client.post(
        f"/plan/{_PROJECT_ID}/task/{_TASK_ID}/edit",
        data={"start": "2026-06-10", "end": "2026-06-12"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(client.repo.task_updates) == 1  # type: ignore[attr-defined]
    assert client.repo.task_updates[0][1] == date(2026, 6, 10)  # type: ignore[attr-defined]


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Admin board: Schedule / Calendar / Load (client xlsx vision)
# ---------------------------------------------------------------------------

def test_schedule_page_lists_tasks(client):
    _auth(client)
    r = client.get("/schedule")
    assert r.status_code == 200
    assert "Расписание" in r.text
    assert "Бриф" in r.text  # task from committed plan


def test_calendar_page_shows_person_tasks_by_date(client):
    _auth(client)
    r = client.get("/calendar")
    assert r.status_code == 200
    assert "Айгуль" in r.text   # person row
    assert "Бриф" in r.text     # task in a day cell


def test_load_board_shows_slots(client):
    _auth(client)
    r = client.get("/load-board")
    assert r.status_code == 200
    assert "Итого слотов" in r.text


def test_board_pages_require_auth(client):
    assert client.get("/schedule").status_code == 401
    assert client.get("/calendar").status_code == 401
    assert client.get("/load-board").status_code == 401


def test_reassign_admin_moves_task(client):
    _auth(client, is_admin=True)
    pid = client.repo.people["Айгуль"].id  # type: ignore[attr-defined]
    r = client.post(
        "/schedule/reassign",
        data={"task_id": str(_TASK_ID), "person_id": str(pid)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(client.repo.reassigns) == 1  # type: ignore[attr-defined]


def test_reassign_empty_person_is_noop(client):
    _auth(client, is_admin=True)
    r = client.post(
        "/schedule/reassign",
        data={"task_id": str(_TASK_ID), "person_id": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.repo.reassigns == []  # type: ignore[attr-defined]


def test_reassign_blocked_for_member(client):
    _auth(client, is_admin=False)
    pid = client.repo.people["Айгуль"].id  # type: ignore[attr-defined]
    r = client.post(
        "/schedule/reassign",
        data={"task_id": str(_TASK_ID), "person_id": str(pid)},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_dev_login_disabled_without_debug():
    # debug off → 404 even from loopback
    app = create_app(WebFakeRepo(), _settings())  # _settings() has debug=False
    c = TestClient(app, client=("127.0.0.1", 5000))
    r = c.get("/dev-login", follow_redirects=False)
    assert r.status_code == 404


def test_dev_login_mints_admin_session_from_loopback():
    settings = _settings().model_copy(update={"debug": True})
    app = create_app(WebFakeRepo(), settings)
    c = TestClient(app, client=("127.0.0.1", 5000))
    r = c.get("/dev-login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    assert c.cookies.get(COOKIE_NAME)


def test_dev_login_rejected_from_non_loopback_even_with_debug():
    settings = _settings().model_copy(update={"debug": True})
    app = create_app(WebFakeRepo(), settings)
    c = TestClient(app, client=("10.0.0.5", 5000))  # LAN peer
    r = c.get("/dev-login", follow_redirects=False)
    assert r.status_code == 404


def test_logout_clears_cookie(client):
    _auth(client)
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert "planner_jwt" not in r.cookies or r.cookies.get("planner_jwt") == ""


def test_team_list_renders(client):
    _auth(client)
    r = client.get("/team")
    assert r.status_code == 200
    assert "Айгуль" in r.text


def test_vacation_unknown_person_returns_404(client):
    """Setting vacation for someone not in the team is an error, not a silent ok."""
    _auth(client, is_admin=True)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Призрак", "day_from": "2026-06-10",
              "day_to": "2026-06-10", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 404
    # Friendly copy, not a raw 500 / stack trace.
    assert "Не нашёл такого человека" in r.text


def _error_client(exc_type):
    """App fixture whose repo raises exc_type on list_project_tasks."""

    class _ErrorRepo(WebFakeRepo):
        async def list_project_tasks(self, project_id):
            raise exc_type("test")

        async def update_task_schedule(self, *a):
            raise exc_type("test")

    app = create_app(_ErrorRepo(), _settings())
    return TestClient(app)


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_gantt_png_requires_auth(client):
    assert client.get(f"/plan/{_PROJECT_ID}/gantt.png").status_code == 401


def test_gantt_png_returns_image_when_authed(client):
    _auth(client)
    r = client.get(f"/plan/{_PROJECT_ID}/gantt.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(_PNG_MAGIC)


def test_gantt_png_404_when_no_committed_plan(client):
    _auth(client)
    r = client.get(f"/plan/{uuid4()}/gantt.png")
    assert r.status_code == 404


def test_app_plan_not_found_returns_404():
    from planner.app.confirm_plan import PlanNotFoundError
    c = _error_client(PlanNotFoundError)
    _auth(c)
    r = c.get(f"/plan/{_PROJECT_ID}")
    assert r.status_code == 404


def test_app_plan_not_proposed_returns_409():
    from planner.app.confirm_plan import PlanNotProposedError
    c = _error_client(PlanNotProposedError)
    _auth(c, is_admin=True)
    r = c.post(f"/plan/{_PROJECT_ID}/task/{_TASK_ID}/edit",
               data={"start": "2026-06-10", "end": "2026-06-12"})
    assert r.status_code == 409


def test_app_permission_error_returns_403_via_handler():
    c = _error_client(PermissionError)
    _auth(c, is_admin=True)
    r = c.post(f"/plan/{_PROJECT_ID}/task/{_TASK_ID}/edit",
               data={"start": "2026-06-10", "end": "2026-06-12"})
    assert r.status_code == 403


def test_telegram_login_invalid_signature_returns_401(client):
    r = client.get("/login/telegram", params={"id": "42", "auth_date": "1", "hash": "bad"})
    assert r.status_code == 401


def test_vacation_non_uuid_sub_records_null_actor(client):
    """A dev-login / admin-without-Person (sub not a real Person UUID) must be
    able to record a vacation; the audit actor_id is NULL, never a forged uuid."""
    token = create_jwt(
        {"sub": "dev", "name": "Admin", "is_admin": True}, JWT_SECRET
    )
    client.cookies.set(COOKIE_NAME, token)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Айгуль", "day_from": "2026-06-10",
              "day_to": "2026-06-10", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    recorded_actor = client.repo.audits[0][0]  # type: ignore[attr-defined]
    assert recorded_actor is None


def test_telegram_login_callback_sets_cookie(client):
    data = {"id": "42", "first_name": "Boss", "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(BOT.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    r = client.get("/login/telegram", params=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan"
    assert COOKIE_NAME in r.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# Plan 005: input validation — 400/422 for malformed admin input
# ---------------------------------------------------------------------------

def test_edit_task_bad_date_returns_400(client):
    _auth(client, is_admin=True)
    r = client.post(
        f"/plan/{_PROJECT_ID}/task/{_TASK_ID}/edit",
        data={"start": "not-a-date", "end": ""},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_vacation_bad_date_returns_400(client):
    _auth(client, is_admin=True)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Айгуль", "day_from": "32.13.2026",
              "day_to": "2026-06-11", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_reassign_non_uuid_returns_400(client):
    _auth(client, is_admin=True)
    r = client.post(
        "/schedule/reassign",
        data={"task_id": "not-a-uuid", "person_id": "also-bad"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_audit_negative_offset_returns_422(client):
    _auth(client)
    assert client.get("/audit?offset=-1").status_code == 422
    assert client.get("/audit?limit=-1").status_code == 422


def test_reassign_records_actor_id(client):
    from uuid import UUID
    sub = str(uuid4())
    token = create_jwt({"sub": sub, "name": "Admin", "is_admin": True}, JWT_SECRET)
    client.cookies.set(COOKIE_NAME, token)
    pid = client.repo.people["Айгуль"].id  # type: ignore[attr-defined]
    r = client.post(
        "/schedule/reassign",
        data={"task_id": str(_TASK_ID), "person_id": str(pid)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    recorded_actor = client.repo.audits[0][0]  # type: ignore[attr-defined]
    assert recorded_actor == UUID(sub)
