"""Unit tests for the regex fallback intent parser (spec section 15)."""

from datetime import date

from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import ChatContext

P = BasicIntentParser()
CTX = ChatContext(
    today=date(2026, 6, 4),
    aliases={"лёху": "Лёша"},
    known_people=("Айгуль", "Лёша", "Андрей"),
)


def test_load_with_person():
    i = P.parse_sync("/load Айгуль", CTX)
    assert i.kind == "load"
    assert i.person_name == "Айгуль"


def test_load_whole_team():
    i = P.parse_sync("load", CTX)
    assert i.kind == "load"
    assert i.person_name is None


def test_add_project_with_deadline():
    i = P.parse_sync('создать проект "Альфа", шаблон standard, дедлайн 25 июня', CTX)
    assert i.kind == "add_project"
    assert i.title == "Альфа"
    assert i.template_code == "standard"
    assert i.deadline == date(2026, 6, 25)


def test_add_project_lite_backward_mode():
    i = P.parse_sync('новый проект «Бета», шаблон lite', CTX)
    assert i.kind == "add_project"
    assert i.template_code == "lite"
    assert i.deadline is None


def test_what_if_shift_deadline():
    i = P.parse_sync('что-если сдвинуть дедлайн "Альфа" на 30 июня', CTX)
    assert i.kind == "what_if"
    assert i.operation == "shift_deadline"
    assert i.new_deadline == date(2026, 6, 30)
    assert i.project_title == "Альфа"


def test_what_if_switch_to_lite():
    i = P.parse_sync("что-если переключить на lite", CTX)
    assert i.kind == "what_if"
    assert i.operation == "switch_to_lite"


def test_vacation_range():
    i = P.parse_sync("отпуск Айгуль 10-12 июня", CTX)
    assert i.kind == "vacation"
    assert i.person_name == "Айгуль"
    assert i.day_from == date(2026, 6, 10)
    assert i.day_to == date(2026, 6, 12)


def test_alias_resolution():
    i = P.parse_sync("/load лёху", CTX)
    assert i.person_name == "Лёша"


def test_iso_date():
    i = P.parse_sync('создать проект "Гамма" дедлайн 2026-07-15', CTX)
    assert i.deadline == date(2026, 7, 15)


def test_unrecognized_is_clarify():
    i = P.parse_sync("абракадабра ничего непонятно", CTX)
    assert i.kind == "clarify"


def test_confirm():
    assert P.parse_sync("ок", CTX).kind == "confirm"
