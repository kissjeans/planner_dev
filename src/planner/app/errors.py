"""Map internal exceptions to user-facing messages (spec 6.2 error boundary).

Handlers log the traceback server-side, but only this friendly text reaches the
user. Keeps UI-facing copy in one place (DRY).
"""

from __future__ import annotations

from planner.app.confirm_plan import PlanNotFoundError, PlanNotProposedError
from planner.app.set_vacation import InvalidVacationError, PersonNotFoundError

_GENERIC = "Что-то пошло не так. Попробуй переформулировать запрос."


def user_message(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "Только админ может править план."
    if isinstance(exc, PlanNotFoundError):
        return "Не нашёл такой план."
    if isinstance(exc, PlanNotProposedError):
        return "Этот план уже зафиксирован."
    if isinstance(exc, PersonNotFoundError):
        return "Не нашёл такого человека в команде."
    if isinstance(exc, InvalidVacationError):
        return str(exc)  # carries a specific Russian reason from validation
    return _GENERIC
