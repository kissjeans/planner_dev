"""Unit tests for the regex fallback intent parser (spec section 15)."""

from datetime import date

import pytest

from planner.infra.llm.basic import BasicIntentParser, _parse_date
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


def test_unrecognized_is_captured_as_task():
    """Non-command text is captured as a task (low-friction path), not clarify."""
    i = P.parse_sync("подготовить бриф по мтс, Андрей задача твоя", CTX)
    assert i.kind == "capture_task"
    assert i.task_title == "подготовить бриф по мтс, Андрей задача твоя"
    assert i.assignee_names == ["Андрей"]  # resolved from known_people


def test_empty_text_is_clarify():
    i = P.parse_sync("   ", CTX)
    assert i.kind == "clarify"


def test_confirm():
    assert P.parse_sync("ок", CTX).kind == "confirm"


def test_task_query_keywords():
    assert P.parse_sync("какие задачи сейчас в работе", CTX).kind == "load"
    assert P.parse_sync("что сейчас идёт", CTX).kind == "load"
    assert P.parse_sync("текущий статус", CTX).kind == "load"


def test_dm_date_format():
    i = P.parse_sync('создать проект "Дельта" дедлайн 20 июня', CTX)
    assert i.deadline == date(2026, 6, 20)


def test_unknown_month_returns_clarify():
    # "зелёного" starts with "з" — no Russian month stem matches
    i = P.parse_sync("отпуск Айгуль 10-12 зелёного", CTX)
    assert i.kind == "clarify"


def test_what_if_drop_project():
    i = P.parse_sync('что-если удали проект "Альфа"', CTX)
    assert i.kind == "what_if"
    assert i.operation == "drop_project"


def test_what_if_add_person():
    i = P.parse_sync("что-если +1 человек", CTX)
    assert i.kind == "what_if"
    assert i.operation == "add_person"


def test_parse_date_ddmm_no_year():
    """_DDMM path lines 41-42: '20.06' without year uses today.year."""
    result = _parse_date("дедлайн 20.06", date(2026, 6, 4))
    assert result == date(2026, 6, 20)


def test_parse_date_ddmm_with_year():
    """_DDMM path with explicit year: '20.06.2027'."""
    result = _parse_date("дедлайн 20.06.2027", date(2026, 6, 4))
    assert result == date(2027, 6, 20)


def test_parse_date_dm_format():
    """_DM path: '20 июня' format."""
    result = _parse_date("дедлайн 20 июня", date(2026, 6, 4))
    assert result == date(2026, 6, 20)


def test_parse_date_dm_unknown_month_returns_none():
    """_DM matches but _month_num returns None → returns None."""
    result = _parse_date("срок 15 зелёного", date(2026, 6, 4))
    assert result is None


def test_parse_date_out_of_range_ddmm_returns_none():
    """'32.13' is out of range — must return None, not raise ValueError."""
    assert _parse_date("дедлайн 32.13", date(2026, 6, 4)) is None


def test_parse_date_out_of_range_iso_returns_none():
    assert _parse_date("2026-99-99", date(2026, 6, 4)) is None


def test_capture_with_bad_date_does_not_crash():
    """A task-like message with a bad date is still captured (no crash)."""
    i = P.parse_sync("сделать отчёт к 99.99", CTX)
    assert i.kind == "capture_task"
    assert i.deadline is None


def test_vacation_out_of_range_returns_clarify():
    i = P.parse_sync("отпуск Айгуль 40-50 июня", CTX)
    assert i.kind == "clarify"


def test_delete_project_not_classified_as_create():
    i = P.parse_sync('удали проект «Альфа»', CTX)
    assert i.kind == "what_if"
    assert i.operation == "drop_project"
    assert i.project_title == "Альфа"


def test_remove_project_phrasing_drops():
    i = P.parse_sync('убери проект "Бета"', CTX)
    assert i.kind == "what_if"
    assert i.operation == "drop_project"


def test_create_project_still_works():
    """Guard must not break the normal create path."""
    i = P.parse_sync('создать проект "Гамма", шаблон standard', CTX)
    assert i.kind == "add_project"
    assert i.title == "Гамма"


@pytest.mark.asyncio
async def test_availability_question_is_load():
    from datetime import date

    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    ctx = ChatContext(today=date(2026, 6, 5), known_people=("Рай",))
    out = await BasicIntentParser().parse("сколько слотов у Рая?", ctx)
    assert out.kind == "load"


@pytest.mark.asyncio
async def test_plain_question_not_captured_as_task():
    from datetime import date

    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    ctx = ChatContext(today=date(2026, 6, 5))
    out = await BasicIntentParser().parse("ты изменила загрузку?", ctx)
    assert out.kind in ("load", "clarify")  # never a captured task


@pytest.mark.asyncio
async def test_imperative_still_captured_as_task():
    """Regression: real task-like messages still capture."""
    from datetime import date

    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    ctx = ChatContext(today=date(2026, 6, 5))
    out = await BasicIntentParser().parse("подготовить бриф по МТС", ctx)
    assert out.kind == "capture_task"


@pytest.mark.asyncio
async def test_basic_capture_assignee_is_list():
    from datetime import date

    from planner.infra.llm.basic import BasicIntentParser
    from planner.infra.llm.ports import ChatContext
    ctx = ChatContext(today=date(2026, 6, 5), known_people=("Андрей",))
    out = await BasicIntentParser().parse("подготовить бриф, Андрей задача твоя", ctx)
    assert out.kind == "capture_task"
    assert out.assignee_names == ["Андрей"]


@pytest.mark.asyncio
async def test_parse_intents_returns_one_element_list():
    """Regex fallback stays single-action: parse_intents wraps parse_sync."""
    out = await P.parse_intents("/load Айгуль", CTX)
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0].kind == "load"
    assert out[0].person_name == "Айгуль"
