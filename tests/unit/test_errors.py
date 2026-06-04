"""Unit tests for the user-facing error mapping (spec 6.2)."""

from planner.app.confirm_plan import PlanNotFoundError, PlanNotProposedError
from planner.app.errors import user_message
from planner.app.set_vacation import PersonNotFoundError


def test_permission_error_message():
    assert "админ" in user_message(PermissionError())


def test_plan_not_found_message():
    assert user_message(PlanNotFoundError()) == "Не нашёл такой план."


def test_plan_not_proposed_message():
    assert "зафиксиров" in user_message(PlanNotProposedError())


def test_person_not_found_message():
    assert "команде" in user_message(PersonNotFoundError())


def test_unknown_exception_is_generic():
    assert user_message(ValueError("boom")) == (
        "Что-то пошло не так. Попробуй переформулировать запрос."
    )
