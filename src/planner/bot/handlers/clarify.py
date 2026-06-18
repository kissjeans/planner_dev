"""Button-driven clarify for a captured task's missing key fields.

When the agent calls ``capture_task`` without исполнитель / проект / дедлайн, the
ToolBox stashes the partial args (``pending_capture``) instead of writing. The
bot then collects the missing fields deterministically via inline buttons — no
reliance on flaky free-text follow-ups — and captures once all three are known.

Project and assignee are enumerable (buttons over the roster); the deadline gets
quick relative buttons plus a "type a date" escape. Typed answers (new-project
name, custom date) use the FSM states; button picks fire on callback_data.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from planner.bot.states import CaptureClarifyState

router = Router(name="clarify")

_MAX_OPTIONS = 12  # cap roster keyboards so they stay tappable


# --- missing-field logic --------------------------------------------------

def _missing(args: dict[str, Any]) -> list[str]:
    """Key fields still absent, in the order we ask them."""
    out: list[str] = []
    if not (args.get("project") or "").strip():
        out.append("project")
    if not (args.get("assignees") or []):
        out.append("assignee")
    if not args.get("deadline"):
        out.append("deadline")
    return out


# --- keyboards ------------------------------------------------------------

def _options_kb(options: list[str], prefix: str, extra: list[InlineKeyboardButton]
                ) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=opt, callback_data=f"{prefix}{i}")]
        for i, opt in enumerate(options[:_MAX_OPTIONS])
    ]
    rows.extend([btn] for btn in extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _deadline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data="cap:dl:0"),
         InlineKeyboardButton(text="Завтра", callback_data="cap:dl:1")],
        [InlineKeyboardButton(text="+3 дня", callback_data="cap:dl:3"),
         InlineKeyboardButton(text="+неделя", callback_data="cap:dl:7")],
        [InlineKeyboardButton(text="Ввести дату", callback_data="cap:dltext")],
    ])


# --- date parsing ---------------------------------------------------------

def _parse_date(text: str | None) -> date | None:
    """Accept 2026-06-21, 21.06.2026 or 21.06 (year = current)."""
    raw = (text or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    parts = raw.split(".")
    try:
        if len(parts) == 2:
            return date(date.today().year, int(parts[1]), int(parts[0]))
        if len(parts) == 3:
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except (ValueError, IndexError):
        return None
    return None


# --- flow -----------------------------------------------------------------

async def start_capture_clarify(
    message: Message, state: FSMContext, pending: dict[str, Any], repo: Any
) -> None:
    """Entry point from the agent path: stash args, ask the first missing field."""
    await state.update_data(capture=dict(pending))
    await _send_field_keyboard(message, state, pending, repo)


async def _send_field_keyboard(
    target: Message, state: FSMContext, args: dict[str, Any], repo: Any
) -> None:
    field = _missing(args)[0]
    if field == "project":
        options = [p.title for p in await repo.list_projects()]
        await state.update_data(proj_options=options)
        await state.set_state(None)
        extra = [InlineKeyboardButton(text="➕ Новый проект", callback_data="cap:projnew")]
        await target.answer(
            "В какой проект записать задачу?",
            reply_markup=_options_kb(options, "cap:proj:", extra),
        )
    elif field == "assignee":
        options = [p.name for p in await repo.list_people()]
        await state.update_data(asg_options=options)
        await state.set_state(None)
        await target.answer(
            "Кто исполнитель?", reply_markup=_options_kb(options, "cap:asg:", []),
        )
    else:  # deadline
        await state.set_state(None)
        await target.answer("Какой дедлайн?", reply_markup=_deadline_kb())


async def _after_fill(
    target: Message, state: FSMContext, args: dict[str, Any],
    repo: Any, task_sink: Any, actor_record: Any, actor: dict[str, Any] | None,
) -> None:
    await state.update_data(capture=args)
    if _missing(args):
        await _send_field_keyboard(target, state, args, repo)
    else:
        await _finalize(target, state, args, repo, task_sink, actor_record, actor)


async def _finalize(
    target: Message, state: FSMContext, args: dict[str, Any],
    repo: Any, task_sink: Any, actor_record: Any, actor: dict[str, Any] | None,
) -> None:
    await state.clear()
    if not (actor or {}).get("is_admin"):
        await target.answer("Только админ может ставить задачи.")
        return
    # Lazy imports break the task_router <-> clarify cycle.
    from planner.bot.handlers.task_router import build_capture_reply
    from planner.domain.intent import CaptureTaskIntent

    intent = CaptureTaskIntent(
        task_title=args["title"],
        assignee_names=list(args.get("assignees") or []),
        project_name=args.get("project"),
        deadline=date.fromisoformat(args["deadline"]) if args.get("deadline") else None,
        est_hours=args.get("est_hours"),
        required_skills=list(args.get("required_skills") or []),
    )
    text, _ = await build_capture_reply(
        intent, repo=repo, actor_record=actor_record, task_sink=task_sink
    )
    await target.answer(text)


# --- callbacks ------------------------------------------------------------

async def _pick_from_options(
    cb: CallbackQuery, state: FSMContext, *, opts_key: str, field: str,
    as_list: bool, repo: Any, task_sink: Any, actor_record: Any, actor: dict[str, Any] | None,
) -> None:
    assert cb.data is not None
    idx = int(cb.data.rsplit(":", 1)[1])
    data = await state.get_data()
    options = data.get(opts_key) or []
    if idx >= len(options):
        await cb.answer("Вариант не найден.", show_alert=True)
        return
    args = dict(data.get("capture") or {})
    args[field] = [options[idx]] if as_list else options[idx]
    await cb.answer()
    await _after_fill(
        cast(Message, cb.message), state, args, repo, task_sink, actor_record, actor
    )


@router.callback_query(F.data.startswith("cap:proj:"))
async def handle_pick_project(
    cb: CallbackQuery, state: FSMContext, repo: Any = None,
    task_sink: Any = None, actor_record: Any = None, actor: dict[str, Any] | None = None,
) -> None:
    await _pick_from_options(
        cb, state, opts_key="proj_options", field="project", as_list=False,
        repo=repo, task_sink=task_sink, actor_record=actor_record, actor=actor,
    )


@router.callback_query(F.data.startswith("cap:asg:"))
async def handle_pick_assignee(
    cb: CallbackQuery, state: FSMContext, repo: Any = None,
    task_sink: Any = None, actor_record: Any = None, actor: dict[str, Any] | None = None,
) -> None:
    await _pick_from_options(
        cb, state, opts_key="asg_options", field="assignees", as_list=True,
        repo=repo, task_sink=task_sink, actor_record=actor_record, actor=actor,
    )


@router.callback_query(F.data == "cap:projnew")
async def handle_new_project(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CaptureClarifyState.waiting_project_name)
    await cb.answer()
    await cast(Message, cb.message).answer("Напиши название нового проекта одним сообщением.")


@router.callback_query(F.data.startswith("cap:dl:"))
async def handle_pick_deadline(
    cb: CallbackQuery, state: FSMContext, repo: Any = None,
    task_sink: Any = None, actor_record: Any = None, actor: dict[str, Any] | None = None,
) -> None:
    assert cb.data is not None
    days = int(cb.data.rsplit(":", 1)[1])
    data = await state.get_data()
    args = dict(data.get("capture") or {})
    args["deadline"] = (date.today() + timedelta(days=days)).isoformat()
    await cb.answer()
    await _after_fill(
        cast(Message, cb.message), state, args, repo, task_sink, actor_record, actor
    )


@router.callback_query(F.data == "cap:dltext")
async def handle_deadline_text(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CaptureClarifyState.waiting_deadline_text)
    await cb.answer()
    await cast(Message, cb.message).answer("Напиши дату: 2026-06-21 или 21.06.2026.")


# --- typed-answer re-entry (state-filtered, preempts the catch-all) --------

@router.message(StateFilter(CaptureClarifyState.waiting_project_name), F.text)
async def handle_typed_project(
    message: Message, state: FSMContext, repo: Any = None,
    task_sink: Any = None, actor_record: Any = None, actor: dict[str, Any] | None = None,
) -> None:
    data = await state.get_data()
    args = dict(data.get("capture") or {})
    args["project"] = (message.text or "").strip()
    await _after_fill(message, state, args, repo, task_sink, actor_record, actor)


@router.message(StateFilter(CaptureClarifyState.waiting_deadline_text), F.text)
async def handle_typed_deadline(
    message: Message, state: FSMContext, repo: Any = None,
    task_sink: Any = None, actor_record: Any = None, actor: dict[str, Any] | None = None,
) -> None:
    parsed = _parse_date(message.text)
    if parsed is None:
        await message.answer("Не понял дату. Формат: 2026-06-21 или 21.06.2026.")
        return
    data = await state.get_data()
    args = dict(data.get("capture") or {})
    args["deadline"] = parsed.isoformat()
    await _after_fill(message, state, args, repo, task_sink, actor_record, actor)
