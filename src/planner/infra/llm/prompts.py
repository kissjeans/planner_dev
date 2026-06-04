"""Prompt templates for intent parsing and plan explanation (spec 6.2/6.3)."""

from __future__ import annotations

from planner.infra.llm.ports import ChatContext

INTENT_SYSTEM_PROMPT = """\
Ты — парсер намерений для бота-планировщика задач команды пресейла.
Преобразуй сообщение пользователя ровно в один из intent-типов:

- add_project: создать проект. Поля: title, template_code (standard|lite),
  deadline (если назван), brief_return_date, notes.
- load: показать загрузку. person_name (или пусто = вся команда), date_range.
- what_if: смоделировать изменение. operation одно из
  shift_deadline|add_person|switch_to_lite|drop_project + связанные поля.
- vacation: отпуск. person_name, day_from, day_to, capacity_h (0 = полный день).
- confirm: подтвердить последний предложенный план.
- assign: назначить задачу человеку. task_ref, person_name.
- clarify: ЕСЛИ УВЕРЕННОСТИ НЕТ — верни clarify с уточняющим вопросом.

Правила:
- Резолвь имена по таблице алиасов из контекста.
- Резолвь относительные даты («к среде», «завтра») от today из контекста, RU-локаль.
- Никогда не выдумывай deadline — если не назван, оставь null.
"""

EXPLAIN_SYSTEM_PROMPT = """\
Ты объясняешь план команде. Кратко (до 5 строк), по-русски.
Выдели перегрузы и риски срыва дедлайна. Не выдумывай цифры.
"""


def build_user_message(text: str, ctx: ChatContext) -> str:
    aliases = ", ".join(f"{a}->{c}" for a, c in ctx.aliases.items()) or "—"
    people = ", ".join(ctx.known_people) or "—"
    projects = ", ".join(ctx.known_projects) or "—"
    return (
        f"today={ctx.today.isoformat()}\n"
        f"люди: {people}\n"
        f"алиасы: {aliases}\n"
        f"проекты: {projects}\n"
        f"---\n{text}"
    )
